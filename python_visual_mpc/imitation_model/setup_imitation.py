import tensorflow as tf
import importlib.machinery
import importlib.util
import numpy as np
import os
from python_visual_mpc.imitation_model.imitation_model import gen_mix_samples

def setup_openloop_predictor(imitation_conf, gpu_id = 0):
    if gpu_id == None:
        gpu_id = 0
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    print('using CUDA_VISIBLE_DEVICES=', os.environ["CUDA_VISIBLE_DEVICES"] )

    imitation_config_fpath, pretrained_model = imitation_conf

    loader = importlib.machinery.SourceFileLoader('mod_hyper', imitation_config_fpath)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    conf = importlib.util.module_from_spec(spec)
    loader.exec_module(conf)
    net_config = conf.configuration

    pretrained_path = os.path.join(net_config['model_dir'], pretrained_model)

    img_height, img_width = net_config['orig_size']
    adim, sdim = net_config['adim'], net_config['sdim']

    images_pl = tf.placeholder(tf.uint8, [1, None, img_height, img_width, 3])
    actions = tf.placeholder(tf.float32, [1, None, adim])
    end_effector_pos_pl = tf.placeholder(tf.float32, [1, None, sdim])

    with tf.variable_scope('model', reuse=None) as imitation_scope:
        model = net_config['model'](net_config, images_pl, actions, end_effector_pos_pl)
        model.build_sim()

    vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope = 'model')
    saver = tf.train.Saver(vars, max_to_keep=0)

    gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.1)
    sess = tf.Session(config=tf.ConfigProto(gpu_options=gpu_options))
    tf.train.start_queue_runners(sess)
    sess.run(tf.global_variables_initializer())

    print('restoring model from {}'.format(pretrained_path))
    saver.restore(sess, pretrained_path)

    def predict(past_images, past_eeps, n_samples, over_sample_factor = 3):
        images = past_images.reshape((1, -1, img_height, img_width, 3)).astype(np.uint8)
        end_effector = past_eeps.reshape((1, -1, sdim)).astype(np.float32)
        
        f_dict = {images_pl: images, end_effector_pos_pl : end_effector}

        mdn_mix, mdn_std_dev, mdn_means = sess.run([model.mixing_parameters, model.std_dev, model.means],
                                                        feed_dict=f_dict)
        action = np.zeros((net_config['sequence_length'], n_samples, net_config['adim']))
        last_state = np.repeat(past_eeps[-1].reshape((1, -1)), n_samples, axis = 0)
   
        for i in range(net_config['sequence_length']):
            samps, samps_log_l = gen_mix_samples(n_samples * over_sample_factor, mdn_means[0, i], mdn_std_dev[0, i], mdn_mix[0, i])
            for j in range(n_samples):
                if samps[j, -1] >= 0.05:
                    samps[j, -1] = 21
                else:
                    samps[j, -1] = -100
            action[i, :, :4] = samps[:n_samples, :4] - last_state[:, :4]
            action[i, :, -1] = samps[:n_samples, -1]

            last_state = samps[:n_samples]

        return np.transpose(action, (1, 0, 2))
    return predict