"""
python utils_learning.py -r cr -s tactip_331 -m simple_cnn -t edge_2d
"""
import os
import pickle
import itertools as it
import numpy as np
import pandas as pd
import torch
from torch.autograd import Variable

from tactile_data.tactile_servo_control import BASE_MODEL_PATH
from tactile_data.utils_data import load_json_obj
from tactile_image_processing.image_transforms import process_image
from tactile_learning.utils.utils_plots import LearningPlotter

from tactile_servo_control.learning.utils_plots import RegressErrorPlotter
from tactile_servo_control.utils.parse_args import parse_args


class LabelEncoder:

    def __init__(self, task_params, device='cuda'):
        self.device = device
        self.label_names = task_params['label_names']
        self.target_label_names = task_params['target_label_names'].copy()
        num_targets = len(self.target_label_names)

        # optional arguments
        self.periodic_label_names = task_params.get('periodic_label_names', [])
        self.target_weights = task_params.get('target_weights', np.ones(num_targets))
        self.tolerences = task_params.get('tolerences', np.ones(num_targets))

        # create tensors for pose limits
        self.llims_np = np.array(task_params['llims'])
        self.ulims_np = np.array(task_params['ulims'])
        self.llims_torch = torch.from_numpy(self.llims_np).float().to(self.device)
        self.ulims_torch = torch.from_numpy(self.ulims_np).float().to(self.device)

    @property
    def out_dim(self):
        periodic_dims = [self.target_label_names.count(p) for p in self.periodic_label_names]
        return len(self.target_label_names) + sum(periodic_dims)

    def encode_norm(self, target, label_name):
        llim = self.llims_torch[self.label_names.index(label_name)]
        ulim = self.ulims_torch[self.label_names.index(label_name)]
        norm_target = (((target - llim) / (ulim - llim)) * 2) - 1
        return norm_target.unsqueeze(dim=1)

    def decode_norm(self, prediction, label_name):
        llim = self.llims_np[self.label_names.index(label_name)]
        ulim = self.ulims_np[self.label_names.index(label_name)]
        return (((prediction + 1) / 2) * (ulim - llim)) + llim

    def encode_circnorm(self, target):
        ang = target * np.pi/180
        return [torch.sin(ang).float().to(self.device).unsqueeze(dim=1),
                torch.cos(ang).float().to(self.device).unsqueeze(dim=1)]

    def decode_circnorm(self, vec_prediction):
        pred_rot = torch.atan2(*vec_prediction)
        return pred_rot * 180/np.pi

    def encode_label(self, labels_dict):
        """
        Process raw pose data to NN friendly label for prediction.
        Default: maps to weight * range(-1,+1)
        Periodic: maps to weight * [cos angle, sin angle]
        """

        # encode pose to predictable label
        encoded_pose = []
        for label_name, weight in zip(self.target_label_names, self.target_weights):

            # get the target from the dict
            target = labels_dict[label_name].float().to(self.device)

            # normalize pose label within limits
            if not label_name in self.periodic_label_names:
                encoded_pose.append(weight * self.encode_norm(target, label_name))

            # if periodic use sine/cosine encoding of angle
            if label_name in self.periodic_label_names:
                encoded_pose.extend(weight * self.encode_circnorm(target))

        return torch.cat(encoded_pose, 1)

    def decode_label(self, outputs):
        """
        Process NN predictions to raw pose data, always decodes to cpu.
        Inverse of encode
        """

        # decode predicted label to pose
        decoded_pose = {label: torch.zeros(outputs.shape[0]) for label in self.label_names}

        ind = 0
        for label_name, weight in zip(self.target_label_names, self.target_weights):

            if label_name not in self.periodic_label_names:
                prediction = outputs[:, ind].detach().cpu() / weight
                decoded_pose[label_name] = self.decode_norm(prediction, label_name)
                ind += 1

            elif label_name in self.periodic_label_names:
                vec_prediction = [outputs[:, ind].detach().cpu() / weight,
                                  outputs[:, ind+1].detach().cpu() / weight]
                decoded_pose[label_name] = self.decode_circnorm(vec_prediction)
                ind += 2

        return decoded_pose

    def calc_batch_metrics(self, labels, predictions):
        """
        Calculate metrics useful for measuring progress throughout training.

        Returns: dict of metrics
            {
                'metric': np.array()
            }
        """
        err_df = self.err_metric(labels, predictions)
        acc_df = self.acc_metric(err_df)
        return err_df, acc_df

    def err_metric(self, labels, predictions):
        """
        Error metric for regression problem, returns dict of errors.
        """
        err_df = pd.DataFrame(columns=self.label_names)
        for label_name in self.target_label_names:

            if label_name not in self.periodic_label_names:
                abs_err = torch.abs(
                    labels[label_name] - predictions[label_name]
                ).detach().cpu().numpy()

            elif label_name in self.periodic_label_names:
                targ_rot = labels[label_name] * np.pi/180
                pred_rot = predictions[label_name] * np.pi/180

                # Calculate angle difference, taking into account periodicity (thanks ChatGPT)
                abs_err = torch.abs(
                    torch.atan2(torch.sin(targ_rot - pred_rot), torch.cos(targ_rot - pred_rot))
                ).detach().cpu().numpy() * 180/np.pi

            err_df[label_name] = abs_err

        return err_df

    def acc_metric(self, err_df):
        """
        Accuracy metric for regression problem, counting the number of predictions within a tolerance.
        """

        batch_size = err_df.shape[0]
        acc_df = pd.DataFrame(columns=[*self.label_names, 'overall_acc'])
        overall_correct = np.ones(batch_size, dtype=bool)

        for label_name, tolerence in zip(self.target_label_names, self.tolerences):
            abs_err = err_df[label_name]
            correct = (abs_err < tolerence)

            overall_correct = overall_correct & correct
            acc_df[label_name] = correct.astype(np.float32)

        # count where all predictions are correct for overall accuracy
        acc_df['overall_acc'] = overall_correct.astype(np.float32)

        return acc_df


