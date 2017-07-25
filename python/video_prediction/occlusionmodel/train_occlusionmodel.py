import os
import numpy as np
import tensorflow as tf
import imp
import sys
import cPickle
import pdb
import matplotlib.pyplot as plt

import imp

from video_prediction.utils_vpred.adapt_params_visualize import adapt_params_visualize
from tensorflow.python.platform import app
from tensorflow.python.platform import flags
import video_prediction.utils_vpred.create_gif


from video_prediction.utils_vpred.skip_example import skip_example

import makegifs

from datetime import datetime

# How often to record tensorboard summaries.
SUMMARY_INTERVAL = 40

# How often to run a batch through the validation model.
VAL_INTERVAL = 200

# How often to save a model checkpoint
SAVE_INTERVAL = 2000

FLAGS = flags.FLAGS
flags.DEFINE_string('hyper', '', 'hyperparameters configuration file')
flags.DEFINE_string('visualize', '', 'model within hyperparameter folder from which to create gifs')
flags.DEFINE_integer('device', 0 ,'the value for CUDA_VISIBLE_DEVICES variable, -1 uses cpu')
flags.DEFINE_string('pretrained', None, 'path to model file from which to resume training')
flags.DEFINE_bool('diffmotions', False, 'visualize several different motions for a single scene')
flags.DEFINE_bool('canon', False, 'use canonical examples')

## Helper functions
def peak_signal_to_noise_ratio(true, pred):
    """Image quality metric based on maximal signal power vs. power of the noise.

    Args:
      true: the ground truth image.
      pred: the predicted image.
    Returns:
      peak signal to noise ratio (PSNR)
    """
    return 10.0 * tf.log(1.0 / mean_squared_error(true, pred)) / tf.log(10.0)


def mean_squared_error(true, pred):
    """L2 distance between tensors true and pred.

    Args:
      true: the ground truth image.
      pred: the predicted image.
    Returns:
      mean squared error between ground truth and predicted image.
    """

    return tf.reduce_sum(tf.square(true - pred)) / tf.to_float(tf.size(pred))


