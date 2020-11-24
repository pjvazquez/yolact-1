"""
Arthor: Vedanshu
"""

import tensorflow as tf
import time
from utils import utils


class YOLACTLoss(object):

    def __init__(self, loss_weight_cls=1,
                 loss_weight_box=1.5,
                 loss_weight_mask=6.125,
                 loss_seg=1,
                 neg_pos_ratio=3,
                 max_masks_for_train=100):
        self._loss_weight_cls = loss_weight_cls
        self._loss_weight_box = loss_weight_box
        self._loss_weight_mask = loss_weight_mask
        self._loss_weight_seg = loss_seg
        self._neg_pos_ratio = neg_pos_ratio
        self._max_masks_for_train = max_masks_for_train

    def __call__(self, pred, label, num_classes):
        """
        :param num_classes:
        :param anchors:
        :param label: labels dict from dataset
        :param pred:
        :return:
        """
        # all prediction component
        pred_cls = pred['pred_cls']
        pred_offset = pred['pred_offset']
        pred_mask_coef = pred['pred_mask_coef']
        proto_out = pred['proto_out']
        seg = pred['seg']

        # all label component
        # all_offsets: the transformed box coordinate offsets of each pair of prior and gt box
        # conf_gt: the foreground and background labels according to the 'pos_thre' and 'neg_thre',
        #          '0' means background, '>0' means foreground.
        # prior_max_box: the corresponding max IoU gt box for each prior
        # prior_max_index: the index of the corresponding max IoU gt box for each prior
        gt_offset = label['all_offsets']
        conf_gt = label['conf_gt']
        prior_max_box = label['prior_max_box']
        prior_max_index = label['prior_max_index']

        bbox_norm = label['bbox_for_norm']
        masks = label['mask_target']
        classes = label['classes']
        num_obj = label['num_obj']

        # calculate num_pos
        loc_loss = self._loss_location(pred_offset, gt_offset, conf_gt) * self._loss_weight_box
        conf_loss = self._loss_class(pred_cls, num_classes, conf_gt) * self._loss_weight_cls
        # conf_loss = self._focal_conf_sigmoid_loss(pred_cls, num_classes, conf_gt) * self._loss_weight_cls
        mask_loss = self._loss_mask(prior_max_index, pred_mask_coef, proto_out, masks, prior_max_box, conf_gt) * self._loss_weight_mask
        seg_loss = self._loss_semantic_segmentation(seg, masks, classes) * self._loss_weight_seg
        total_loss = loc_loss + conf_loss + mask_loss + seg_loss
        return loc_loss, conf_loss, mask_loss, seg_loss, total_loss

    def _loss_location(self, pred_offset, gt_offset, conf_gt):
        # only compute losses from positive samples
        # get postive indices
        pos_indices = tf.where(conf_gt > 0 )
        pred_offset = tf.gather_nd(pred_offset, pos_indices)
        gt_offset = tf.gather_nd(gt_offset, pos_indices)

        # calculate the smoothL1(positive_pred, positive_gt) and return
        num_pos = tf.shape(gt_offset)[0]
        smoothl1loss = tf.keras.losses.Huber(delta=1., reduction=tf.losses.Reduction.NONE)
        loss_loc = tf.reduce_sum(smoothl1loss(gt_offset, pred_offset)) #/ tf.cast(num_pos, tf.float32)

        return loss_loc

    def _focal_conf_sigmoid_loss(self, pred_cls, num_cls, conf_gt, focal_loss_alpha=0.75, focal_loss_gamma=2):
        """
        Focal loss but using sigmoid like the original paper.
        Note: To make things mesh easier, the network still predicts 81 class confidences in this mode.
              Because retinanet originally only predicts 80, we simply just don't use pred_cls[..., 0]
        """
        conf_gt = tf.reshape(conf_gt, -1) # [batch_size*num_priors]
        pred_cls = tf.reshape(pred_cls, [-1, num_cls]) # [batch_size*num_priors, num_classes]

        # Ignore neutral samples (class < 0)
        keep = tf.where(conf_gt >= 0 )
        neutrals_indices = tf.where(conf_gt < 0 )
        conf_gt = tf.tensor_scatter_nd_update(conf_gt, neutrals_indices, tf.zeros(tf.shape(neutrals_indices)[0], dtype=tf.int64)) # filter out neutrals (conf_gt = -1)

        # Compute a one-hot embedding of conf_gt
        conf_one_gt = tf.one_hot(conf_gt, depth=num_cls)
        conf_pm_gt  = conf_one_gt * 2 - 1 # -1 if background, +1 if forground for specific class

        logpt = tf.math.log_sigmoid(pred_cls * conf_pm_gt) # note: 1 - sigmoid(x) = sigmoid(-x)
        pt = tf.math.exp(logpt)

        at = focal_loss_alpha * conf_one_gt + (1 - focal_loss_alpha) * (1 - conf_one_gt)

        # Achieving following in tf
        # at[..., 0] = 0 # Set alpha for the background class to 0 because sigmoid focal loss doesn't use it
        ind = tf.stack([tf.range(tf.shape(at)[0]), tf.zeros(tf.shape(at)[0], dtype=tf.int32)], axis=1)
        at = tf.tensor_scatter_nd_update(at, ind, tf.zeros(tf.shape(ind)[0]))

        loss = -at * (1 - pt) ** focal_loss_gamma * logpt

        loss = tf.gather_nd(tf.reduce_sum(loss, -1), keep)

        return tf.reduce_sum(loss)

    def _loss_class(self, pred_cls, num_cls, conf_gt, ohem_use_most_confident=False):
        # num_cls includes background
        batch_conf = tf.reshape(pred_cls, [-1, num_cls])
        batch_conf_max = tf.math.reduce_max(pred_cls)

        # Hard Negative Mining
        if ohem_use_most_confident:
        # i.e. max(softmax) along classes > 0 
            batch_conf = tf.nn.softmax(batch_conf, axis=1)
            mark = tf.math.reduce_max(batch_conf[:, 1:], axis=1)
        else:
            # This will be used to determine unaveraged confidence loss across all examples in a batch.
            # https://github.com/dbolya/yolact/blob/b97e82d809e5e69dc628930070a44442fd23617a/layers/modules/multibox_loss.py#L251
            # https://github.com/dbolya/yolact/blob/b97e82d809e5e69dc628930070a44442fd23617a/layers/box_utils.py#L316
            mark = tf.math.log(tf.math.reduce_sum(tf.math.exp(batch_conf-batch_conf_max), 1)) + batch_conf_max - batch_conf[:,0]

        mark = tf.reshape(mark, (tf.shape(pred_cls)[0], -1))  # (batch_size, 27429)
        pos_indices = tf.where(conf_gt > 0 )
        mark = tf.tensor_scatter_nd_update(mark, pos_indices, tf.zeros(tf.shape(pos_indices)[0])) # filter out pos boxes
        num_pos = tf.math.count_nonzero(tf.greater(conf_gt,0), axis=1, keepdims=True)
        num_neg = tf.clip_by_value(num_pos * self._neg_pos_ratio, clip_value_min=tf.constant(1, dtype=tf.int64), clip_value_max=tf.cast(tf.shape(conf_gt)[1]-1, tf.int64))

        neutrals_indices = tf.where(conf_gt < 0 )
        mark = tf.tensor_scatter_nd_update(mark, neutrals_indices, tf.zeros(tf.shape(neutrals_indices)[0])) # filter out neutrals (conf_gt = -1)

        idx = tf.argsort(mark, axis=1, direction='DESCENDING')
        idx_rank = tf.argsort(idx, axis=1)

        # Just in case there aren't enough negatives, don't start using positives as negatives
        # Filter out neutrals and positive
        neg_indices = tf.where((tf.cast(idx_rank, dtype=tf.int64) < num_neg) & (conf_gt == 0))

        # neg_indices shape is (batch_size, no_prior)
        # pred_cls shape is (batch_size, no_prior, no_class)
        neg_pred_cls_for_loss = tf.gather_nd(pred_cls, neg_indices)
        neg_gt_for_loss = tf.gather_nd(conf_gt, neg_indices)
        pos_pred_cls_for_loss = tf.gather_nd(pred_cls, pos_indices)
        pos_gt_for_loss = tf.gather_nd(conf_gt, pos_indices)

        target_logits = tf.concat([pos_pred_cls_for_loss, neg_pred_cls_for_loss], axis=0)
        target_labels = tf.concat([pos_gt_for_loss, neg_gt_for_loss], axis=0)
        target_labels = tf.one_hot(tf.squeeze(target_labels), depth=num_cls)

        loss_conf = tf.reduce_sum(tf.nn.softmax_cross_entropy_with_logits(target_labels, target_logits)) #/ tf.cast(num_pos, tf.float32)

        # if loss_conf > 500:
        #     import pdb
        #     pdb.set_trace()
        return loss_conf

    def _loss_mask(self, prior_max_index, coef_p, proto_p, mask_gt, prior_max_box, conf_gt):
        shape_proto = tf.shape(proto_p)
        proto_h = shape_proto[1]
        proto_w = shape_proto[2]
        num_batch = shape_proto[0]
        loss_m = 0.0

        mask_gt = tf.transpose(mask_gt, (0,2,3,1)) #[batch, height, width, num_object]

        for i in tf.range(num_batch):
            pos_indices = tf.where(conf_gt[i] > 0 )
            _pos_prior_index = tf.gather_nd(prior_max_index[i], pos_indices) #shape: [num_positives]
            _pos_prior_box = tf.gather_nd(prior_max_box[i], pos_indices) #shape: [num_positives]
            _pos_coef = tf.gather_nd(coef_p[i], pos_indices) #shape: [num_positives]
            _mask_gt = mask_gt[i]

            if tf.shape(_pos_prior_index)[0] == 0: # num_positives are zero
                continue
            
            # If exceeds the number of masks for training, select a random subset
            old_num_pos = tf.shape(_pos_coef)[0]
            
            if old_num_pos > self._max_masks_for_train:
                perm = tf.random.shuffle(tf.range(tf.shape(_pos_coef)[0]))
                select = perm[:self._max_masks_for_train]
                _pos_coef = tf.gather(_pos_coef, select)
                _pos_prior_index = tf.gather(_pos_prior_index, select)
                _pos_prior_box = tf.gather(_pos_prior_box, select)
                
            num_pos = tf.shape(_pos_coef)[0]
            _pos_mask_gt = tf.gather(_mask_gt, _pos_prior_index, axis=-1)  
            
            # mask assembly by linear combination
            mask_p = tf.linalg.matmul(proto_p[i], _pos_coef, transpose_a=False, transpose_b=True) # [proto_height, proto_width, num_pos]
            mask_p = tf.math.sigmoid(mask_p)
            
            # crop the pred (not real crop, zero out the area outside the gt box)
            mask_p = utils.crop(mask_p, _pos_prior_box)  # _pos_prior_box.shape: (num_pos, 4)
            
            mask_loss = tf.keras.losses.binary_crossentropy(_pos_mask_gt, mask_p)
            # Normalize the mask loss to emulate roi pooling's effect on loss.
            pos_get_csize = utils.map_to_center_form(_pos_prior_box)
            _area = pos_get_csize[:, 2] * pos_get_csize[:, 3]
            mask_loss = tf.reduce_sum(mask_loss, [0, 1]) / _area
            
            if old_num_pos > num_pos:
                mask_loss *= tf.cast(old_num_pos / num_pos, tf.float32)

            loss_m += tf.reduce_sum(mask_loss)
            
        loss_m /= (tf.cast(proto_h, tf.float32) * tf.cast(proto_w, tf.float32))

        return loss_m

    def _loss_semantic_segmentation(self, pred_seg, mask_gt, classes):
        # Note num_classes here is without the background class so cfg.num_classes-1
        batch_size = tf.shape(pred_seg)[0]
        mask_h = tf.shape(pred_seg)[1]
        mask_w = tf.shape(pred_seg)[2]
        num_classes = tf.shape(pred_seg)[3]
        loss_s = 0.0

        for i in range(batch_size):
            cur_segment = pred_seg[i]
            cur_class_gt = classes[i]
            masks = mask_gt[i]

            masks = tf.expand_dims(masks, axis=-1)
            masks = tf.image.resize(masks, [mask_h, mask_w], method=tf.image.ResizeMethod.BILINEAR)
            masks = tf.cast(masks + 0.5, tf.int64)
            masks = tf.squeeze(tf.cast(masks, tf.float32))

            segment_gt = tf.zeros((mask_h, mask_w, num_classes+1)) # [height, width, num_cls]; num_cls including background
            segment_gt = tf.transpose(segment_gt, perm=(2, 0, 1))

            obj_cls = tf.expand_dims(cur_class_gt, axis=-1)
            segment_gt = tf.tensor_scatter_nd_max(segment_gt, indices=obj_cls, updates=masks)
            segment_gt = tf.transpose(segment_gt, perm=(1, 2, 0))

            loss_s += tf.reduce_sum(tf.nn.sigmoid_cross_entropy_with_logits(segment_gt[:,:,1:], cur_segment)) #exclude background from segment_gt

        return loss_s / tf.cast(mask_h, tf.float32) / tf.cast(mask_w, tf.float32)
