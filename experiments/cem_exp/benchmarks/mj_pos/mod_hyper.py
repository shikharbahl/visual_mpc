import os
import python_visual_mpc
current_dir = '/'.join(str.split(__file__, '/')[:-1])
bench_dir = '/'.join(str.split(__file__, '/')[:-2])

from python_visual_mpc.visual_mpc_core.algorithm.cem_controller import CEM_controller

ROOT_DIR = os.path.abspath(python_visual_mpc.__file__)
ROOT_DIR = '/'.join(str.split(ROOT_DIR, '/')[:-2])

from python_visual_mpc.visual_mpc_core.agent.agent_mjc import AgentMuJoCo
import numpy as np

agent = {
    'type': AgentMuJoCo,
    'T': 30,
    'substeps':50,
    'make_final_gif':'',
    'adim':5,
    'sdim':12,
    # 'no_instant_gif':"",
    'filename': ROOT_DIR + '/mjc_models/cartgripper_grasp.xml',
    'filename_nomarkers': ROOT_DIR + '/mjc_models/cartgripper_grasp.xml',
    'gen_xml':1,   #generate xml every nth trajecotry
    'num_objects': 1,
    'viewer_image_height' : 480,
    'viewer_image_width' : 640,
    'image_height':48,
    'image_width':64,
    'additional_viewer':'',
    'data_save_dir':current_dir + '/data/train',
    'posmode':"",
    'targetpos_clip':[[-0.45, -0.45, -0.08, -np.pi*2, 0.], [0.45, 0.45, 0.15, np.pi*2, 0.1]],
    'mode_rel':np.array([True, True, True, True, False]),
    'not_use_images':"",
}

policy = {
    # 'verbose':10,
    'type' : CEM_controller,
    'current_dir':current_dir,
    'nactions': 5,
    'repeat': 3,
    'initial_std': 0.08,        # std dev. in xy
    'initial_std_lift': 0.1,
    'initial_std_rot': 0.1,
    'initial_std_grasp': 0.0,
    'iterations': 2,
    'action_cost_factor': 0,
    'rew_all_steps':"",
    'finalweight':10,
    'no_action_bound':"",
    'num_samples':50,
}

tag_images = {'name': 'images',
             'file':'/images/im{}.png',   # only tindex
             'shape':[agent['image_height'],agent['image_width'],3],
               }

tag_qpos = {'name': 'qpos',
             'shape':[3],
             'file':'/state_action.pkl'}
tag_object_full_pose = {'name': 'object_full_pose',
                         'shape':[4,7],
                         'file':'/state_action.pkl'}
tag_object_statprop = {'name': 'obj_statprop',
                     'not_per_timestep':''}

config = {
    'current_dir':current_dir,
    'save_data': False,
    'save_raw_images':'',
    'start_index':0,
    'end_index': 49,
    'agent':agent,
    'policy':policy,
    'ngroup': 100,
    'sourcetags':[tag_images, tag_qpos, tag_object_full_pose, tag_object_statprop],
    'source_basedirs':[ROOT_DIR + '/pushing_data/cartgripper_startgoal_masks/train'],
    'sequence_length':2
}