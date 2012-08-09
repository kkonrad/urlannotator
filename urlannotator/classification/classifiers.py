import nltk
import httplib2
import os
import csv
import json

from apiclient.discovery import build
from boto.gs.connection import GSConnection
from boto.gs.bucket import Bucket
from django.conf import settings
from oauth2client.file import Storage
from boto.s3.key import Key

from urlannotator.classification.models import (Classifier as ClassifierModel,
    TrainingSet)
from urlannotator.tools.synchronization import RWSynchronize247


class Classifier(object):
    def train(self, samples=[], turn_off=True, set_id=0):
        pass

    def update(self, samples):
        pass

    def classify(self, sample):
        pass

    def classify_with_info(self, sample):
        pass


class Classifier247(Classifier):

    def __init__(self, reader_instance, writer_instance, entry_id, factory):
        """
        Our permanent (24/7) synchronize template can be initialized with
        instances of sync objects.
        """

        self.id = entry_id
        self.factory = factory

        self.sync247 = RWSynchronize247(template_name=str(entry_id))

    def update_self(self):
        """
        Loads subclassifiers data from the DB and returns correct classifiers.
        :rtype: A tuple (writer, reader)
        """
        entry = ClassifierModel.objects.get(id=self.id)
        id_reader = entry.parameters['reader']
        entry_reader = ClassifierModel.objects.get(id=id_reader)
        reader = self.factory.create_classifier_from_id(entry_reader.id)

        id_writer = entry.parameters['writer']
        entry_writer = ClassifierModel.objects.get(id=id_writer)
        writer = self.factory.create_classifier_from_id(entry_writer.id)

        return (writer, reader)

    def update_to_db(self, writer_id, reader_id):
        """
        Updates classifier entry in the DB.
        """
        entry = ClassifierModel.objects.get(id=self.id)
        entry.parameters['reader'] = reader_id
        entry.parameters['writer'] = writer_id
        entry.save()

    def train_lock(self, func, turn_off=True, *args, **kwargs):
        """
        Locks the classifier during training.
        """
        self.sync247.modified_lock()

        func(*args, **kwargs)
        writer, reader = self.update_self()

        self.sync247.modified_release(
            self.update_to_db,
            writer_id=reader.id,
            reader_id=writer.id,
        )

    def _train(self, samples=[], set_id=0):
        writer, reader = self.update_self()

        # Trains the subclassifier
        writer.train(
            samples=[],
            turn_off=False,
            set_id=set_id,
        )

    def train(self, samples=[], turn_off=True, set_id=0):
        self.train_lock(
            self._train,
            samples=samples,
            turn_off=turn_off,
            set_id=set_id,
        )

    def _update(self, samples=[], set_id=0):
        writer, reader = self.update_self()

        # Trains the subclassifier
        writer.update(
            samples=[],
            turn_off=False,
            set_id=set_id,
        )

    def update(self, samples=[], turn_off=True, set_id=0):
        self.train_lock(
            self._update,
            samples=samples,
            turn_off=False,
            set_id=set_id,
        )

    def classify_lock(self, func, *args, **kwargs):
        """
        Locks the classifier during classifying.
        """
        self.sync247.reader_lock()

        res = func(*args, **kwargs)

        self.sync247.reader_release()
        return res

    def _classify(self, sample):
        writer, reader = self.update_self()

        # Classifies the sample
        return reader.classify(sample)

    def classify(self, sample):
        return self.classify_lock(
            self._classify,
            sample=sample,
        )

    def _classify_with_info(self, sample):
        writer, reader = self.update_self()

        # Classifies the sample
        return reader.classify_with_info(sample)

    def classify_with_info(self, sample):
        return self.classify_lock(
            self._classify_with_info,
            sample=sample,
        )


class SimpleClassifier(Classifier):
    """
        Simple url classifier using Decision Tree.
        Model parameters:
            training_set - id of the set the was trained on.
    """

    def __init__(self, description, classes, *args, **kwargs):
        """
            Description and classes are not used by this classifier.
        """
        self.classifier = None
        super(SimpleClassifier, self).__init__(*args, **kwargs)

    @staticmethod
    def get_features(sample):
        """
            Creates a set of words the sample's text consists of.
        """
        if not hasattr(sample, 'text') and hasattr(sample, 'sample'):
            sample = sample.sample

        words = nltk.word_tokenize(sample.text)
        words = set(words)
        feature_set = {}
        for word in words:
            feature_set[word] = True
        return feature_set

    def train(self, samples=[], turn_off=True, set_id=0):
        """
            Trains classifier on gives samples' set. If sample has no label,
            it's checked for being a GoldSample.
        """
        entry = ClassifierModel.objects.get(id=self.id)
        job = entry.job
        if turn_off:
            job.unset_classifier_trained()

        if set_id:
            training_set = TrainingSet.objects.get(id=set_id)
            samples = training_set.training_samples.all()
            entry.parameters['training_set'] = set_id
            entry.save()

        train_set = []
        for sample in samples:
            job = sample.sample.job
            sample.sample.label = sample.label
            train_set.append((self.get_features(sample.sample), sample.label))

        if train_set:
            self.classifier = nltk.classify.DecisionTreeClassifier.train(
                train_set)

            job.set_classifier_trained()

    def classify(self, sample):
        """
            Classifies gives sample and saves result to the model.
        """
        if self.classifier is None:
            return None
        if not hasattr(sample, 'text'):
            sample = sample.sample
        label = self.classifier.classify(self.get_features(sample))

        entry = ClassifierModel.objects.get(id=self.id)
        train_set_id = entry.parameters['training_set']
        training_set = TrainingSet.objects.get(id=train_set_id)
        sample.training_set = training_set
        sample.label = label
        sample.label_probability = {'Yes': 0, 'No': 0}
        sample.save()
        return label

    def classify_with_info(self, sample):
        """
            Classifies given sample and returns more detailed data.
            Currently only label.
        """
        if self.classifier is None:
            return None
        label = self.classifier.classify(self.get_features(sample.sample))

        entry = ClassifierModel.objects.get(id=self.id)
        train_set_id = entry.parameters['training_set']
        training_set = TrainingSet.objects.get(id=train_set_id)
        sample.training_set = training_set

        sample.label = label
        sample.label_probability = {'Yes': 0, 'No': 0}
        sample.save()
        return {'label': label}