class LabelledModel:
    def __init__(self,
                 model,
                 image_processing_params,
                 label_encoder,
                 device='cuda'
                 ):
        self.model = model
        self.image_processing_params = image_processing_params
        self.label_encoder = label_encoder
        self.label_names = label_encoder.label_names
        self.target_label_names = label_encoder.target_label_names
        self.device = device

    def predict(self, tactile_image):

        processed_image = process_image(
            tactile_image,
            gray=False,
            **self.image_processing_params
        )

        # channel first for pytorch; add batch dim
        processed_image = np.rollaxis(processed_image, 2, 0)
        processed_image = processed_image[np.newaxis, ...]

        # perform inference with the trained model
        model_input = Variable(torch.from_numpy(processed_image)).float().to(self.device)
        outputs = self.model(model_input)

        # decode the prediction
        predictions_dict = self.label_encoder.decode_label(outputs)

        # pack into array and report
        print("\nPredictions: ", end="")
        predictions_arr = np.zeros(len(self.label_names))
        for label_name in self.target_label_names:
            predicted_val = predictions_dict[label_name].detach().cpu().numpy()
            predictions_arr[self.label_names.index(label_name)] = predicted_val
            with np.printoptions(precision=2, suppress=True):
                print(label_name, predicted_val, end=" ")

        return predictions_arr


if __name__ == '__main__':

    args = parse_args(
        robot='sim',
        sensor='tactip',
        tasks=['edge_2d'],
        models=['simple_cnn'],
        version=[''],
        device='cuda'
    )

    for args.task, args.model in it.product(args.tasks, args.models):

        output_dir = '_'.join([args.robot, args.sensor])
        model_dir_name = '_'.join(filter(None, [args.model, *args.version]))

        # set save dir
        save_dir = os.path.join(BASE_MODEL_PATH, output_dir, args.task, model_dir_name)

        # create task params
        task_params = load_json_obj(os.path.join(save_dir, 'task_params'))

        # load and plot predictions
        with open(os.path.join(save_dir, 'val_pred_targ_err.pkl'), 'rb') as f:
            pred_df, targ_df, err_df, label_names = pickle.load(f)

        error_plotter = RegressErrorPlotter(task_params, save_dir, 'error_plot_best.png')
        error_plotter.final_plot(pred_df, targ_df, err_df)

        # load and plot training
        with open(os.path.join(save_dir, 'train_val_loss_acc.pkl'), 'rb') as f:
            train_loss, val_loss, train_acc, val_acc = pickle.load(f)

        learning_plotter = LearningPlotter(save_dir=save_dir)
        learning_plotter.final_plot(train_loss, val_loss, train_acc, val_acc)