class Model(object):
    def __init__(self,
                 conf,
                 images=None,
                 actions=None,
                 states=None,
                 reuse_scope=None,
                 pix_distrib=None
                 ):


        if 'model' in conf:
            Occlusion_Model = conf['model']
        else:
            from occlusionmodel import Occlusion_Model

        self.prefix = prefix = tf.placeholder(tf.string, [])
        self.iter_num = tf.placeholder(tf.float32, [])
        self.conf = conf

        if 'use_len' in conf:
            images, states, actions  = self.random_shift(images, states, actions)

        summaries = []

        # Split into timesteps.
        if actions != None:
            actions = tf.split(1, actions.get_shape()[1], actions)
            actions = [tf.squeeze(act) for act in actions]
        if states != None:
            states = tf.split(1, states.get_shape()[1], states)
            states = [tf.squeeze(st) for st in states]

        if pix_distrib != None:
            pix_distrib = tf.split(1, pix_distrib.get_shape()[1], pix_distrib)
            pix_distrib = [tf.squeeze(st) for st in pix_distrib]

        images = tf.split(1, images.get_shape()[1], images)
        images = [tf.squeeze(img) for img in images]

        if reuse_scope is None:
            self.om = Occlusion_Model(
                images,
                actions,
                states,
                iter_num=self.iter_num,
                conf=conf,
                pix_distibution=pix_distrib)
            self.om.build()
        else:  # If it's a validation or test model.
            with tf.variable_scope(reuse_scope, reuse=True):
                self.om = Occlusion_Model(
                    images,
                    actions,
                    states,
                    iter_num=self.iter_num,
                    conf= conf,
                    pix_distibution=pix_distrib)
                self.om.build()

        # L2 loss, PSNR for eval.
        loss, psnr_all = 0.0, 0.0

        for i, x, gx in zip(
                range(len(self.om.gen_images)), images[conf['context_frames']:],
                self.om.gen_images[conf['context_frames'] - 1:]):
            recon_cost_mse = mean_squared_error(x, gx)
            summaries.append(
                tf.scalar_summary(prefix + '_recon_cost' + str(i), recon_cost_mse))
            recon_cost = recon_cost_mse
            loss += recon_cost

        for i, state, gen_state in zip(
                range(len(self.om.gen_states)), states[conf['context_frames']:],
                self.om.gen_states[conf['context_frames'] - 1:]):
            state_cost = mean_squared_error(state, gen_state) * 1e-4 * conf['use_state']
            summaries.append(
                tf.scalar_summary(prefix + '_state_cost' + str(i), state_cost))
            loss += state_cost

        if 'mask_act_cost' in conf:  #encourage all masks but the first to have small regions of activation
            print 'computing mask_act_cost with factor:', conf['mask_act_cost']
            act_cost = self.mask_act_loss(self.om.objectmasks) * conf['mask_act_cost']
            summaries.append(
                tf.scalar_summary(prefix + '_mask_act_cost', act_cost))
            loss += act_cost

        if 'mask_distinction_cost' in conf:
            dcost = self.distinction_loss(self.om.objectmasks) * conf['mask_distinction_cost']
            summaries.append(tf.scalar_summary(prefix + '_mask_distinction_cost', dcost))
            loss += dcost

        if 'padding_usage_penalty' in self.conf:
            print 'computing padding_usage_penalty with factor:', conf['padding_usage_penalty']
            padding_usage_loss = 0
            for gmask, pmap in zip(self.om.gen_masks, self.om.padding_map[1:]):
                for p in range(len(gmask)):
                    pmap[p] += 1
                    padding_usage_loss += tf.reduce_sum(gmask[p]*pmap[p])

            padding_usage_loss *= self.conf['padding_usage_penalty']
            summaries.append(tf.scalar_summary(prefix + '_padding_usage_', padding_usage_loss))
            loss+= padding_usage_loss

        if 'mask_consistency_loss' in self.conf:
            print 'computing mask_consistency_loss with factor:', conf['mask_consistency_loss']
            m_cons_loss = 0
            for gmask, mmask in zip(self.om.gen_masks, self.om.moved_masksl[1:]):
                for p in range(len(gmask)):
                    m_cons_loss += mean_squared_error(gmask[p], mmask[p])*conf['mask_consistency_loss']
            summaries.append(tf.scalar_summary(prefix + '_mask_consistency_loss', m_cons_loss))
            loss += m_cons_loss

        if 'compfact_slowness' in self.conf:
            print 'computing compfact_slowness loss with factor:', conf['compfact_slowness']
            cfact_loss = 0
            cfact = self.om.list_of_comp_factors
            cfact = [tf.concat(1, c) for c in cfact]

            for i in range(len(cfact)-1):
                cfact_loss += mean_squared_error(cfact[i], cfact[i+1]) * conf['compfact_slowness']
            summaries.append(tf.scalar_summary(prefix + '_compfact_slowness', cfact_loss))
            loss += cfact_loss

        self.loss = loss = loss / np.float32(len(images) - conf['context_frames'])

        summaries.append(tf.scalar_summary(prefix + '_loss', loss))

        self.lr = tf.placeholder_with_default(conf['learning_rate'], ())

        self.train_op = tf.train.AdamOptimizer(self.lr).minimize(loss)
        self.summ_op = tf.merge_summary(summaries)

    def mask_act_loss(self, masks):
        print 'adding mask activation loss'
        act_cost = 0
        for mask in masks[1:]:
            act_cost += tf.reduce_sum(mask)
        return act_cost

    def distinction_loss(self, masks):
        print 'adding mask distinction loss'
        delta = 0.
        for i in range(self.conf['num_masks']):
            for j in range(self.conf['num_masks']):
                if i == j:
                    continue
                delta -= tf.reduce_sum(tf.abs(masks[i]-masks[j]))
        return delta

    def random_shift(self, images, states, actions):
        print 'shifting the video sequence randomly in time'
        tshift = 2
        uselen = self.conf['use_len']
        fulllength = self.conf['sequence_length']
        nshifts = (fulllength - uselen) / 2 + 1
        rand_ind = tf.random_uniform([1], 0, nshifts, dtype=tf.int64)
        self.rand_ind = rand_ind

        start = tf.concat(0,[tf.zeros(1, dtype=tf.int64), rand_ind * tshift, tf.zeros(3, dtype=tf.int64)])
        images_sel = tf.slice(images, start, [-1, uselen, -1, -1, -1])
        start = tf.concat(0, [tf.zeros(1, dtype=tf.int64), rand_ind * tshift, tf.zeros(1, dtype=tf.int64)])
        actions_sel = tf.slice(actions, start, [-1, uselen, -1])
        start = tf.concat(0, [tf.zeros(1, dtype=tf.int64), rand_ind * tshift, tf.zeros(1, dtype=tf.int64)])
        states_sel = tf.slice(states, start, [-1, uselen, -1])

        return images_sel, states_sel, actions_sel

