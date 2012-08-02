from celery import task, Task, registry
from celery.task import current

from urlannotator.flow_control import send_event
from urlannotator.classification.models import (TrainingSet,
    ClassifierPerformance)
from urlannotator.classification.factories import classifier_factory
from urlannotator.main.models import Sample, ClassifiedSample


@task()
class ClassifierTrainingManager(Task):
    """ Manage training of classifiers.
    """

    def __init__(self):
        self.samples = []

    def run(self, samples, *args, **kwargs):
        # FIXME: Mock
        # TODO: Make proper classifier management
        if samples:
            if isinstance(samples, int):
                samples = [samples]
            job = Sample.objects.get(id=samples[0]).job

            # If classifier is not trained, retry later
            if not job.is_classifier_trained():
                registry.tasks[ClassifierTrainingManager.name].retry(
                    countdown=3 * 60)

            classifier = classifier_factory.create_classifier(job.id)
            # train_samples = [train_sample.sample for train_sample in
            #     TrainingSet.objects.newest_for_job(job).training_samples.all()]
            # if train_samples:
            #     classifier.train(train_samples)
            samples_list = Sample.objects.filter(id__in=samples)
            for sample in samples_list:
                classifier.classify(sample)


add_samples = registry.tasks[ClassifierTrainingManager.name]


@task
def update_classified_sample(sample_id, *args, **kwargs):
    """
        Monitors sample creation and updates classify requests with this sample
        on match.
    """
    sample = Sample.objects.get(id=sample_id)
    ClassifiedSample.objects.filter(job=sample.job, url=sample.url,
        sample=None).update(sample=sample)
    classified = ClassifiedSample.objects.filter(
        job=sample.job,
        url=sample.url,
        sample=sample,
        label=''
    )
    for class_sample in classified:
        send_event("EventNewClassifySample", class_sample.id, 'update_classified')
    return None


@task
def train_on_set(set_id):
    """
        Trains classifier on newly created training set
    """
    training_set = TrainingSet.objects.get(id=set_id)
    job = training_set.job

    # If classifier hasn't been created, retry later
    if not job.is_classifier_created():
        train_on_set.retry(countdown=30)

    classifier = classifier_factory.create_classifier(job.id)

    samples = (training_sample
        for training_sample in training_set.training_samples.all())
    classifier.train(samples)


    # Gold samples created (since we are here), classifier created (checked).
    # Job has been fully initialized
    # TODO: Move to job.activate()?
    # send_event('EventNewJobInitializationCompleted')


@task
def classify(sample_id, from_name='', *args, **kwargs):
    """
        Classifies given samples
    """
    print 'Classifying sample from', from_name
    class_sample = ClassifiedSample.objects.get(id=sample_id)
    if class_sample.label:
        return

    job = class_sample.job

    # If classifier is not trained, retry later
    if not job.is_classifier_trained():
        current.retry(countdown=min(60 * 2 ** current.request.retries,
            60 * 60 * 24))

    classifier = classifier_factory.create_classifier(job.id)
    label = classifier.classify(class_sample.sample)
    ClassifierPerformance.objects.create(
        job=job,
        value=ClassifiedSample.objects.filter(job=job).count()
    )
    class_sample.label = label
    class_sample.save()


@task
def update_classifier_stats(*args, **kwargs):
    pass


FLOW_DEFINITIONS = [
    (r'^EventNewSample$', update_classified_sample),
    (r'^EventSamplesValidated$', add_samples),
    (r'^EventNewClassifySample$', classify),
    # (r'EventTrainClassifier', classify),
    (r'^EventTrainingSetCompleted$', train_on_set),
    (r'^EventClassifierTrained$', update_classifier_stats),
]
