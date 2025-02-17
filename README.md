# YOLACT Real-time Instance Segmentation
This is a Tensorflow 2.3 implementation of the paper [YOLACT: Real-time Instance Segmentation](https://arxiv.org/abs/1904.02689). The paper presents a fully-convolutional model for real- time instance segmentation that achieves 29.8 mAP on MS COCO at 33.5 fps evaluated on a single Titan Xp, which is significantly faster than any previous competitive approach. In this Repo implements "ResNet50-FPN". Unlike original implemetation of YOLACT in which image is resized to 550x550, this repo can handle image of size MxN.  <br/>

# Installation
* Protobuf 3.0.0
* Tensorflow (>=2.3.0)
* cocoapi
* OpenCV

For detailed steps to install Tensorflow, follow the [Tensorflow installation instructions](https://www.tensorflow.org/install/). A typical user can install Tensorflow using one of the following commands:

## For CPU
```
pip install tensorflow==2.3
```
## For GPU
```
pip install tensorflow-gpu==2.3
```

The remaining libraries can be installed on Ubuntu 16.04 using via apt-get:
```
sudo apt-get install protobuf-compiler
```

## COCO API installation
Download the
[cocoapi](https://github.com/cocodataset/cocoapi). The default metrics are
based on those used in Pascal VOC evaluation.

```bash
git clone https://github.com/cocodataset/cocoapi.git
cd cocoapi/PythonAPI
make
make install
```

## Protobuf Compilation

Protobufs is used to configure model and training parameters. Before the framework can be used, the Protobuf libraries
must be compiled. This should be done by running the following command from
the yolact/ directory:


``` bash
# From yolact/
protoc protos/*.proto --python_out=.
```

**Note**: If you're getting errors while compiling, you might be using an incompatible protobuf compiler. If that's the case, use the following manual installation

## Manual protobuf-compiler installation and usage

**If you are on linux:**

Download and install the 3.0 release of protoc, then unzip the file.

```bash
# From yolact/
wget -O protobuf.zip https://github.com/google/protobuf/releases/download/v3.0.0/protoc-3.0.0-linux-x86_64.zip
unzip protobuf.zip
```

Run the compilation process again, but use the downloaded version of protoc

```bash
# From yolact/
./bin/protoc protos/*.proto --python_out=.
```

**If you are on MacOS:**

If you have homebrew, download and install the protobuf with
```brew install protobuf```

Alternately, run:
```
PROTOC_ZIP=protoc-3.3.0-osx-x86_64.zip
curl -OL https://github.com/google/protobuf/releases/download/v3.3.0/$PROTOC_ZIP
sudo unzip -o $PROTOC_ZIP -d /usr/local bin/protoc
rm -f $PROTOC_ZIP
```

Run the compilation process again:

``` bash
# From yolact/
protoc protos/*.proto --python_out=.
```

## Create TFRecord for training 
Refer to the tensorflow object detection api for tfrecord creation. ([link](https://github.com/tensorflow/models/blob/master/research/object_detection/g3doc/using_your_own_dataset.md))


# Train
### (1) Label Maps
Each dataset is required to have a label map associated with it. This label map defines a mapping from string class names to integer class Ids. The label map should be a StringIntLabelMap text protobuf. Sample label maps can be found in object_detection/data. Label maps should always start from id 1. For an example:
```
item {
  id: 1
  name: 'Cat'
}


item {
  id: 2
  name: 'Dog'
}
```

### (2) Dataset Requirements
For every example in your dataset, you should have the following information:

1. An RGB image for the dataset encoded as jpeg or png.
2. A list of bounding boxes for the image. Each bounding box should contain:
    1. A bounding box coordinates (with origin in top left corner) defined by 4
       floating point numbers [ymin, xmin, ymax, xmax]. Note that we store the
       _normalized_ coordinates (x / width, y / height) in the TFRecord dataset.
    2. The class id of the object in the bounding box.
3. PNG encoded mask for every groundtruth bounding box. Each mask has only a single channel, and the pixel values are either 0 (background) or 1 (object mask). 

### (3) Usage
Training procedure can be conducted directly by following command:
```
python train.py -tfrecord_dir 'path of TFRecord files'
				        -label_map 'path label_map.pbtxt'
                -weights 'path to store weights' 
                -train_iter 'number of  iteration for training'
                -img_h 'image height'
                -img_w 'image width'
                -num_class 'No of classes excluding background'
                -aspect_ratio 'aspect ratio for anchors'
                -scale 'scales in pixels for anchors '
                -batch_size 'batch_size'
                -lr 'learning rate'
                -momentum 'momentum for SGD'
                -weight_decay 'weight_decay rate for SGD'
                -print_interval 'interval for printing training result'
                -save_interval 'interval for conducting validation'
                -valid_iter 'number of iteration for validation'
```
A sample file is given in `train.sh`

# Inference 
Inside `saved_models` there will be saved graphs according to the score of their validation. For an example `saved_model_0.19968511` is saved_model when the validation loss was 0.19968511. To run inference on using this saved_model see `infer.py`.

# Reference
* https://github.com/feiyuhuahuo/Yolact_minimal
* https://github.com/dbolya/yolact
* https://github.com/leohsuofnthu/Tensorflow-YOLACT/