def main(unused_argv, conf_script= None):

    if FLAGS.device ==-1:   # using cpu!
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        tfconfig = None
    else:
        print 'using CUDA_VISIBLE_DEVICES=', FLAGS.device
        os.environ["CUDA_VISIBLE_DEVICES"] = str(FLAGS.device)
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.9)
        tfconfig = tf.ConfigProto(gpu_options=gpu_options)

        from tensorflow.python.client import device_lib
        print device_lib.list_local_devices()

    if conf_script == None: conf_file = FLAGS.hyper
    else: conf_file = conf_script

    if not os.path.exists(FLAGS.hyper):
        sys.exit("Experiment configuration not found")

    hyperparams = imp.load_source('hyperparams', conf_file)
    conf = hyperparams.configuration
    if FLAGS.visualize:
        print 'creating visualizations ...'
        conf = adapt_params_visualize(conf, FLAGS.visualize)
        conf.pop('use_len', None)
        conf['sequence_length'] = 14
        conf['batch_size'] = 10

    print '-------------------------------------------------------------------'
    print 'verify current settings!! '
    for key in conf.keys():
        print key, ': ', conf[key]
    print '-------------------------------------------------------------------'

    if 'sawyer' in conf:
        from video_prediction.sawyer.read_tf_record_sawyer import build_tfrecord_input
    else:
        from video_prediction.read_tf_record import build_tfrecord_input

    if FLAGS.diffmotions or FLAGS.canon:
        print 'visualizing pixel motion'
        val_model = Diffmotion_model(conf, build_tfrecord_input)
    else:
        print 'Constructing models and inputs.'
        with tf.variable_scope('model', reuse=None) as training_scope:
            images, actions, states = build_tfrecord_input(conf, training=True)
            model = Model(conf, images, actions, states)

        with tf.variable_scope('val_model', reuse=None):
            val_images, val_actions, val_states = build_tfrecord_input(conf, training=False)
            val_model = Model(conf, val_images, val_actions, val_states, training_scope)

    print 'Constructing saver.'
    # Make saver.
    saver = tf.train.Saver(tf.get_collection(tf.GraphKeys.VARIABLES), max_to_keep=0)


    # Make training session.
    sess = tf.InteractiveSession(config= tfconfig)
    summary_writer = tf.train.SummaryWriter(
        conf['output_dir'], graph=sess.graph, flush_secs=10)

    tf.train.start_queue_runners(sess)
    sess.run(tf.initialize_all_variables())


    if conf['visualize']:
        saver.restore(sess, conf['visualize'])
        file_path = conf['output_dir']

        if FLAGS.diffmotions or FLAGS.canon:
            val_model.visualize_diffmotions(file_path, sess)
            return
        else:
            feed_dict = {val_model.lr: 0.0,
                         val_model.prefix: 'vis',
                         val_model.iter_num: 0}

            ground_truth, gen_images, object_masks, moved_images, trafos, comp_factors, moved_parts, first_step_masks, moved_masks, background, background_mask, gen_masks = sess.run([
                                                            val_images,
                                                            val_model.om.gen_images,
                                                            val_model.om.objectmasks,
                                                            val_model.om.moved_imagesl,
                                                            val_model.om.list_of_trafos,
                                                            val_model.om.list_of_comp_factors,
                                                            val_model.om.moved_partsl,
                                                            val_model.om.first_step_masks,
                                                            val_model.om.moved_masksl,
                                                            val_model.om.background,
                                                            val_model.om.background_mask,
                                                            val_model.om.gen_masks
                                                            ],
                                                            feed_dict)
            dict_ = {}
            dict_['ground_truth'] = ground_truth
            dict_['gen_images'] = gen_images
            dict_['object_masks'] = object_masks
            dict_['moved_images'] = moved_images
            dict_['trafos'] = trafos
            dict_['comp_factors'] = comp_factors
            dict_['moved_parts'] = moved_parts
            dict_['first_step_masks'] = first_step_masks
            dict_['moved_masks'] = moved_masks
            dict_['background'] = background
            dict_['background_mask'] = background_mask
            dict_['gen_masks'] = gen_masks

            cPickle.dump(dict_, open(file_path + '/dict_.pkl', 'wb'))
            print 'written files to:' + file_path
            makegifs.comp_gif(conf, conf['output_dir'], show_parts=True, examples=10)
            return

    itr_0 =0

    if FLAGS.pretrained != None:
        conf['pretrained_model'] = FLAGS.pretrained

        saver.restore(sess, conf['pretrained_model'])
        # resume training at iteration step of the loaded model:
        import re
        itr_0 = re.match('.*?([0-9]+)$', conf['pretrained_model']).group(1)
        itr_0 = int(itr_0)
        print 'resuming training at iteration:  ', itr_0

    tf.logging.info('iteration number, cost')

    starttime = datetime.now()
    t_iter = []


    for itr in range(itr_0, conf['num_iterations'], 1):
        t_startiter = datetime.now()
        # Generate new batch of data_files.
        feed_dict = {model.prefix: 'train',
                     model.iter_num: np.float32(itr),
                     model.lr: conf['learning_rate'],
                     }
        cost, _, summary_str = sess.run([model.loss, model.train_op, model.summ_op],
                                        feed_dict)

        # Print info: iteration #, cost.
        if (itr) % 10 ==0:
            tf.logging.info(str(itr) + ' ' + str(cost))

        if (itr) % VAL_INTERVAL == 2:
            # Run through validation set.
            feed_dict = {val_model.lr: 0.0,
                         val_model.prefix: 'val',
                         val_model.iter_num: np.float32(itr),
                         }
            _, val_summary_str = sess.run([val_model.train_op, val_model.summ_op],
                                          feed_dict)
            summary_writer.add_summary(val_summary_str, itr)


        if (itr) % SAVE_INTERVAL == 2:
            tf.logging.info('Saving model to' + conf['output_dir'])
            # oldfile = conf['output_dir'] + '/model' + str(itr - SAVE_INTERVAL)
            # if os.path.isfile(oldfile):
            #     os.system("rm {}".format(oldfile))
            #     os.system("rm {}".format(oldfile + '.meta'))
            saver.save(sess, conf['output_dir'] + '/model' + str(itr))

        t_iter.append((datetime.now() - t_startiter).seconds * 1e6 +  (datetime.now() - t_startiter).microseconds )

        if itr % 100 == 1:
            hours = (datetime.now() -starttime).seconds/3600
            tf.logging.info('running for {0}d, {1}h, {2}min'.format(
                (datetime.now() - starttime).days,
                hours,
                (datetime.now() - starttime).seconds/60 - hours*60))
            avg_t_iter = np.sum(np.asarray(t_iter))/len(t_iter)
            tf.logging.info('time per iteration: {0}'.format(avg_t_iter/1e6))
            tf.logging.info('expected for complete training: {0}h '.format(avg_t_iter /1e6/3600 * conf['num_iterations']))

        if (itr) % SUMMARY_INTERVAL:
            summary_writer.add_summary(summary_str, itr)

    tf.logging.info('Saving model.')
    saver.save(sess, conf['output_dir'] + '/model')
    tf.logging.info('Training complete')
    tf.logging.flush()