# Google Storage parameters used in GooglePrediction classifier
GOOGLE_STORAGE_PREFIX = 'gs'
GOOGLE_BUCKET_NAME = 'urlannotator'


class GooglePredictionClassifier(Classifier):
    """
        Classifier using Google Prediction API.
        Model parameters:
            model - name of the classification model in Google Prediction
            training - status of training. If not present, training is finished
            training_set - id of training set the classifier was trained on
    """

    def __init__(self, description, classes, *args, **kwargs):
        """
            Description and classes are not used by this classifier.
        """
        self.model = None
        storage = Storage('prediction.dat')
        credentials = storage.get()

        # Create an httplib2.Http object to handle our HTTP requests and
        # authorize it with our good Credentials.
        http = httplib2.Http()
        http = credentials.authorize(http)

        # Get access to the Prediction API.
        service = build("prediction", "v1.5", http=http)
        self.papi = service.trainedmodels()

    def analyze(self):
        try:
            status = self.papi.analyze(id=self.model).execute()
            return status
        except:
            return {}

    def get_train_status(self):
        try:
            status = self.papi.get(id=self.model).execute()
            return status['trainingStatus']
        except:
            # Model doesn't exist (first training).
            return 'RUNNING'

    def create_and_upload_training_data(self, samples):
        training_dir = 'urlannotator-training-data'
        file_name = 'model-%s' % self.model
        file_out = '%s/%s.csv' % (training_dir, file_name)

        # Lets create dir for temporary training sets.
        os.system("mkdir -p %s" % training_dir)

        # Write data to csv file
        data = open(file_out, 'wb')
        writer = csv.writer(data)
        for sample in samples:
            writer.writerow(['%s' % sample.label, '"%s"' % sample.text])
        data.close()

        # Upload file to gs
        training_file = open(file_out, 'r')

        con = GSConnection(settings.GS_ACCESS_KEY, settings.GS_SECRET)
        try:
            bucket = con.create_bucket(GOOGLE_BUCKET_NAME)
        except:
            # Bucket exists
            bucket = Bucket(connection=con, name=GOOGLE_BUCKET_NAME)

        key = Key(bucket)
        key.key = file_name
        key.set_contents_from_file(training_file)
        training_file.close()

        key.make_public()
        # Remove file from disc
        os.system('rm %s' % file_out)

        return file_name

    def train(self, samples=[], turn_off=True, set_id=0):
        """
            Trains classifier on gives samples' set. If sample has no label,
            it's checked for being a GoldSample. Required model
            is TrainingSample.
        """
        # Turns off classifier for the job. Can't be used until classification
        # is done.
        entry = ClassifierModel.objects.get(id=self.id)
        if turn_off:
            job = entry.job
            job.unset_classifier_trained()

        if set_id:
            training_set = TrainingSet.objects.get(id=set_id)
            samples = training_set.training_samples.all()
            entry.parameters['training_set'] = set_id

        train_set = []
        for sample in samples:
            sample.sample.label = sample.label
            train_set.append(sample.sample)

        name = self.create_and_upload_training_data(train_set)
        body = {
            'id': self.model,
            'storageDataLocation': 'urlannotator/%s' % name
        }

        # We have to always check for training status due to different
        # instances of the classifier in different threads. YET only one is
        # used at a time.
        try:
            status = self.papi.get(id=self.model).execute()
            if status['trainingStatus'] == 'DONE':
                self.papi.update(body=body).execute()
        except:
            # Model doesn't exist (first training).
            self.papi.insert(body=body).execute()

        # Update classifier entry.
        params = entry.parameters
        params['training'] = 'RUNNING'
        entry.parameters = json.dumps(params)
        entry.save()

    def classify(self, sample):
        """
            Classifies given sample and saves result to the model.
        """
        if self.model is None:
            return None

        body = {'input': {'csvInstance': [sample.sample.text]}}
        label = self.papi.predict(body=body, id=self.model).execute()

        entry = ClassifierModel.objects.get(id=self.id)
        train_set_id = entry.parameters['training_set']
        training_set = TrainingSet.objects.get(train_set_id)
        sample.training_set = training_set

        sample.label = label['outputLabel']
        sample.label_probability = label['outputMulti']
        sample.save()

        return label['outputLabel']

    def classify_with_info(self, sample):
        """
            Classifies given sample and returns more detailed data.
            Currently only label.
        """
        if self.model is None:
            return None

        body = {'input': {'csvInstance': [sample.sample.text]}}
        label = self.papi.predict(body=body, id=self.model).execute()

        entry = ClassifierModel.objects.get(id=self.id)
        train_set_id = entry.parameters['training_set']
        training_set = TrainingSet.objects.get(train_set_id)
        sample.training_set = training_set

        sample.label = label['outputLabel']
        sample.label_probability = label['outputMulti']
        sample.save()

        result = {
            'outputLabel': label['outputLabel'],
            'outputMulti': label['outputMulti'],
        }
        return result
