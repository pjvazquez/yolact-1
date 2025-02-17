import datetime
import contextlib
import tensorflow as tf
import tensorflow_model_optimization as tfmot
# tf.config.experimental_run_functions_eagerly(True)
# tf.debugging.enable_check_numerics()

# it s recommanded to use absl for tf 2.0
from absl import app
from absl import flags
from absl import logging

import yolact
from yolactModule import YOLACTModule
from data import dataset_coco
from loss import loss_yolact
from utils import learning_rate_schedule
from utils import coco_evaluation
from utils import standard_fields

import numpy as np
import cv2
from google.protobuf import text_format
from protos import string_int_label_map_pb2

tf.random.set_seed(1234)

FLAGS = flags.FLAGS

flags.DEFINE_string('tfrecord_dir', './data/coco',
                    'directory of tfrecord')
flags.DEFINE_string('label_map', './label_map.pbtxt',
                    'path to label_map.pbtxt')
flags.DEFINE_string('weights', './weights',
                    'path to store weights')
flags.DEFINE_integer('train_iter', 800000,
                     'iteraitons')
flags.DEFINE_integer('batch_size', 1,
                     'train batch size')
flags.DEFINE_integer('num_class', 10,
                     'number of class')
flags.DEFINE_integer('img_h', 550,
                     'image height')
flags.DEFINE_integer('img_w', 550,
                     'image width')
flags.DEFINE_list('aspect_ratio', [1, 0.5, 2],
                   'comma-separated list of strings for aspect ratio')
flags.DEFINE_list('scale', [24, 48, 96, 130, 192],
                   'comma-separated list of strings for scales in pixels')
flags.DEFINE_float('lr', 1e-3,
                   'learning rate')
flags.DEFINE_float('total_steps', 1200000,
                   'learning rate total steps')
flags.DEFINE_float('momentum', 0.9,
                   'momentum')
flags.DEFINE_float('weight_decay', 5 * 1e-4,
                   'weight_decay')
flags.DEFINE_float('print_interval', 1,
                   'number of iteration between printing loss')
flags.DEFINE_float('save_interval', 1000,
                   'number of iteration between saving model(checkpoint)')
flags.DEFINE_float('valid_iter', 1000,
                   'number of iteration during validation')
flags.DEFINE_boolean('model_quantization', False,
                    'do quantization aware training')
flags.DEFINE_boolean('tflite_export', False,
                    'Inference Module for TFLite-friendly models')
flags.DEFINE_boolean('use_dcn', False,
                    'use dcnv2 for base model')
'''
def _get_categories_list():
  
'''
def _validate_label_map(label_map):
  # https://github.com/tensorflow/models/blob/67fd2bef6500c14b95e0b0de846960ed13802405/research/object_detection/utils/label_map_util.py#L34
  """Checks if a label map is valid.
  Args:
    label_map: StringIntLabelMap to validate.
  Raises:
    ValueError: if label map is invalid.
  """
  for item in label_map.item:
    if item.id < 0:
      raise ValueError('Label map ids should be >= 0.')
    if (item.id == 0 and item.name != 'background' and
        item.display_name != 'background'):
      raise ValueError('Label map id 0 is reserved for the background label')

def load_labelmap(path):
  # https://github.com/tensorflow/models/blob/67fd2bef6500c14b95e0b0de846960ed13802405/research/object_detection/utils/label_map_util.py#L159
  """Loads label map proto.
  Args:
    path: path to StringIntLabelMap proto text file.
  Returns:
    a StringIntLabelMapProto
  """
  with tf.io.gfile.GFile(path, 'r') as fid:
    label_map_string = fid.read()
    label_map = string_int_label_map_pb2.StringIntLabelMap()
    try:
      text_format.Merge(label_map_string, label_map)
    except text_format.ParseError:
      label_map.ParseFromString(label_map_string)
  _validate_label_map(label_map)
  return label_map

def _get_categories_list(label_map_path):
    # https://github.com/tensorflow/models/blob/67fd2bef6500c14b95e0b0de846960ed13802405/research/cognitive_planning/label_map_util.py#L73
    '''
    return [{
          'id': 1,
          'name': 'person'
      }, {
          'id': 2,
          'name': 'dog'
      }, {
          'id': 3,
          'name': 'cat'
      }]
    '''
    label_map = load_labelmap(label_map_path)
    categories = []
    list_of_ids_already_added = []
    for item in label_map.item:
        name = item.name
        if item.id not in list_of_ids_already_added:
          list_of_ids_already_added.append(item.id)
          categories.append({'id': item.id, 'name': name})
    return categories