class Diffmotion_model(Model):
    def __init__(self, conf, build_tfrecord_input):
        self.conf = conf
        self.actions_pl = tf.placeholder(tf.float32, name='actions',
                                    shape=(conf['batch_size'], conf['sequence_length'], 4))
        self.states_pl = tf.placeholder(tf.float32, name='states',
                                   shape=(conf['batch_size'], conf['sequence_length'], 3))
        self.images_pl = tf.placeholder(tf.float32, name='images',
                                   shape=(conf['batch_size'], conf['sequence_length'], 64, 64, 3))
        self.val_images, _, self.val_states = build_tfrecord_input(conf, training=False)

        self.pix_distrib_pl = tf.placeholder(tf.float32, name='states',
                                        shape=(conf['batch_size'], conf['sequence_length'], 64, 64, 1))

        with tf.variable_scope('model', reuse=None):
            Model.__init__(self, conf, self.images_pl, self.actions_pl, self.states_pl, pix_distrib=self.pix_distrib_pl)

    def visualize_diffmotions(self,file_path, sess):
        feed_dict = {self.lr: 0.0, self.prefix: 'vis', self.iter_num: 0}
        b_exp, ind0 = 5, 0 #9

        if FLAGS.canon:
            file_path_canon = '/home/frederik/Documents/catkin_ws/src/lsdc/pushing_data/canonical_examples'
            dict = cPickle.load(open(file_path_canon + '/pkl/example{}.pkl'.format(b_exp), 'rb'))
            desig_pix = dict['desig_pix']
            one_hot = create_one_hot(self.conf, desig_pix)
            sel_img = dict['images']
            sel_img = sel_img[:2]
            state = dict['endeff']
            sel_state = state[:2]
        else:
            img, state = sess.run([self.val_images, self.val_states])
            sel_img = img[b_exp, ind0:ind0 + 2]
            c = Getdesig(sel_img[0], self.conf, 'b{}'.format(b_exp))
            desig_pix = c.coords.astype(np.int32)

            # desig_pos_aux1 = np.array([14, 45])
            print "selected designated position for aux1 [row,col]:", desig_pix
            one_hot = create_one_hot(self.conf, desig_pix)
            sel_state = np.stack([state[b_exp, ind0], state[b_exp, ind0 + 1]], axis=0)

        feed_dict[self.pix_distrib_pl] = one_hot

        start_states = np.concatenate([sel_state, np.zeros((self.conf['sequence_length'] - 2, 3))])
        start_states = np.expand_dims(start_states, axis=0)
        start_states = np.repeat(start_states, self.conf['batch_size'], axis=0)  # copy over batch
        feed_dict[self.states_pl] = start_states

        start_images = np.concatenate([sel_img, np.zeros((self.conf['sequence_length'] - 2, 64, 64, 3))])
        start_images = np.expand_dims(start_images, axis=0)
        start_images = np.repeat(start_images, self.conf['batch_size'], axis=0)  # copy over batch
        feed_dict[self.images_pl] = start_images

        actions = np.zeros([self.conf['batch_size'], self.conf['sequence_length'], 4])
        step = .025
        step = .05
        n_angles = 8
        for b in range(n_angles):
            for i in range(self.conf['sequence_length']):
                actions[b, i] = np.array([np.cos(b / float(n_angles) * 2 * np.pi) * step, np.sin(b / float(n_angles) * 2 * np.pi) * step, 0,0])
        b += 1
        actions[b, 0] = np.array([0, 0, 4, 0])
        actions[b, 1] = np.array([0, 0, 4, 0])
        b += 1
        actions[b, 0] = np.array([0, 0, 0, 4])
        actions[b, 1] = np.array([0, 0, 0, 4])
        feed_dict[self.actions_pl] = actions

        gen_images, object_masks, moved_images, trafos, comp_factors, moved_parts, first_step_masks, moved_masks, background, background_mask, moved_pix_distrib = sess.run(
            [
                self.om.gen_images,
                self.om.objectmasks,
                self.om.moved_imagesl,
                self.om.list_of_trafos,
                self.om.list_of_comp_factors,
                self.om.moved_partsl,
                self.om.first_step_masks,
                self.om.moved_masksl,
                self.om.background,
                self.om.background_mask,
                self.om.moved_pix_distrib,
            ],
            feed_dict)
        dict_ = {}
        dict_['gen_images'] = gen_images
        dict_['object_masks'] = object_masks
        dict_['moved_images'] = moved_images
        dict_['trafos'] = trafos
        dict_['comp_factors'] = comp_factors
        dict_['moved_parts'] = moved_parts
        dict_['first_step_masks'] = first_step_masks
        dict_['moved_masks'] = moved_masks
        dict_['background'] = background
        dict_['background_mask'] = background_mask
        dict_['moved_pix_distrib'] = moved_pix_distrib
        dict_['desig_pix'] = desig_pix

        cPickle.dump(dict_, open(file_path + '/dict_.pkl', 'wb'))
        print 'written files to:' + file_path
        makegifs.comp_gif(self.conf, self.conf['output_dir'],
                          name='pixelmotion_b{}_l{}'.format(b_exp, self.conf['sequence_length']),
                          show_parts=True, examples= self.conf['batch_size'])
        return

