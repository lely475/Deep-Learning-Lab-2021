import gin
import tensorflow as tf
import logging
import datetime
from evaluation.metrics import ConfusionMatrix, Accuracy, Sensitivity, Specificity, F1Score, RocAuc
from input_pipeline.visualize import plot_confusion_matrix, plot_to_image
from shutil import copyfile

@gin.configurable
class Trainer(object):
    def __init__(self, model, ds_train, ds_val, ds_test, ds_info, run_paths, total_steps, log_interval, ckpt_interval):
        # Summary Writer
        self.timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.summary_writer = tf.summary.create_file_writer("logs/train/"+self.timestamp)

        # Loss objective
        self.loss_object = tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True)
        self.optimizer = tf.keras.optimizers.Adam()

        # Checkpoint Manager
        self.ckpt = tf.train.Checkpoint(step=tf.Variable(1),net=model,optimizer=self.optimizer, iterator=iter(ds_train)) #
        self.manager = tf.train.CheckpointManager(self.ckpt, './tf_ckpts/'+self.timestamp, max_to_keep=50)

        # Metrics
        self.train_loss = tf.keras.metrics.Mean(name='train_loss')
        self.train_accuracy = tf.keras.metrics.SparseCategoricalAccuracy(name='train_accuracy')
        self.train_confusion_matrix = ConfusionMatrix()
        self.train_sensitivity = Sensitivity()
        self.train_specificity = Specificity()
        self.train_f1_score = F1Score()
        self.train_roc_auc = RocAuc()

        self.test_loss = tf.keras.metrics.Mean(name='test_loss')
        self.test_accuracy = tf.keras.metrics.SparseCategoricalAccuracy(name='test_accuracy')
        self.test_confusion_matrix = ConfusionMatrix()
        self.test_sensitivity = Sensitivity()
        self.test_specificity = Specificity()
        self.test_f1_score = F1Score()
        self.test_roc_auc = RocAuc()

        self.eval_loss = tf.keras.metrics.Mean(name='test_loss')
        self.eval_accuracy = tf.keras.metrics.SparseCategoricalAccuracy(name='test_accuracy')
        self.eval_confusion_matrix = ConfusionMatrix()
        self.eval_sensitivity = Sensitivity()
        self.eval_specificity = Specificity()
        self.eval_f1_score = F1Score()
        self.eval_roc_auc = RocAuc()

        self.model = model
        self.ds_train = ds_train
        self.ds_val = ds_val
        self.ds_test = ds_test
        self.ds_info = ds_info
        self.run_paths = run_paths
        self.total_steps = total_steps
        self.log_interval = log_interval
        self.ckpt_interval = ckpt_interval

    @tf.function
    def train_step(self, images, labels):
        with tf.GradientTape() as tape:
            # training=True is only needed if there are layers with different
            # behavior during training versus inference (e.g. Dropout).
            predictions = self.model(images, training=True)
            loss = self.loss_object(labels, predictions)
        gradients = tape.gradient(loss, self.model.trainable_variables)
        self.optimizer.apply_gradients(zip(gradients, self.model.trainable_variables))

        self.train_loss(loss)
        self.train_accuracy(labels, predictions)
        self.train_confusion_matrix.update_state(labels, predictions)
        self.train_sensitivity.update_state(labels, predictions)
        self.train_specificity.update_state(labels, predictions)
        self.train_f1_score.update_state(labels, predictions)
        self.train_roc_auc.update_state(labels, predictions)

    @tf.function
    def test_step(self, images, labels):
        # training=False is only needed if there are layers with different
        # behavior during training versus inference (e.g. Dropout).
        predictions = self.model(images, training=False)
        t_loss = self.loss_object(labels, predictions)

        self.test_loss(t_loss)
        self.test_accuracy(labels, predictions)
        self.test_confusion_matrix.update_state(labels, predictions)
        self.test_sensitivity.update_state(labels, predictions)
        self.test_specificity.update_state(labels, predictions)
        self.test_f1_score.update_state(labels, predictions)
        self.test_roc_auc.update_state(labels, predictions)

    @tf.function
    def eval_step(self, images, labels):
        # training=False is only needed if there are layers with different
        # behavior during training versus inference (e.g. Dropout).
        predictions = self.model(images, training=False)
        t_loss = self.loss_object(labels, predictions)

        self.eval_loss(t_loss)
        self.eval_accuracy(labels, predictions)
        self.eval_confusion_matrix.update_state(labels, predictions)
        self.eval_sensitivity.update_state(labels, predictions)
        self.eval_specificity.update_state(labels, predictions)
        self.eval_f1_score.update_state(labels, predictions)
        self.eval_roc_auc.update_state(labels, predictions)

    def train(self):

        self.ckpt.restore(self.manager.latest_checkpoint)
        if self.manager.latest_checkpoint:
            logging.info("Restored from {}".format(self.manager.latest_checkpoint))
        else:
            logging.info("Initializing from scratch.")

        for idx, (images, labels) in enumerate(self.ds_train):

            step = idx + 1
            self.train_step(images, labels)

            if step % self.log_interval == 0:

                # Reset test metrics
                self.test_loss.reset_states()
                self.test_accuracy.reset_states()
                self.test_confusion_matrix.reset_states()
                self.test_sensitivity.reset_states()
                self.test_specificity.reset_states()
                self.test_f1_score.reset_states()
                self.test_roc_auc.reset_states()

                self.eval_loss.reset_states()
                self.eval_accuracy.reset_states()
                self.eval_confusion_matrix.reset_states()
                self.eval_sensitivity.reset_states()
                self.eval_specificity.reset_states()
                self.eval_f1_score.reset_states()
                self.eval_roc_auc.reset_states()

                for test_images, test_labels in self.ds_val:
                    self.test_step(test_images, test_labels)

                for eval_images, eval_labels in self.ds_test:
                    self.eval_step(eval_images, eval_labels)

                # ROC AUC: {},, Test ROC AUC: {}
                template = 'Step {}, Accuracy: {},Confusion Matrix: {}, Sensitivity: {}, Specificity: {}, ' \
                           '\n Test Accuracy: {}, Test Confusion Matrix: {}, Test Sensitivity: {}, Test Specificity: {},' \
                           ' \n Eval Accuracy: {}, Eval Confusion Matrix: {}, Eval Sensitivity: {}, Eval Specificity: {}'
                # template = 'Step {}, Loss: {}, Accuracy: {},Confusion Matrix: {}, Sensitivity: {}, Specificity: {}, ' \
                       #    'F1 Score: {}, \n Test Loss: {}, Test Accuracy: {}, Test Confusion Matrix: {}, ' \
                       #    'Test Sensitivity: {}, Test Specificity: {}, Test F1 Score: {} \n Eval Loss: {}, ' \
                       #    'Eval Accuracy: {}, Eval Confusion Matrix: {}, Eval Sensitivity: {}, Eval Specificity: {}, ' \
                       #    'Eval F1 Score: {}'
                logging.info(template.format(
                                            step,
                                            #self.train_loss.result(),
                                            self.train_accuracy.result() * 100,
                                            self.train_confusion_matrix.result(),
                                            self.train_sensitivity.result()*100,
                                            self.train_specificity.result()*100,
                                           # self.train_f1_score.result()*100,
                                            #self.train_roc_auc.result(),

                                            #self.test_loss.result(),
                                            self.test_accuracy.result() * 100,
                                            self.test_confusion_matrix.result(),
                                            self.test_sensitivity.result() * 100,
                                            self.test_specificity.result() * 100, #,self.test_roc_auc.result()
                                            #self.test_f1_score.result() * 100,

                                            #self.eval_loss.result(),
                                            self.eval_accuracy.result() * 100,
                                            self.eval_confusion_matrix.result(),
                                            self.eval_sensitivity.result() * 100,
                                            self.eval_specificity.result() * 100))  # ,self.test_roc_auc.result()
                                            #self.eval_f1_score.result() * 100))

                # Write summary to tensorboard
                with self.summary_writer.as_default():
                    tf.summary.scalar('Train loss', self.train_loss.result(), step=step)
                    tf.summary.scalar('Train accuracy', self.train_accuracy.result(), step=step)
                    #tf.summary.image('Train Confusion Matrix',
                    #                 plot_to_image(plot_confusion_matrix(self.train_confusion_matrix.result(),
                    #                                                     class_names=['1', '0'])),
                    #                 step=step)
                    tf.summary.scalar('Train sensitivity', self.train_sensitivity.result(), step=step)
                    tf.summary.scalar('Train specificity', self.train_specificity.result(), step=step)
                    tf.summary.scalar('Train F1 Score', self.train_f1_score.result(), step=step)
                    tf.summary.scalar('Train ROC AUC', self.train_roc_auc.result(), step=step)

                    tf.summary.scalar('Test loss', self.test_loss.result(), step=step)
                    tf.summary.scalar('Test accuracy', self.test_accuracy.result(), step=step)
                    #tf.summary.image('Test Confusion Matrix',
                    #                 plot_to_image(plot_confusion_matrix(self.test_confusion_matrix.result(),
                    #                                                     class_names=['1', '0'])),
                    #                 step=step)
                    tf.summary.scalar('Test sensitivity', self.test_sensitivity.result(), step=step)
                    tf.summary.scalar('Test specificity', self.test_specificity.result(), step=step)
                    tf.summary.scalar('Test F1 Score', self.test_f1_score.result(), step=step)
                    tf.summary.scalar('Test ROC AUC', self.test_roc_auc.result(), step=step)

                # Reset train metrics
                self.train_loss.reset_states()
                self.train_accuracy.reset_states()
                self.train_confusion_matrix.reset_states()
                self.train_sensitivity.reset_states()
                self.train_specificity.reset_states()
                self.train_f1_score.reset_states()
                self.train_roc_auc.reset_states()

                yield self.test_accuracy.result().numpy(), self.eval_accuracy.result().numpy()#, eval_accuracy = self.eval_accuracy.result().numpy())

            # Save checkpoint
            if step % self.ckpt_interval == 0:
                save_path = self.manager.save()
                #logging.info(f'Saving checkpoint to /tf_cpkt/{self.timestamp}.')

            if step % self.total_steps == 0:
                logging.info(f'Finished training after {step} steps.')
                # Save final checkpoint
                save_path = self.manager.save()
                # log current config file
                copyfile('./configs/config.gin', './logs/train/'+self.timestamp+'/config.gin')
                return self.test_accuracy.result().numpy(), self.eval_accuracy.result().numpy() #eval_accuracy