def main(argv):
    # set up Grappler for graph optimization
    # Ref: https://www.tensorflow.org/guide/graph_optimization
    @contextlib.contextmanager
    def options(options):
        old_opts = tf.config.optimizer.get_experimental_options()
        tf.config.optimizer.set_experimental_options(options)
        try:
            yield
        finally:
            tf.config.optimizer.set_experimental_options(old_opts)

    # -----------------------------------------------------------------
    # Creating the instance of the model specified.
    logging.info("Creating the model instance of YOLACT")
    model = yolact.Yolact(img_h=FLAGS.img_h, 
                          img_w=FLAGS.img_w,
                          fpn_channels=256,
                          num_class=FLAGS.num_class+1, # adding background class
                          num_mask=64,
                          aspect_ratio=[float(i) for i in FLAGS.aspect_ratio],
                          scales=[int(i) for i in FLAGS.scale],
                          use_dcn=FLAGS.use_dcn)
    if FLAGS.model_quantization:
      logging.info("Quantization aware training")
      quantize_model = tfmot.quantization.keras.quantize_model
      model = quantize_model(model)
    # -----------------------------------------------------------------
    # Creating dataloaders for training and validation
    logging.info("Creating the dataloader from: %s..." % FLAGS.tfrecord_dir)
    train_dataset = dataset_coco.prepare_dataloader(img_h=FLAGS.img_h, 
                                                    img_w=FLAGS.img_w,
                                                    feature_map_size=model.feature_map_size, 
                                                    protonet_out_size=model.protonet_out_size,
                                                    aspect_ratio=[float(i) for i in FLAGS.aspect_ratio], 
                                                    scale=[int(i) for i in FLAGS.scale],
                                                    tfrecord_dir=FLAGS.tfrecord_dir,
                                                    batch_size=FLAGS.batch_size,
                                                    subset='train')

    valid_dataset = dataset_coco.prepare_dataloader(img_h=FLAGS.img_h, 
                                                    img_w=FLAGS.img_w,
                                                    feature_map_size=model.feature_map_size, 
                                                    protonet_out_size=model.protonet_out_size,
                                                    aspect_ratio=[float(i) for i in FLAGS.aspect_ratio], 
                                                    scale=[int(i) for i in FLAGS.scale],
                                                    tfrecord_dir=FLAGS.tfrecord_dir,
                                                    batch_size=1,
                                                    subset='val')
    

    # add weight decay
    def add_weight_decay(model, weight_decay):
        # https://github.com/keras-team/keras/issues/12053
        if (weight_decay is None) or (weight_decay == 0.0):
            return

        # recursion inside the model
        def add_decay_loss(m, factor):
            if isinstance(m, tf.keras.Model):
                for layer in m.layers:
                    add_decay_loss(layer, factor)
            else:
                for param in m.trainable_weights:
                    with tf.keras.backend.name_scope('weight_regularizer'):
                        regularizer = lambda: tf.keras.regularizers.l2(factor)(param)
                        m.add_loss(regularizer)

        # weight decay and l2 regularization differs by a factor of 2
        add_decay_loss(model, weight_decay/2.0)
        return

    add_weight_decay(model, FLAGS.weight_decay)

    # -----------------------------------------------------------------
    # Choose the Optimizor, Loss Function, and Metrics, learning rate schedule
    lr_schedule = learning_rate_schedule.Yolact_LearningRateSchedule(warmup_steps=5000, warmup_lr=1e-4,
                                                                     initial_lr=FLAGS.lr, total_steps=FLAGS.total_steps)
    logging.info("Initiate the Optimizer and Loss function...")
    # optimizer = tf.keras.optimizers.SGD(learning_rate=lr_schedule, momentum=FLAGS.momentum)
    optimizer = tf.keras.optimizers.Adam(learning_rate=lr_schedule)
    criterion = loss_yolact.YOLACTLoss()
    train_loss = tf.keras.metrics.Mean('train_loss', dtype=tf.float32)
    valid_loss = tf.keras.metrics.Mean('valid_loss', dtype=tf.float32)
    loc = tf.keras.metrics.Mean('loc_loss', dtype=tf.float32)
    conf = tf.keras.metrics.Mean('conf_loss', dtype=tf.float32)
    mask = tf.keras.metrics.Mean('mask_loss', dtype=tf.float32)
    seg = tf.keras.metrics.Mean('seg_loss', dtype=tf.float32)
    v_loc = tf.keras.metrics.Mean('vloc_loss', dtype=tf.float32)
    v_conf = tf.keras.metrics.Mean('vconf_loss', dtype=tf.float32)
    v_mask = tf.keras.metrics.Mean('vmask_loss', dtype=tf.float32)
    v_seg = tf.keras.metrics.Mean('vseg_loss', dtype=tf.float32)

    # -----------------------------------------------------------------

    # Setup the TensorBoard for better visualization
    # Ref: https://www.tensorflow.org/tensorboard/get_started
    logging.info("Setup the TensorBoard...")
    current_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    train_log_dir = './logs/gradient_tape/' + current_time + '/train'
    test_log_dir = './logs/gradient_tape/' + current_time + '/test'
    train_summary_writer = tf.summary.create_file_writer(train_log_dir)
    test_summary_writer = tf.summary.create_file_writer(test_log_dir)

    # -----------------------------------------------------------------
    # Start the Training and Validation Process
    logging.info("Start the training process...")

    # setup checkpoints manager
    checkpoint = tf.train.Checkpoint(step=tf.Variable(1), optimizer=optimizer, model=model)
    manager = tf.train.CheckpointManager(
        checkpoint, directory="./checkpoints", max_to_keep=5
    )
    # restore from latest checkpoint and iteration
    status = checkpoint.restore(manager.latest_checkpoint)
    if manager.latest_checkpoint:
        logging.info("Restored from {}".format(manager.latest_checkpoint))
    else:
        logging.info("Initializing from scratch.")

    # COCO evalator for showing MAP
    coco_evaluator = coco_evaluation.CocoMaskEvaluator(_get_categories_list(FLAGS.label_map))

    best_val = 1e10
    iterations = checkpoint.step.numpy()

    for image, labels in train_dataset:
        # check iteration and change the learning rate
        if iterations > FLAGS.train_iter:
            break

        checkpoint.step.assign_add(1)
        iterations += 1
        with options({'constant_folding': True,
                      'layout_optimize': True,
                      'loop_optimization': True,
                      'arithmetic_optimization': True,
                      'remapping': True}):
            with tf.GradientTape() as tape:
                output = model(image, training=True)
                loc_loss, conf_loss, mask_loss, seg_loss, total_loss = criterion(output, labels, FLAGS.num_class+1)
            grads = tape.gradient(total_loss, model.trainable_variables)
            optimizer.apply_gradients(zip(grads, model.trainable_variables))
            train_loss.update_state(total_loss)

        loc.update_state(loc_loss)
        conf.update_state(conf_loss)
        mask.update_state(mask_loss)
        seg.update_state(seg_loss)
        with train_summary_writer.as_default():
            tf.summary.scalar('Total loss', train_loss.result(), step=iterations)
            tf.summary.scalar('Loc loss', loc.result(), step=iterations)
            tf.summary.scalar('Conf loss', conf.result(), step=iterations)
            tf.summary.scalar('Mask loss', mask.result(), step=iterations)
            tf.summary.scalar('Seg loss', seg.result(), step=iterations)

        if iterations and iterations % FLAGS.print_interval == 0:
            logging.info("Iteration {}, LR: {}, Total Loss: {}, B: {},  C: {}, M: {}, S:{} ".format(
                iterations,
                optimizer._decayed_lr(var_dtype=tf.float32),
                train_loss.result(), 
                loc.result(),
                conf.result(),
                mask.result(),
                seg.result()
            ))

        if iterations and iterations % FLAGS.save_interval == 0:
            # save checkpoint
            save_path = manager.save()
            logging.info("Saved checkpoint for step {}: {}".format(int(checkpoint.step), save_path))
            # validation
            valid_iter = 0
            for valid_image, valid_labels in valid_dataset:
                if valid_iter > FLAGS.valid_iter:
                    break
                # calculate validation loss
                with options({'constant_folding': True,
                              'layout_optimize': True,
                              'loop_optimization': True,
                              'arithmetic_optimization': True,
                              'remapping': True}):
                    output = model(valid_image, training=False)
                    valid_loc_loss, valid_conf_loss, valid_mask_loss, valid_seg_loss, valid_total_loss = criterion(output, valid_labels, FLAGS.num_class+1)
                    valid_loss.update_state(valid_total_loss)

                    _h = valid_image.shape[1]
                    _w = valid_image.shape[2]
                    
                    gt_num_box = valid_labels['num_obj'][0].numpy()
                    gt_boxes = valid_labels['boxes_norm'][0][:gt_num_box]
                    gt_boxes = gt_boxes.numpy()*np.array([_h,_w,_h,_w])
                    gt_classes = valid_labels['classes'][0][:gt_num_box].numpy()
                    gt_masks = valid_labels['mask_target'][0][:gt_num_box].numpy()

                    gt_masked_image = np.zeros((gt_num_box, _h, _w))
                    for _b in range(gt_num_box):
                        _mask = gt_masks[_b].astype("uint8")
                        _mask = cv2.resize(_mask, (_w, _h))
                        gt_masked_image[_b] = _mask

                    coco_evaluator.add_single_ground_truth_image_info(
                        image_id='image'+str(valid_iter),
                        groundtruth_dict={
                            standard_fields.InputDataFields.groundtruth_boxes: gt_boxes,
                            standard_fields.InputDataFields.groundtruth_classes: gt_classes,
                            standard_fields.InputDataFields.groundtruth_instance_masks: gt_masked_image
                        })

                    det_num = output['num_detections'][0].numpy()
                    det_boxes = output['detection_boxes'][0][:det_num]
                    det_boxes = det_boxes.numpy()*np.array([_h,_w,_h,_w])
                    det_masks = output['detection_masks'][0][:det_num].numpy()
                    det_masks = (det_masks > 0.5)

                    det_scores = output['detection_scores'][0][:det_num].numpy()
                    det_classes = output['detection_classes'][0][:det_num].numpy()

                    det_masked_image = np.zeros((det_num, _h, _w))
                    for _b in range(det_num):
                        _mask = det_masks[_b].astype("uint8")
                        _mask = cv2.resize(_mask, (_w, _h))
                        det_masked_image[_b] = _mask
                    
                    coco_evaluator.add_single_detected_image_info(
                        image_id='image'+str(valid_iter),
                        detections_dict={
                            standard_fields.DetectionResultFields.detection_boxes: det_boxes,
                            standard_fields.DetectionResultFields.detection_scores: det_scores,
                            standard_fields.DetectionResultFields.detection_classes: det_classes,
                            standard_fields.DetectionResultFields.detection_masks: det_masked_image
                        })

                v_loc.update_state(valid_loc_loss)
                v_conf.update_state(valid_conf_loss)
                v_mask.update_state(valid_mask_loss)
                v_seg.update_state(valid_seg_loss)
                valid_iter += 1

            metrics = coco_evaluator.evaluate()
            coco_evaluator.clear()

            with test_summary_writer.as_default():
                tf.summary.scalar('V Total loss', valid_loss.result(), step=iterations)
                tf.summary.scalar('V Loc loss', v_loc.result(), step=iterations)
                tf.summary.scalar('V Conf loss', v_conf.result(), step=iterations)
                tf.summary.scalar('V Mask loss', v_mask.result(), step=iterations)
                tf.summary.scalar('V Seg loss', v_seg.result(), step=iterations)

            train_template = 'Iteration {}, Train Loss: {}, Loc Loss: {},  Conf Loss: {}, Mask Loss: {}, Seg Loss: {}'
            valid_template = 'Iteration {}, Valid Loss: {}, V Loc Loss: {},  V Conf Loss: {}, V Mask Loss: {}, Seg Loss: {}'
            logging.info(train_template.format(iterations + 1,
                                        train_loss.result(),
                                        loc.result(),
                                        conf.result(),
                                        mask.result(),
                                        seg.result()))
            logging.info(valid_template.format(iterations + 1,
                                        valid_loss.result(),
                                        v_loc.result(),
                                        v_conf.result(),
                                        v_mask.result(),
                                        v_seg.result()))
            if valid_loss.result() < best_val:
                best_val = valid_loss.result()
                if FLAGS.tflite_export:
                  detection_module = YOLACTModule(model, True)
                  # Getting the concrete function traces the graph and forces variables to
                  # be constructed; only after this can we save the saved model.
                  concrete_function = detection_module.inference_fn.get_concrete_function(
                      tf.TensorSpec(
                          shape=[FLAGS.batch_size, FLAGS.img_h, FLAGS.img_w, 3], dtype=tf.float32, name='input'))

                  # Export SavedModel.
                  tf.saved_model.save(
                      detection_module,
                      './saved_models/saved_model_'+ str(valid_loss.result().numpy()),
                      signatures=concrete_function)
                else:
                  model.save('./saved_models/saved_model_'+ str(valid_loss.result().numpy()))

            # reset the metrics
            train_loss.reset_states()
            loc.reset_states()
            conf.reset_states()
            mask.reset_states()
            seg.reset_states()

            valid_loss.reset_states()
            v_loc.reset_states()
            v_conf.reset_states()
            v_mask.reset_states()
            v_seg.reset_states()


if __name__ == '__main__':
    app.run(main)