def create_one_hot(conf, desig_pix):
    one_hot = np.zeros((1, 1, 64, 64, 1), dtype=np.float32)
    # switch on pixels
    one_hot[0, 0, desig_pix[0], desig_pix[1]] = 1.
    one_hot = np.repeat(one_hot, conf['context_frames'], axis=1)
    app_zeros = np.zeros((1, conf['sequence_length']- conf['context_frames'], 64, 64, 1), dtype=np.float32)
    one_hot = np.concatenate([one_hot, app_zeros], axis=1)
    one_hot = np.repeat(one_hot, conf['batch_size'], axis=0)
    return one_hot

class Getdesig(object):
    def __init__(self,img,conf,img_namesuffix):
        self.suf = img_namesuffix
        self.conf = conf
        self.img = img
        fig = plt.figure()
        self.ax = fig.add_subplot(111)
        self.ax.set_xlim(0, 63)
        self.ax.set_ylim(63, 0)
        plt.imshow(img)

        self.coords = None
        cid = fig.canvas.mpl_connect('button_press_event', self.onclick)
        plt.show()

    def onclick(self, event):
        print('button=%d, x=%d, y=%d, xdata=%f, ydata=%f' %
              (event.button, event.x, event.y, event.xdata, event.ydata))
        self.coords = np.array([event.ydata, event.xdata])
        self.ax.scatter(self.coords[1], self.coords[0], s=60, facecolors='none', edgecolors='b')
        self.ax.set_xlim(0, 63)
        self.ax.set_ylim(63, 0)
        plt.draw()
        plt.savefig(self.conf['output_dir']+'/img_desigpix'+self.suf)

if __name__ == '__main__':
    tf.logging.set_verbosity(tf.logging.INFO)
    app.run()
