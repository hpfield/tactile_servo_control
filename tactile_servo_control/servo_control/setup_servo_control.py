# -*- coding: utf-8 -*-
import os
import pandas as pd

from tactile_learning.utils.utils_learning import save_json_obj
from cri.transforms import transform_euler


def setup_control_params(task, save_dir=None):

    if task == 'surface_3d':
        control_params = {
            'ep_len': 500,
            'pid_params': {
                'kp': [1, 1, 0.5, 0.5, 0.5, 1],                 
                'ki': [0, 0, 0.3, 0.1, 0.1, 0],                
                'ei_clip': [[0, 0, 0, -30, -30, 0], [0, 0, 5, 30, 30, 0]],        
                'error': 'lambda y, r: transform_euler(r, y)'  # SE(3) error
            },
            'ref_pose': [0, -1, 3, 0, 0, 0]              
        }

    elif task == 'edge_2d':
        control_params = {
            'ep_len': 50,
            'pid_params': {
                'kp': [0.5, 1, 0, 0, 0, 0.5],                 
                'ki': [0.3, 0, 0, 0, 0, 0.1],                
                'ei_clip': [[-5, 0, 0, 0, 0, -45], [5, 0, 0, 0, 0,  45]],          
                'error': 'lambda y, r: transform_euler(r, y)'  # SE(3) error
            },
            'ref_pose': [0, 1, 0, 0, 0, 0]              
        }

    elif task == 'edge_3d':
        control_params = {
            'ep_len': 400,
            'pid_params': {
                'kp': [0.5, 1, 0.5, 0, 0, 0.5],                 
                'ki': [0.3, 0, 0.3, 0, 0, 0.1],                
                'ei_clip': [[0, -5, 0, 0, 0, -45], [0, 5, 5, 0, 0, 45]],
                'error': 'lambda y, r: transform_euler(r, y)'  # SE(3) error
            },
            'ref_pose': [0, 1, 3, 0, 0, 0]             
        }

    elif task == 'edge_5d':
        control_params = {
            'ep_len': 250,
            'pid_params': {
                'kp': [1, 0.5, 0.5, 0.5, 0.5, 0.5],                
                'ki': [0, 0.3, 0.3, 0.1, 0.1, 0.1],
                'ei_clip': [[0, -5, 0, -30, -30, -45], [0, 5, 5, 30, 30, 45]],
                'error': 'lambda y, r: transform_euler(r, y)'  # SE(3) error
            },
            'ref_pose': [1, 0, 3, 0, 0, 0]              
        }

    if save_dir:
        save_json_obj(control_params, os.path.join(save_dir, 'control_params'))

    # convert error into function handle if exists
    if 'error' in control_params['pid_params']:
        control_params['pid_params']['error'] = eval(control_params['pid_params']['error'])

    return control_params


def setup_env_params(robot, task, stimulus, save_dir=None):

    if robot == 'Sim':
        env_params = {
            'robot': 'Sim',
            'tcp_pose': (0, 0, 0, 0, 0, 0),
            'stim_name': stimulus,
            'stim_pose': (600, 0, 0, 0, 0, 0),
            'show_gui': True, 
            'show_tactile': True, 
            'quick_mode': False,
            'model_type': 'simple_cnn'
        }
    
    else:
        env_params = {
            'robot': 'CR',
            'tcp_pose': (0, 0, -100, 0, 0, 0),
            'stim_name': stimulus,
            'speed': 20, 
            'model_type': 'posenet_cnn',
            # 'servo_delay': 0.1
        }

    work_frame_df = pd.DataFrame(
        columns = ['robot', 'task',    'stimulus', 'work_frame'],
        data = [
                #   ['CR',   'edge',    'circle',   (0,  -370,  65, -180, 0, 180)],
                  ['CR',    'edge',    'circle',   (0,  -470,  65, -180, 0, 0)],
                  ['Sim',   'edge',    'circle',   (650,   0, 40-3, -180, 0, 0)],
                  ['Sim',   'edge',    'saddle',   (600, -65, 55-3, -180, 0, 0)],
                  ['Sim',   'surface', 'saddle',   (600, -65, 55-3, -180, 0, 0)],
                  ['Sim',   'edge',    'bowl',     (600,   0,   25, -180, 0, 0)],
        ]
    )

    query_str = f"robot=='{env_params['robot']}' & task=='{task[:-3]}' & stimulus=='{stimulus}'"
    env_params['work_frame'] = work_frame_df.query(query_str)['work_frame'].iloc[0]

    if save_dir:
        save_json_obj(env_params, os.path.join(save_dir, 'env_params'))

    return env_params


if __name__ == '__main__':
    pass