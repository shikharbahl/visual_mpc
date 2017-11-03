import os
import numpy as np
import tensorflow as tf
import imp
import sys
import cPickle
import pdb

import imp
from tensorflow.python.platform import app
from tensorflow.python.platform import flags

from datetime import datetime
import collections
# How often to record tensorboard summaries.
SUMMARY_INTERVAL = 400

# How often to run a batch through the validation model.
VAL_INTERVAL = 500

# How often to save a model checkpoint
SAVE_INTERVAL = 4000

from dynamic_base_model import Dynamic_Base_Model
# from python_visual_mpc.video_prediction.tracking_model.single_point_tracking_model import Single_Point_Tracking_Model



if __name__ == '__main__':
    FLAGS = flags.FLAGS
    flags.DEFINE_string('hyper', '', 'hyperparameters configuration file')
    flags.DEFINE_string('visualize', '', 'model within hyperparameter folder from which to create gifs')
    flags.DEFINE_integer('device', 0 ,'the value for CUDA_VISIBLE_DEVICES variable')
    flags.DEFINE_string('pretrained', None, 'path to model file from which to resume training')
    flags.DEFINE_bool('diffmotions', False, 'visualize several different motions for a single scene')
    flags.DEFINE_bool('metric', False, 'compute metric of expected distance to human-labled positions ob objects')

    flags.DEFINE_bool('create_images', False, 'whether to create images')


def main(unused_argv, conf_script= None):
    os.environ["CUDA_VISIBLE_DEVICES"] = str(FLAGS.device)
    print 'using CUDA_VISIBLE_DEVICES=', FLAGS.device
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
        conf['schedsamp_k'] = -1  # don't feed ground truth
        conf['data_dir'] = '/'.join(str.split(conf['data_dir'], '/')[:-1] + ['test'])

        conf['visualize'] = conf['output_dir'] + '/' + FLAGS.visualize
        conf['event_log_dir'] = '/tmp'
        conf.pop('use_len', None)

        if FLAGS.metric:
            conf['batch_size'] = 128
            conf['sequence_length'] = 15
        else:
            conf['batch_size'] = 15

        conf['sequence_length'] = 14
        if FLAGS.diffmotions:
            conf['sequence_length'] = 30

        # when using alex interface:
        if 'modelconfiguration' in conf:
            conf['modelconfiguration']['schedule_sampling_k'] = conf['schedsamp_k']

    if 'pred_model' in conf:
        Model = conf['pred_model']
    else:
        Model = Dynamic_Base_Model

    if FLAGS.diffmotions or "visualize_tracking" in conf or FLAGS.metric:
        model = Model(conf, load_data =False, trafo_pix=True)
    else:
        model = Model(conf, load_data=True, trafo_pix=False)

    print 'Constructing saver.'
    # Make saver.

    # if isinstance(model, Single_Point_Tracking_Model) and not FLAGS.visualize:
    #     # initialize the predictor from pretrained weights
    #     # select weights that are *not* part of the tracker
    #     vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES)
    #     predictor_vars = []
    #     for var in vars:
    #         if str.split(var.name, '/')[0] != 'tracker':
    #             predictor_vars.append(var)


    vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES)
    # remove all states from group of variables which shall be saved and restored:
    vars_no_state = filter_vars(vars)
    saver = tf.train.Saver(vars_no_state, max_to_keep=0)

    gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.9)
    # Make training session.
    sess = tf.InteractiveSession(config=tf.ConfigProto(gpu_options=gpu_options))
    summary_writer = tf.summary.FileWriter(conf['output_dir'], graph=sess.graph, flush_secs=10)

    if not FLAGS.diffmotions:
        tf.train.start_queue_runners(sess)

    sess.run(tf.global_variables_initializer())

    if conf['visualize']:
        print '-------------------------------------------------------------------'
        print 'verify current settings!! '
        for key in conf.keys():
            print key, ': ', conf[key]
        print '-------------------------------------------------------------------'

        saver.restore(sess, conf['visualize'])
        print 'restore done.'

        if FLAGS.diffmotions:
            model.visualize_diffmotions(sess)
        elif FLAGS.metric:
            model.compute_metric(sess, FLAGS.create_images)
        else:
            model.visualize(sess)

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

    print '-------------------------------------------------------------------'
    print 'verify current settings!! '
    for key in conf.keys():
        print key, ': ', conf[key]
    print '-------------------------------------------------------------------'

    tf.logging.info('iteration number, cost')

    starttime = datetime.now()
    t_iter = []
    # Run training.

    for itr in range(itr_0, conf['num_iterations'], 1):
        t_startiter = datetime.now()
        # Generate new batch of data_files.
        feed_dict = {model.iter_num: np.float32(itr),
                     model.train_cond: 1}

        cost, _, summary_str = sess.run([model.loss, model.train_op, model.summ_op],
                                        feed_dict)

        if (itr) % 10 ==0:
            tf.logging.info(str(itr) + ' ' + str(cost))

        if (itr) % VAL_INTERVAL == 2:
            # Run through validation set.
            feed_dict = {model.iter_num: np.float32(itr),
                         model.train_cond: 0}
            [val_summary_str] = sess.run([model.summ_op], feed_dict)
            summary_writer.add_summary(val_summary_str, itr)

        if (itr) % SAVE_INTERVAL == 2:
            tf.logging.info('Saving model to' + conf['output_dir'])
            saver.save(sess, conf['output_dir'] + '/model' + str(itr))

        t_iter.append((datetime.now() - t_startiter).seconds * 1e6 +  (datetime.now() - t_startiter).microseconds )

        if itr % 100 == 1:
            hours = (datetime.now() -starttime).seconds/3600
            tf.logging.info('running for {0}d, {1}h, {2}min'.format(
                (datetime.now() - starttime).days,
                hours,+
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



def filter_vars(vars):
    newlist = []
    for v in vars:
        if not '/state:' in v.name:
            newlist.append(v)
        else:
            print 'removed state variable from saving-list: ', v.name

    return newlist



if __name__ == '__main__':
    tf.logging.set_verbosity(tf.logging.INFO)
    app.run()