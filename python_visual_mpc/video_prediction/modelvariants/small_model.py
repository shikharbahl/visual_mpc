import numpy as np
import tensorflow as tf

import tensorflow.contrib.slim as slim
from tensorflow.contrib.layers.python import layers as tf_layers
from python_visual_mpc.video_prediction.lstm_ops12 import basic_conv_lstm_cell
from python_visual_mpc.misc.zip_equal import zip_equal

import collections
import cPickle
from python_visual_mpc.video_prediction.utils_vpred.animate_tkinter import Visualizer_tkinter
import pdb

# Amount to use when lower bounding tensors

from python_visual_mpc.video_prediction.basecls.prediction_model_basecls import Base_Prediction_Model


class Small_Model(Base_Prediction_Model):
    def __init__(self,
                conf = None,
                trafo_pix = True,
                load_data = True,
                mode=True):
        Base_Prediction_Model.__init__(self,
                                        conf = conf,
                                        trafo_pix = trafo_pix,
                                        load_data = load_data,
                                        )


    def build_network_core(self, action, current_state, input_image):
        lstm_func = basic_conv_lstm_cell
        
        self.lstm_size = np.int32(np.array([8, 16, 32, 16, 8]))

        with slim.arg_scope(
                [lstm_func, slim.layers.conv2d, slim.layers.fully_connected,
                 tf_layers.layer_norm, slim.layers.conv2d_transpose],
                reuse=self.reuse):

            enc0 = slim.layers.conv2d(  # 32x32x32
                input_image,
                32, [5, 5],
                stride=2,
                scope='scale1_conv1',
                normalizer_fn=tf_layers.layer_norm,
                normalizer_params={'scope': 'layer_norm1'})
            hidden1, self.lstm_state1 = self.lstm_func(  # 32x32x16
                enc0, self.lstm_state1, self.lstm_size[0], scope='state1')
            hidden1 = tf_layers.layer_norm(hidden1, scope='layer_norm2')

            enc1 = slim.layers.conv2d(  # 16x16x16
                hidden1, hidden1.get_shape()[3], [3, 3], stride=2, scope='conv2')
            hidden3, self.lstm_state3 = self.lstm_func(  # 16x16x32
                enc1, self.lstm_state3, self.lstm_size[1], scope='state3')
            hidden3 = tf_layers.layer_norm(hidden3, scope='layer_norm4')

            enc2 = slim.layers.conv2d(  # 8x8x32
                hidden3, hidden3.get_shape()[3], [3, 3], stride=2, scope='conv3')

            if not 'ignore_state_action' in self.conf:
                # Pass in state and action.
                state_action = tf.concat(axis=1, values=[action, current_state])

                smear = tf.reshape(state_action, [int(self.batch_size), 1, 1, int(state_action.get_shape()[1])])
                smear = tf.tile(
                    smear, [1, int(enc2.get_shape()[1]), int(enc2.get_shape()[2]), 1])

                enc2 = tf.concat(axis=3, values=[enc2, smear])
            else:
                print 'ignoring states and actions'
            enc3 = slim.layers.conv2d(  # 8x8x32
                enc2, hidden3.get_shape()[3], [1, 1], stride=1, scope='conv4')
            hidden5, self.lstm_state5 = self.lstm_func(  # 8x8x64
                enc3, self.lstm_state5, self.lstm_size[2], scope='state5')
            hidden5 = tf_layers.layer_norm(hidden5, scope='layer_norm6')

            enc4 = slim.layers.conv2d_transpose(  # 16x16x64
                hidden5, hidden5.get_shape()[3], 3, stride=2, scope='convt1')
            hidden6, self.lstm_state6 = self.lstm_func(  # 16x16x32
                enc4, self.lstm_state6, self.lstm_size[3], scope='state6')
            hidden6 = tf_layers.layer_norm(hidden6, scope='layer_norm7')
            if 'noskip' not in self.conf:
                # Skip connection.
                hidden6 = tf.concat(axis=3, values=[hidden6, enc1])  # both 16x16

            enc5 = slim.layers.conv2d_transpose(  # 32x32x32
                hidden6, hidden6.get_shape()[3], 3, stride=2, scope='convt2')
            hidden7, self.lstm_state7 = self.lstm_func(  # 32x32x16
                enc5, self.lstm_state7, self.lstm_size[4], scope='state7')
            hidden7 = tf_layers.layer_norm(hidden7, scope='layer_norm8')
            if not 'noskip' in self.conf:
                # Skip connection.
                hidden7 = tf.concat(axis=3, values=[hidden7, enc0])  # both 32x32

            enc6 = slim.layers.conv2d_transpose(  # 64x64x16
                hidden7,
                hidden7.get_shape()[3], 3, stride=2, scope='convt3',
                normalizer_fn=tf_layers.layer_norm,
                normalizer_params={'scope': 'layer_norm9'})

            if current_state != None:
                current_state = slim.layers.fully_connected(
                    state_action,
                    int(current_state.get_shape()[1]),
                    scope='state_pred',
                    activation_fn=None)
            self.gen_states.append(current_state)

            self.apply_trafo_predict(enc6, hidden5)

            return current_state