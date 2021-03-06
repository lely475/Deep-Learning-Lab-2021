import cv2
import numpy as np
import tensorflow as tf
import gin


@tf.custom_gradient
def guided_relu(x):
    """ guided relu gradient """
    y = tf.nn.relu(x)

    def grad(dy):
        pos_grads = tf.cast(dy > 0, "float32")  # only use positive derivatives (gradients)
        pos_filters = tf.cast(x > 0, "float32")  # only use positive conv filter values
        return pos_grads * pos_filters * dy

    return y, grad


@gin.configurable
def grad_cam_wbp(model, layer, ds, ds_info, timestamp, class_index, num_of_batches):
    """Runs guided grad cam on ds image batch.

    Parameters:
        model (keras.Model): (trained) keras model object
        layer (string): CNN layer to visualize
        ds (tf.data.Dataset): datasets with (image,label) pairs to run though trained model
        ds_info: dictionary containing information about the dataset
        timestamp (string): timestamp of eval log, to synchronize folder names
        class_index (int): class index (0 or 1) to run gbp for
        img_height (int): image pixel height
        img_width (int): image pixel width
        num_of_batches (int): amount of batches to run gbp for

    Returns:
        Nothing, images with heatmaps are stored in log folder
    """
    img_height = ds_info['img_height']
    img_width = ds_info['img_width']
    grad_model = tf.keras.models.Model([model.inputs], [model.get_layer(layer).output, model.output])

    # replace relu with custom gradient guided relu
    for layer in grad_model.layers[1:len(grad_model.layers) - 1]:
        if hasattr(layer, 'activation'):
            layer.activation = guided_relu

    # create summary writer for tensorboard logging
    path = "logs/eval/" + timestamp + "/gradcam"
    summary_writer = tf.summary.create_file_writer(path)

    #apply grad cam on num_of_batches batches from the ds
    for images, label in ds.take(num_of_batches):
        with tf.GradientTape() as tape:
            conv_outputs, predictions = grad_model(images, training=False)
            pred_labels = tf.math.argmax(predictions, axis=1, output_type=tf.dtypes.int32)
            class_prediction = predictions[:, class_index]

        guided_grads = tape.gradient(class_prediction, conv_outputs)  # guided backpropagation
        weights = tf.reduce_mean(guided_grads, axis=(1, 2))  # get weights for each filter

        # get grad cam output for each image in batch
        for j, batch_conv_layer in enumerate(conv_outputs):
            cam = tf.ones(batch_conv_layer.shape[0:2], dtype=tf.float32)

            # weight each filter output of conv layer with filter-weight
            for i, w in enumerate(weights[j, :]):
                cam += w * batch_conv_layer[:, :, i]

            # resize to image dimensions
            cam = tf.squeeze(tf.image.resize(tf.expand_dims(cam, 2), size=(img_height, img_width)))
            cam = tf.maximum(cam, 0)

            # normalization to get heatmap
            heatmap = (cam - tf.math.reduce_min(cam)) / (tf.math.reduce_max(cam) - tf.math.reduce_min(cam))

            # add heatmap to original image
            cam = cv2.applyColorMap(np.uint8(255 * np.array(heatmap)), cv2.COLORMAP_JET)
            output_image = cv2.addWeighted(
                cv2.cvtColor(np.array(tf.cast((images[j, :, :, :] * ds_info['train_std'] + ds_info['train_mean']) * 255, tf.uint8)),
                             cv2.COLOR_RGB2BGR), 0.6, cam, 0.4, 0)
            comp_image = cv2.cvtColor(np.array(tf.cast((images[j, :, :, :] * ds_info['train_std'] + ds_info['train_mean']) * 255, tf.uint8)),
                                      cv2.COLOR_RGB2BGR)

            #save images locally and log them to tensorboard
            cv2.imwrite(path + '/comp' + str(j) + '.png', comp_image)
            if int(label[j]) == class_index:
                cv2.imwrite(
                    path + '/gradcam' + str(j) + '_label_' + str(label[j].numpy()) + '_pred_' + str(
                        pred_labels[j].numpy()) + '.png', output_image)

                with summary_writer.as_default():
                    tf.summary.image(
                        'Comparison' + str(j), tf.expand_dims(cv2.cvtColor(comp_image, cv2.COLOR_BGR2RGB), axis=0),
                        step=0)
                    tf.summary.image('GradCam' + str(j) + ' for class: ' + str(label[j].numpy()) + ', pred: ' + str(
                        pred_labels[j].numpy()), tf.expand_dims(cv2.cvtColor(output_image, cv2.COLOR_BGR2RGB), axis=0),
                                     step=0)
