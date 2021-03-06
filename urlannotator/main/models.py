import math
import json
import datetime
import hashlib
import urlparse

from django.db import models
from django.db.models import F, Sum
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils.timezone import now
from django.utils.http import urlencode
from django.conf import settings
from django.core.urlresolvers import reverse
from django.template.loader import get_template
from django.template import Context
from itertools import ifilter
from tenclouds.django.jsonfield.fields import JSONField

from urlannotator.flow_control import send_event
from urlannotator.tools.synchronization import POSIXLock
from urlannotator.tools.utils import cached
from urlannotator.settings import imagescale2
from urlannotator.crowdsourcing.tagasauris_helper import (stop_job,
    make_tagapi_client, get_gather_cost, get_vote_cost)

import logging
log = logging.getLogger(__name__)

LABEL_BROKEN = 'Broken'
LABEL_YES = 'Yes'
LABEL_NO = 'No'
LABEL_CHOICES = (
    (LABEL_NO, 'No'),
    (LABEL_YES, 'Yes'),
    (LABEL_BROKEN, 'Broken')
)


def make_label(to_check):
    '''
        Transforms the `to_check` string into a valid label used by the system.
        Returns a valid label if transform succeeded. None otherwise.
    '''
    try:
        to_check = to_check.capitalize()
        if to_check == LABEL_BROKEN.capitalize():
            return LABEL_BROKEN

        if to_check == LABEL_YES.capitalize():
            return LABEL_YES

        if to_check == LABEL_NO.capitalize():
            return LABEL_NO
    except:
        log.exception('Label transformation failed for argument %s.' % to_check)
    return None


class Account(models.Model):
    """
        Model representing additional user data. Used as user profile.
    """
    user = models.OneToOneField(User)
    activation_key = models.CharField(default='', max_length=100)
    email_registered = models.BooleanField(default=False)
    odesk_token = models.CharField(default='', max_length=100)
    odesk_secret = models.CharField(default='', max_length=100)
    odesk_uid = models.CharField(default='', max_length=100)
    odesk_id = models.CharField(default='', max_length=100)
    odesk_teams = JSONField(default='{}')
    full_name = models.CharField(default='', max_length=100)
    alerts = models.BooleanField(default=False)
    worker_entry = models.OneToOneField('Worker', null=True, blank=True)
    job_limits = JSONField(default=json.dumps({
        'max_jobs': settings.USER_MAX_JOBS,
        'max_urls_per_job': settings.USER_MAX_URLS_PER_JOB,
    }))


def create_user_profile(sender, instance, created, raw, **kwargs):
    if created and not raw:
        Account.objects.create(user=instance)

post_save.connect(create_user_profile, sender=User)

# Job source types
JOB_SOURCE_ODESK_FREE = 0
JOB_SOURCE_OWN_WORKFORCE = 1
JOB_SOURCE_ODESK_PAID = 2
JOB_SOURCE_MTURK_WORKFORCE = 3

JOB_BASIC_DATA_SOURCE_CHOICES = (
    (JOB_SOURCE_OWN_WORKFORCE, 'Own workforce'),
    (JOB_SOURCE_MTURK_WORKFORCE, 'Amazon Mechanical Turk'),
)
JOB_ODESK_DATA_SOURCE_CHOICES = (
    (JOB_SOURCE_ODESK_FREE, 'Odesk free'),
    (JOB_SOURCE_ODESK_PAID, 'Odesk paid')
)

JOB_DATA_SOURCE_CHOICES = JOB_BASIC_DATA_SOURCE_CHOICES
JOB_DATA_SOURCE_CHOICES_DICT = dict(JOB_DATA_SOURCE_CHOICES)
JOB_TYPE_CHOICES = ((0, 'Fixed no. of URLs to collect'), (1, 'Fixed price'))

JOB_FREE_SOURCES = (JOB_SOURCE_OWN_WORKFORCE,)
JOB_HIT_MAPPING_NAME = 'job_source_to_hit_type'
# Job status breakdown:
# Draft - template of a job, not active yet, can be started.
# Active - up and running job.
# Completed - job has reached it's goal. Possible BTM still running.
# Stopped - job has been stopped by it's owner.
# Initializing - job has been just created by user, awaiting initialization of
#                elements. An active job must've gone through this step.
#                Drafts DO NOT get this status.
JOB_STATUS_DRAFT = 0
JOB_STATUS_ACTIVE = 1
JOB_STATUS_COMPLETED = 2
JOB_STATUS_STOPPED = 3
JOB_STATUS_INIT = 4

JOB_STATUS_CHOICES = (
    (JOB_STATUS_DRAFT, 'Draft'),
    (JOB_STATUS_ACTIVE, 'Active'),
    (JOB_STATUS_COMPLETED, 'Completed'),
    (JOB_STATUS_STOPPED, 'Stopped'),
    (JOB_STATUS_INIT, 'Initializing')
)


class JobManager(models.Manager):
    def create_active(self, **kwargs):
        kwargs['status'] = 4
        kwargs['remaining_urls'] = kwargs.get('no_of_urls', 0)
        job = self.create(**kwargs)
        send_event('EventNewJobInitialization',
            job_id=job.id)
        return job

    def create_draft(self, **kwargs):
        kwargs['status'] = 0
        kwargs['remaining_urls'] = kwargs.get('no_of_urls', 0)
        return self.create(**kwargs)

    def get_active(self, **kwargs):
        els = super(JobManager, self).get_query_set().filter(status=1)
        return els


class Job(models.Model):
    """
        Model representing actual project that is start by user, and consists
        of gathering, verifying and classifying samples.
    """
    class BTMStatus:
        # Not activated
        NOT_ACTIVE = 'not_active'

        # Up and running
        ACTIVE = 'active'

        # Payment pending
        PENDING = 'pending'

        # Finished
        FINISHED = 'finished'

        # Stopped
        STOPPED = 'stopped'

    # Job initialization progress flags. Set flags means the step is done.
    class Flags:
        TRAINING_SET_CREATED = 1  # Training set creation
        GOLD_SAMPLES_DONE = 2  # Gold samples have been extracted
        CLASSIFIER_CREATED = 4  # Classifier has been created
        CLASSIFIER_TRAINED = 8  # Classifier has been trained

        ALL = TRAINING_SET_CREATED + GOLD_SAMPLES_DONE + CLASSIFIER_CREATED \
            + CLASSIFIER_TRAINED
        ACTIVE = TRAINING_SET_CREATED + GOLD_SAMPLES_DONE

    BTMSTATUS_CHOICES = (
        (BTMStatus.NOT_ACTIVE, 'Not active'),
        (BTMStatus.PENDING, 'Pending'),
        (BTMStatus.ACTIVE, 'Active'),
        (BTMStatus.FINISHED, 'Finished'),
        (BTMStatus.STOPPED, 'Stopped'),
    )

    account = models.ForeignKey(Account)
    title = models.CharField(max_length=100, default='test')
    description = models.TextField()
    status = models.IntegerField(default=0, choices=JOB_STATUS_CHOICES)
    progress = models.IntegerField(default=0)
    no_of_urls = models.PositiveIntegerField(default=1)
    data_source = models.IntegerField(default=1,
        choices=JOB_DATA_SOURCE_CHOICES)
    project_type = models.IntegerField(default=0, choices=JOB_TYPE_CHOICES)
    same_domain_allowed = models.PositiveIntegerField(default=0)
    hourly_rate = models.DecimalField(default=0, decimal_places=2,
        max_digits=10)
    gold_samples = JSONField(default='[]')
    gold_left = models.PositiveIntegerField(default=0)
    classify_urls = JSONField(default='[]')
    budget = models.DecimalField(default=0, decimal_places=2, max_digits=10)
    remaining_urls = models.PositiveIntegerField(default=0)
    collected_urls = models.PositiveIntegerField(default=0)
    initialization_status = models.IntegerField(default=0)
    activated = models.DateTimeField(auto_now_add=True)
    votes_storage = models.CharField(max_length=50)
    quality_algorithm = models.CharField(max_length=50)
    btm_status = models.CharField(max_length=50, default=BTMStatus.NOT_ACTIVE,
        choices=BTMSTATUS_CHOICES)
    btm_to_gather = models.PositiveIntegerField(default=0)
    btm_points_to_cash = models.PositiveIntegerField(default=1)
    btm_title = models.CharField(max_length=250, default='')
    btm_description = models.TextField(default='')
    add_filler_samples = models.BooleanField(default=False)

    objects = JobManager()

    @classmethod
    def is_paid_source(cls, data_source):
        return not data_source in JOB_FREE_SOURCES

    @classmethod
    def estimate_cost(cls, data_source, no_of_urls):
        estimation = 0
        if not Job.is_paid_source(data_source):
            estimation = 0

        if data_source == JOB_SOURCE_MTURK_WORKFORCE:
            # Only tagasauris cost.
            gather_cost = get_gather_cost(no_of_urls)
            vote_cost = get_vote_cost(no_of_urls)

            estimation = gather_cost + vote_cost

        return round(estimation, 2)

    @classmethod
    def btm_estimate_cost(cls, no_of_urls, pts_per_dollars):
        from urlannotator.crowdsourcing.models import BeatTheMachineSample
        gather_cost = get_gather_cost(no_of_urls)
        vote_cost = get_vote_cost(no_of_urls)
        # Maximize costs (most pesimistic case)
        bonus_cost = BeatTheMachineSample.BTM_REWARD_4 * no_of_urls \
            / pts_per_dollars

        estimation = gather_cost + vote_cost + bonus_cost
        return round(estimation, 2)

    @classmethod
    def job_source_to_hit(cls, source):
        if not hasattr(cls, JOB_HIT_MAPPING_NAME):
            setattr(cls, JOB_HIT_MAPPING_NAME,
            {
                JOB_SOURCE_ODESK_FREE: settings.ODESK_HIT_TYPE,
                JOB_SOURCE_ODESK_PAID: settings.ODESK_HIT_TYPE,
                JOB_SOURCE_OWN_WORKFORCE: settings.OWN_WORKFORCE_HIT_TYPE,
                JOB_SOURCE_MTURK_WORKFORCE: settings.MTURK_WORKFORCE_HIT_TYPE,
            })
        return getattr(cls, JOB_HIT_MAPPING_NAME)[source]

    def get_data_source(self):
        return JOB_DATA_SOURCE_CHOICES_DICT[self.data_source]

    def get_hit_type(self):
        """
            Returns a Tagasauris HIT type according to job's source
        """
        return self.job_source_to_hit(self.data_source)

    @models.permalink
    def get_absolute_url(self):
        return ('project_view', (), {
            'id': self.id,
        })

    @cached
    def _get_confusion_matrix(self, cache):
        val = self.classifierperformance_set.order_by('-id')
        matrix = {
            LABEL_YES: {LABEL_YES: 0.0, LABEL_NO: 0.0},
            LABEL_NO: {LABEL_YES: 0.0, LABEL_NO: 0.0}
        }
        if not val:
            return matrix

        matrix = val[0].value.get('matrix', matrix)
        return matrix

    def get_confusion_matrix(self, cache=True):
        cache_key = 'job-%d-confusion-matrix' % self.id
        return self._get_confusion_matrix(cache_key=cache_key, cache=cache)

    @cached
    def _get_progress_stats(self, cache):
        from urlannotator.statistics.stat_extraction import extract_progress_stats
        cont = {}
        extract_progress_stats(self, cont)
        return cont

    def get_progress_stats(self, cache=True):
        key = 'job-%d-progress-stats'
        return self._get_progress_stats(cache_key=key, cache=cache)

    @cached
    def _get_urls_stats(self, cache):
        from urlannotator.statistics.stat_extraction import extract_url_stats
        cont = {}
        extract_url_stats(self, cont)
        return cont

    def get_urls_stats(self, cache=True):
        key = 'job-%d-urls-stats' % self.id
        return self._get_urls_stats(cache_key=key, cache=cache)

    @cached
    def _get_spent_stats(self, cache):
        from urlannotator.statistics.stat_extraction import extract_spent_stats
        cont = {}
        extract_spent_stats(self, cont)
        return cont

    def get_spent_stats(self, cache=True):
        key = 'job-%d-spent-stats' % self.id
        return self._get_spent_stats(cache_key=key, cache=cache)

    @cached
    def _get_performance_stats(self, cache):
        from urlannotator.statistics.stat_extraction import extract_performance_stats
        cont = {}
        extract_performance_stats(self, cont)
        return cont

    def get_performance_stats(self, cache=True):
        key = 'job-%d-performance-stats' % self.id
        return self._get_performance_stats(cache_key=key, cache=cache)

    @cached
    def _get_votes_stats(self, cache):
        from urlannotator.statistics.stat_extraction import extract_votes_stats
        cont = {}
        extract_votes_stats(self, cont)
        return cont

    def get_votes_stats(self, cache=True):
        key = 'job-%d-votes-stats' % self.id
        return self._get_votes_stats(cache_key=key, cache=cache)

    def update_cache(self):
        """
            Forces cache recalculation.
        """
        self.get_hours_spent(cache=False)
        self.get_progress(cache=False)
        self.get_top_workers(cache=False)
        self.get_newest_votes(cache=False)
        self.get_votes_stats(cache=False)
        self.get_performance_stats(cache=False)
        self.get_spent_stats(cache=False)
        self.get_urls_stats(cache=False)
        self.get_progress_stats(cache=False)
        self.get_display_samples(cache=False)
        self.get_confusion_matrix(cache=False)
        self.get_urls_collected(cache=False)
        self.get_btm_votes(cache=False)

    def recreate_training_set(self, force=False):
        """
            Recreates a training set from quality algorithm and trains
            classifier on it.
        """
        from urlannotator.crowdsourcing.factories import quality_factory
        from urlannotator.classification.models import (TrainingSet,
            TrainingSample)

        if not self.has_new_votes() and not force:
            return

        quality_algorithm = quality_factory.create_algorithm(self)
        decisions = quality_algorithm.extract_decisions()
        if not decisions:
            return

        ts = TrainingSet.objects.create(job=self)
        for sample_id, label in decisions:
            if label == LABEL_BROKEN:
                log.info(
                    'Job %d: Skipped broken training sample %d.' % (self.id, sample_id)
                )
                continue
            sample = Sample.objects.get(id=sample_id)
            log.info(
                'Job %d: Added training sample %d %s.' % (self.id, sample_id, label)
            )
            TrainingSample.objects.create(
                set=ts,
                sample=sample,
                label=label,
            )

        for sample in self.sample_set.all().iterator():
            if sample.is_gold_sample():
                ts_sample, created = TrainingSample.objects.get_or_create(
                    set=ts,
                    sample=sample,
                )
                if not created:
                    log.info(
                        'Job %d: Overriden gold sample %d.' % (self.id, sample.id)
                    )
                ts_sample.label = sample.goldsample.label
                ts_sample.save()

        send_event(
            'EventTrainingSetCompleted',
            set_id=ts.id,
            job_id=self.id,
        )

    @cached
    def _get_display_samples(self, cache):
        db_samples = self.sample_set.all().iterator()
        return filter(lambda x: x.can_display(), db_samples)

    def get_display_samples(self, cache=True):
        cache_key = 'job-%d-display-samples' % self.id
        return self._get_display_samples(cache_key=cache_key, cache=cache)

    def start_btm(self, title, description, no_of_urls, points_to_cash):
        kwargs = {
            'btm_status': self.BTMStatus.PENDING,
            'btm_title': title,
            'btm_description': description,
            'btm_to_gather': no_of_urls,
            'btm_points_to_cash': points_to_cash,
            'status': JOB_STATUS_ACTIVE,
        }

        Job.objects.filter(pk=self.pk).update(**kwargs)
        for name, key in kwargs.iteritems():
            setattr(self, name, key)

    def activate_btm(self):
        Job.objects.filter(pk=self.pk).update(btm_status=self.BTMStatus.ACTIVE)

        self.btm_status = self.BTMStatus.ACTIVE

        send_event(
            'EventBTMStarted',
            job_id=self.id,
            topic=self.btm_title,
            description=self.btm_description,
            no_of_urls=self.btm_to_gather,
        )

    def get_btm_status(self):
        """
            Returns a string representing job's BTM status.
        """
        if self.btm_status == self.BTMStatus.ACTIVE:
            return 'yes'

        return 'no'

    def get_btm_verified_samples(self):
        """
            Returns list of samples verified by BTM to be added to training set.
        """
        from urlannotator.crowdsourcing.models import BeatTheMachineSample

        btms = BeatTheMachineSample.objects.get_btm_verified(job_id=self)
        return [b.sample for b in btms]

    def get_btm_pending_samples(self):
        """
            Returns a list of samples that need to be added to the job by the
            owner.
        """
        from urlannotator.crowdsourcing.models import BeatTheMachineSample
        return BeatTheMachineSample.objects.get_all_ready(job=self)

    def add_btm_verified_sample(self, sample_id):
        """
            Should add a BTM-verified sample to the job so it can be included
            in new training sets.
        """
        from urlannotator.crowdsourcing.models import BeatTheMachineSample

        sample = BeatTheMachineSample.objects.\
            get(id=sample_id, job=self).sample
        sample.training = True
        sample.save()

        return sample

    def finish_btm(self):
        Job.objects.filter(pk=self.pk).\
            update(btm_status=self.BTMStatus.FINISHED)

        self.btm_status = self.BTMStatus.FINISHED

    def is_btm_finished(self):
        return self.btm_status == self.BTMStatus.FINISHED

    def is_btm_pending(self):
        return self.btm_status == self.BTMStatus.PENDING

    def is_btm_active(self):
        return self.btm_status == self.BTMStatus.ACTIVE

    def get_btm_to_gather(self):
        return self.btm_to_gather

    def get_btm_gathered(self):
        """
            Return a list of samples so that
            get_btm_gathered|length == number of samples that count towards
            BTM progress.
        """
        from urlannotator.crowdsourcing.models import BeatTheMachineSample
        return BeatTheMachineSample.objects.get_all_btm(self)

    @cached
    def _get_btm_votes(self, cache):
        from urlannotator.crowdsourcing.models import WorkerQualityVote
        return WorkerQualityVote.objects.filter(sample__job=self,
            btm_vote=True, is_valid=True)

    def get_btm_votes(self, cache=True):
        cache_key = 'job-{0}-btm-votes'.format(self.id)
        return self._get_btm_votes(cache=cache, cache_key=cache_key)

    def update_btm_progress(self):
        progress = self.get_btm_progress()
        if not progress == 100:
            return

        self.finish_btm()
        if self.get_progress() == 100:
            Job.objects.filter(pk=self.pk).update(status=JOB_STATUS_COMPLETED)
            self.status = JOB_STATUS_COMPLETED

    def get_btm_progress(self):
        to_gather = self.get_btm_to_gather() or 1
        progress = len(self.get_btm_gathered())
        total = to_gather

        return min(round((100.0 * progress) / total, 2), 100.0)

    def get_accepted_btm_samples(self):
        """
            Returns list of btm samples to add to training set.
        """

    def reclassify_samples(self):
        """
            Asynchronously reclassifies all samples.
        """
        for sample in self.sample_set.all().iterator():
            sample.reclassify()

    def has_new_votes(self):
        """
            Returns whether there are new votes in the job.
        """
        for sample in self.sample_set.iterator():
            if sample.workerqualityvote_set.filter(is_new=True).count():
                return True
        return False

    def get_link_with_title(self):
        return '<a href="%s">%s</a>' % (self.get_absolute_url(), self.title)

    def get_sample_gathering_url(self):
        """
            Returns the URL under which Own Workforce can submit new samples.
        """
        try:
            tag_job = self.tagasaurisjobs
            return tag_job.get_sample_gathering_url()
        except:
            return ''

    def stop_sample_gathering(self):
        """
            Stops underlying sample gathering job.
        """
        # Importing here due to possible loop imports
        tag_job = self.tagasaurisjobs
        tag_job.sample_gathering_hit = ''
        tag_job.save()
        stop_job(tag_job.sample_gathering_key)
        return True

    def get_btm_gathering_url(self):
        try:
            tag_job = self.tagasaurisjobs
            return tag_job.get_btm_gathering_url()
        except:
            return ''

    def get_btm_voting_url(self):
        try:
            tag_job = self.tagasaurisjobs
            return tag_job.get_btm_voting_url()
        except:
            return ''

    def get_voting_url(self):
        """
            Returns the URL under which Own Workforce can vote on labels.
        """
        try:
            tag_job = self.tagasaurisjobs
            return tag_job.get_voting_url()
        except:
            return ''

    def stop_voting(self):
        """
            Stops underlying voting job.
        """
        tag_job = self.tagasaurisjobs
        tag_job.voting_hit = ''
        tag_job.save()
        stop_job(tag_job.voting_key)
        return True

    def stop_btm(self):
        """
            Stops Beat The Machine job.
        """
        if not self.is_btm_active():
            return False

        tag_job = self.tagasaurisjobs
        tag_job.beatthemachine_hit = ''
        tag_job.voting_btm_hit = ''
        tag_job.save()
        stop_job(tag_job.beatthemachine_key)
        stop_job(tag_job.voting_btm_key)

        Job.objects.filter(pk=self.pk).\
            update(btm_status=self.BTMStatus.STOPPED)
        self.btm_status = self.BTMStatus.STOPPED
        return True

    def get_classifier_performance(self):
        """
            Returns classifier performance as a dict with keys 'TPR', 'TNR',
            'AUC'.
        """
        performances = self.classifierperformance_set.all().order_by('-id')
        ret = 0.00
        if not performances:
            return ret

        ret = performances[0]
        return round(ret.value.get('AUC', 0), 2)

    @cached
    def _get_newest_votes(self, num, cache):
        from urlannotator.crowdsourcing.models import WorkerQualityVote

        votes = WorkerQualityVote.objects.filter(sample__job=self,
            label=LABEL_YES).order_by('-added_on')[:num].iterator()
        newest_votes = [{
            'screenshot': s.sample.get_small_thumbnail_url(),
            'url': s.sample.url,
            'sample_url': reverse('project_data_detail', kwargs={
                'id': self.id,
                'data_id': s.sample.id,
            }),
            'label': s.label,
            'added_on': s.sample.added_on.strftime('%Y-%m-%d %H:%M:%S'),
            'date': s.added_on.strftime('%Y-%m-%d %H:%M:%S'),
        } for s in votes]
        return newest_votes

    def get_newest_votes(self, num=3, cache=False):
        """
            Returns newest correct votes in the job.
        """
        key = 'job-%d-newest_votes' % self.id
        return self._get_newest_votes(cache_key=key, cache=cache, num=num)

    def is_own_workforce(self):
        return self.data_source == JOB_SOURCE_OWN_WORKFORCE

    def get_status(self):
        return self.get_status_display()

    def is_draft(self):
        return self.status == JOB_STATUS_DRAFT

    def is_finished(self):
        return self.status == JOB_STATUS_COMPLETED

    def is_active(self):
        return self.status == JOB_STATUS_ACTIVE

    def activate(self, force=False):
        """
            Activates current job. If due to concurrency job's status changes
            inbetween, then if `force` is True the job is activated forcefully.
            Otherwise nothing happens.
        """
        with POSIXLock(name='job-%d-mutex' % self.id):
            job = Job.objects.get(id=self.id)
            if job.status != self.status and not force:
                return

            if self.is_active():
                return

            self.status = JOB_STATUS_ACTIVE
            self.activated = now()
            Job.objects.filter(id=self.id).update(status=self.status,
                activated=self.activated)
        send_event(
            "EventNewJobInitializationDone",
            job_id=self.id,
        )

    def initialize(self, force=False):
        """
            Initializes current job. If, due to concurrent calls, job's status
            changes then if `force` is True initialization is performed.
            Otherwise the call does nothing.
        """
        with POSIXLock(name='job-%d-mutex' % self.id):
            job = Job.objects.get(id=self.id)
            if job.status != self.status and not force:
                return

            if self.is_initializing():
                return

            self.status = JOB_STATUS_INIT
            self.remaining_urls = self.no_of_urls
            Job.objects.filter(id=self.id).update(status=self.status,
                remaining_urls=self.no_of_urls)
        send_event('EventNewJobInitialization',
            job_id=self.id)

    @cached
    def _get_hours_spent(self, cache):
        sum_res = WorkerJobAssociation.objects.filter(job=self).\
            aggregate(Sum('worked_hours'))
        sum_res = sum_res['worked_hours__sum']
        sum_res = sum_res if sum_res else 0
        return sum_res

    def get_hours_spent(self, cache=False):
        """
            Returns number of hours workers have worked on this project
            altogether.

            Parameters:
            :param cache: - whether to use cache. If not or the cache has
                            expired, it will be updated.
        """
        key = 'job-%d-hours-spent' % self.id
        return self._get_hours_spent(cache_key=key, cache=cache)

    @cached
    def _get_urls_collected(self, cache):
        samples = self.sample_set.filter(
            goldsample__isnull=True, btm_sample=False
        ).iterator()
        gold_samples = [gold['url'] for gold in self.gold_samples]

        collected = ifilter(
            lambda x: not x.url in gold_samples and x.can_display(), samples)
        collected = sum(1 for _ in collected)
        return collected

    def get_urls_collected(self, cache=True):
        """
            Returns number of urls collected (samples without gold samples).

            Parameters:
            :param cache: - whether to use cache. If not or the cache has
                            expired, it will be updated.
        """
        key = 'job-%d-urls-collected' % self.id
        return self._get_urls_collected(cache_key=key, cache=cache)

    def get_workers(self):
        """
            Returns workers associated with the job.
        """
        workers = [assoc.worker
            for assoc in WorkerJobAssociation.objects.filter(job=self)]
        return workers

    def get_no_of_workers(self):
        """
            Returns number of workers that have worked on this project.
        """
        return WorkerJobAssociation.objects.filter(job=self).count()

    @cached
    def _get_top_workers(self, num, cache):
        workers = list(self.workerjobassociation_set.all())
        workers.sort(
            key=lambda w: -w.get_urls_collected()
        )

        workers = workers[:num]
        return workers

    def get_top_workers(self, cache=False, num=3):
        """
            Returns `num` top of workers.

            Parameters:
            :param cache: - whether to use cache. If not or the cache has
                            expired, it will be updated.
        """
        key = 'job-%d-top-workers' % self.id
        return self._get_top_workers(cache_key=key, cache=cache, num=3)

    def get_cost(self, cache=True):
        """
            Returns amount of money the job has costed so far.

            Parameters:
            :param cache: - whether to use cache. If not or the cache has
                            expired, it will be updated.
        """
        # FIXME: Add proper billing entries?
        return self.hourly_rate * self.get_hours_spent(cache=cache)

    @cached
    def _get_votes_gathered(self, cache):
        from urlannotator.crowdsourcing.models import WorkerQualityVote
        votes = WorkerQualityVote.objects.filter(btm_vote=False,
            sample__job__id=self.id).count()
        return votes

    def get_votes_gathered(self, cache=False):
        """
            Returns amount of votes gathered.

            Parameters:
            :param cache: - whether to use cache. If not or the cache has
                            expired, it will be updated.
        """
        key = 'job-%d-votes-gathered' % self.id
        return self._get_votes_gathered(cache_key=key, cache=cache)

    @cached
    def _get_progress(self, cache):
        progress = (self.get_progress_urls(cache=cache)
            + self.get_progress_votes(cache=cache)) / 2.0

        if progress == 100 and \
            (self.btm_status == self.BTMStatus.NOT_ACTIVE or
             self.btm_status == self.BTMStatus.FINISHED):
            Job.objects.filter(pk=self.pk).update(status=JOB_STATUS_COMPLETED)
            self.status = JOB_STATUS_COMPLETED
        return progress

    def get_progress(self, cache=True):
        """
            Returns actual progress (in percents) in the job.

            Parameters:
            :param cache: - whether to use cache. If not or the cache has
                            expired, it will be updated.
        """
        key = 'job-%d-progress' % self.id
        return self._get_progress(cache_key=key, cache=cache)

    @cached
    def _get_progress_urls(self, cache):
        if not self.no_of_urls:
            return 100

        div = self.no_of_urls
        val = min((100 * self.get_urls_collected(cache=cache)) / div, 100)
        return val

    def get_progress_urls(self, cache=False):
        """
            Returns actual progress of urls collecting.

            Parameters:
            :param cache: - whether to use cache. If not or the cache has
                            expired, it will be updated.
        """
        key = 'job-%d-progress-urls' % self.id
        return self._get_progress_urls(cache_key=key, cache=cache)

    @cached
    def _get_progress_votes(self, cache):
        count = self.sample_set.all().count() * \
            settings.TAGASAURIS_VOTE_WORKERS_PER_HIT
        got = self.get_votes_gathered(cache=cache)

        count = count or 1
        val = min((100 * got) / count, 100)
        return val

    def get_progress_votes(self, cache=False):
        """
            Returns actual progress of votes collecting.

            Parameters:
            :param cache: - whether to use cache. If not or the cache has
                            expired, it will be updated.
        """
        key = 'job-%d-progress-votes' % self.id
        return self._get_progress_votes(cache_key=key, cache=cache)

    def is_completed(self):
        return self.status == JOB_STATUS_COMPLETED

    def complete(self):
        self.status = JOB_STATUS_COMPLETED
        self.save()

    def is_stopped(self):
        return self.status == JOB_STATUS_STOPPED

    def stop(self, force=False):
        """
            Stops the job. If `force` is True, the job is forcefully stopped
            no matter if during concurrent status change job's status has
            changed. Otherwise the call will do nothing.
        """
        with POSIXLock(name='job-%d-mutex' % self.id):
            job = Job.objects.get(id=self.id)
            if job.status != self.status and not force:
                return

            if self.is_stopped():
                return

            self.status = JOB_STATUS_STOPPED
            Job.objects.filter(id=self.id).update(status=self.status)
            try:
                self.stop_voting()
            except Exception:
                log.exception("Job: Couldn't stop voting for job {0}"
                    .format(self.id))

            try:
                self.stop_sample_gathering()
            except Exception:
                log.exception("Job: Couldn't stop sample_gathering for job {0}"
                    .format(self.id))

            try:
                self.stop_btm()
            except Exception:
                log.exception("Job: Couldn't stop btm for job {0}"
                    .format(self.id))

    def is_initializing(self):
        return self.status == JOB_STATUS_INIT

    def set_flag(self, flag):
        with POSIXLock(name='job-%d-mutex' % self.id):
            self.initialization_status = F('initialization_status') | flag
            self.save()

            job = Job.objects.get(id=self.id)
            self.initialization_status = job.initialization_status

            if self.initialization_status & self.Flags.ACTIVE == 0:
                return

            if job.is_active():
                return

        self.activate()

    def unset_flag(self, flag):
        self.initialization_status = F('initialization_status') & (~flag)
        self.save()

        job = Job.objects.get(id=self.id)
        self.initialization_status = job.initialization_status

    def is_flag_set(self, flag):
        return self.initialization_status & flag != 0

    def set_training_set_created(self):
        self.set_flag(self.Flags.TRAINING_SET_CREATED)

    def is_training_set_created(self):
        return self.is_flag_set(self.Flags.TRAINING_SET_CREATED)

    def set_gold_samples_done(self):
        self.set_flag(self.Flags.GOLD_SAMPLES_DONE)
        send_event(
            'EventGoldSamplesDone',
            job_id=self.id,
        )

    def is_gold_samples_done(self):
        return self.is_flag_set(self.Flags.GOLD_SAMPLES_DONE)

    def set_classifier_created(self):
        self.set_flag(self.Flags.CLASSIFIER_CREATED)

    def is_classifier_created(self):
        return self.is_flag_set(self.Flags.CLASSIFIER_CREATED)

    def set_classifier_trained(self):
        self.set_flag(self.Flags.CLASSIFIER_TRAINED)

    def unset_classifier_trained(self):
        self.unset_flag(self.Flags.CLASSIFIER_TRAINED)

    def is_classifier_trained(self):
        return self.is_flag_set(self.Flags.CLASSIFIER_TRAINED)

    @staticmethod
    def is_odesk_required_for_source(source):
        return int(source) not in [
            JOB_SOURCE_OWN_WORKFORCE, JOB_SOURCE_MTURK_WORKFORCE]

# Sample source types breakdown:
# owner - Sample created by the job creator. source_val is empty.
SAMPLE_SOURCE_OWNER = 'owner'
SAMPLE_TAGASAURIS_WORKER = 'tagasauris_worker'


class SampleManager(models.Manager):

    def _domain(self, url):
        return urlparse.urlparse(url).hostname

    def _sanitize(self, args, kwargs):
        """
            Sample data sanitization.
        """
        url = kwargs.get('url', '')

        # Add missing schema. Defaults to http://
        if url:
            result = urlparse.urlsplit(url)
            if not result.scheme:
                kwargs['url'] = 'http://%s' % url

            domain = self._domain(kwargs['url'])
            kwargs['domain'] = domain

    def _create_sample(self, *args, **kwargs):

        return send_event(
            'EventNewRawSample',
            *args, **kwargs
        )

    def create_by_owner(self, *args, **kwargs):
        '''
            Asynchronously creates a new sample with owner as a source.
        '''
        self._sanitize(args, kwargs)
        kwargs['source_type'] = SAMPLE_SOURCE_OWNER

        return self._create_sample(*args, **kwargs)

    def create_by_worker(self, *args, **kwargs):
        '''
            Asynchronously creates a new sample with tagasauris worker as a
            source.
        '''
        self._sanitize(args, kwargs)
        kwargs['source_type'] = SAMPLE_TAGASAURIS_WORKER

        # Add worker-job association.
        worker, created = Worker.objects.get_or_create_tagasauris(
            worker_id=kwargs['source_val']
        )
        job = Job.objects.get(id=kwargs['job_id'])

        WorkerJobAssociation.objects.associate(
            job=job,
            worker=worker,
        )

        return self._create_sample(*args, **kwargs)

    def create_by_btm(self, *args, **kwargs):
        '''
            Asynchronously creates a new sample with tagasauris worker as a
            source.
            It will be BTM (Beat The Machine) sample so it wont get into
            voting unless vote_sample parameter will be set.
        '''
        self._sanitize(args, kwargs)
        kwargs['source_type'] = SAMPLE_TAGASAURIS_WORKER
        kwargs['vote_sample'] = False
        kwargs['btm_sample'] = True
        kwargs['training'] = False

        # Add worker-job association.
        worker, created = Worker.objects.get_or_create_tagasauris(
            worker_id=kwargs['source_val']
        )
        job = Job.objects.get(id=kwargs['job_id'])

        WorkerJobAssociation.objects.associate(
            job=job,
            worker=worker,
        )

        return self._create_sample(*args, **kwargs)

    def by_worker(self, source_type, source_val, **kwargs):
        """
            Returns samples done by the worker.
        """
        return self.filter(source_type=source_type, source_val=source_val)


class Sample(models.Model):
    """
        A sample used to classify and verify.
    """
    job = models.ForeignKey(Job)
    url = models.URLField(max_length=500)
    domain = models.CharField(max_length=100, blank=False)
    text = models.TextField()
    screenshot = models.URLField(max_length=500)
    source_type = models.CharField(max_length=100, blank=False)
    source_val = models.CharField(max_length=100, blank=True, null=True)
    added_on = models.DateTimeField(auto_now_add=True)
    btm_sample = models.BooleanField(default=False)
    vote_sample = models.BooleanField(default=True)
    training = models.BooleanField(default=True)

    objects = SampleManager()

    class Meta:
        unique_together = ('job', 'url')

    @staticmethod
    def get_worker(source_type, source_val):
        """
            Returns a worker that corresponds to given (`source_type`,
            `source_val`) pair.
        """
        if source_type == SAMPLE_SOURCE_OWNER:
            # If the sample's creator is owner, ignore source worker.
            return None
        elif source_type == SAMPLE_TAGASAURIS_WORKER:
            return Worker.objects.get_tagasauris(worker_id=source_val)

    def can_display(self):
        # Internal source gold sample - display
        if self.is_gold_sample():
            return True

        if not self.is_finished():
            return False

        # If sample is from external source - display
        if self.source_type != SAMPLE_SOURCE_OWNER:
            return True

        # Rest - classification request, etc.
        return False

    def get_classified_label(self):
        class_set = self.classifiedsample_set.all().order_by('-id')
        if class_set:
            return class_set[0].label
        return None

    def reclassify(self):
        """
            Asynchronously reclassifies given `sample`.
        """
        # Possible loop imports here
        from urlannotator.classification.models import ClassifiedSample
        ClassifiedSample.objects.create_by_owner(
            job=self.job,
            url=self.url,
            sample=self,
        )

    def get_source_worker(self):
        """
            Returns a worker that has sent this sample.
        """
        return self.get_worker(
            source_type=self.source_type,
            source_val=self.source_val,
        )

    def get_screenshot_key(self):
        """
            Returns sample's key used to authenticate thumbnail request in
            imagescale.
        """
        algorithm = hashlib.new(imagescale2.HASHING_ALGORITHM)
        algorithm.update(imagescale2.SALT)
        algorithm.update(self.screenshot)
        return algorithm.hexdigest()

    def get_thumbnail_url(self, width=60, height=60):
        """
            Returns url which serves sample's thumbnail in given size.
        """
        base_url = 'http://' + settings.IMAGESCALE_URL
        params = {
            'width': width,
            'height': height,
            'url': self.screenshot,
            'key': self.get_screenshot_key(),
        }
        url = '%s/?%s' % (base_url, urlencode(params))
        return url

    def get_small_thumbnail_url(self):
        """
            Returns url which serves sample's small thumbnail.
        """
        return self.get_thumbnail_url(width=60, height=60)

    def get_65x45_thumbnail_url(self):
        return self.get_thumbnail_url(width=65, height=45)

    def get_240x180_thumbnail_url(self):
        return self.get_thumbnail_url(width=240, height=180)

    def get_690x518_thumbnail_url(self):
        return self.get_thumbnail_url(width=690, height=518)

    def get_large_thumbnail_url(self):
        """
            Returns url which serves sample's small thumbnail.
        """
        return self.get_thumbnail_url()

    def is_finished(self):
        """
            Whether the sample's creation has been finished.
        """
        return self.text and self.screenshot

    def get_workers(self):
        """
            Returns workers that have sent this sample (url).
        """
        workers = set()
        for cs in self.classifiedsample_set.all().iterator():
            worker = cs.get_source_worker()
            if worker and worker.can_show_to_user():
                workers.add(worker)
        return workers

    @cached
    def _get_yes_votes(self, cache):
        """
            Returns amount of YES votes received by this sample.
        """
        num = self.workerqualityvote_set.filter(label=LABEL_YES).count()
        return num

    def get_yes_votes(self, cache=True):
        cache_key = 'sample-%d-yes-votes' % self.id
        return self._get_yes_votes(cache_key=cache_key, cache=cache)

    @cached
    def _get_no_votes(self, cache):
        """
            Returns amount of NO votes received by this sample.
        """
        num = self.workerqualityvote_set.filter(label=LABEL_NO).count()
        return num

    def get_no_votes(self, cache=True):
        cache_key = 'sample-%d-no-votes' % self.id
        return self._get_no_votes(cache_key=cache_key, cache=cache)

    @cached
    def _get_broken_votes(self, cache):
        """
            Returns amount of BROKEN votes received by this sample.
        """
        num = self.workerqualityvote_set.filter(label=LABEL_BROKEN).count()
        return num

    def get_broken_votes(self, cache=True):
        cache_key = 'sample-%d-broken-votes' % self.id
        return self._get_broken_votes(cache_key=cache_key, cache=cache)

    def update_votes_cache(self):
        """
            Updates votes cache.
        """
        self.get_broken_votes(cache=False)
        self.get_yes_votes(cache=False)
        self.get_no_votes(cache=False)

    def get_yes_probability(self):
        """
            Returns probability of YES label on this sample, that is the
            percentage from the most recent classification.
        """
        cs_set = self.classifiedsample_set.all()
        if not cs_set:
            return 0

        cs = max(cs_set, key=(lambda x: x.id))
        yes_prob = cs.get_yes_probability()
        return yes_prob

    def get_no_probability(self):
        """
            Returns probability of NO label on this sample, that is the
            percentage from the most recent classification.
        """
        cs_set = self.classifiedsample_set.all()
        if not cs_set:
            return 0

        cs = max(cs_set, key=(lambda x: x.id))
        no_prob = cs.get_no_probability()
        return no_prob

    def get_broken_probability(self):
        """
            Returns probability of BROKEN label on this sample, that is the
            percentage from the most recent classification.
        """
        cs_set = self.classifiedsample_set.all()
        if not cs_set:
            return 0

        cs = max(cs_set, key=(lambda x: x.id))
        broken_prob = cs.get_broken_probability()
        return broken_prob

    def is_classified(self):
        """
            Returns whether this sample has been classified at least once.
        """
        # Check if we have been voted down as BROKEN
        from urlannotator.classification.models import (TrainingSet,
            TrainingSample)
        ts = TrainingSet.objects.newest_for_job(self.job)
        count = TrainingSample.objects.filter(
            sample=self,
            set=ts,
        ).count()

        # We are not adding broken samples to training sets
        if not count:
            return False

        yes_prob = self.get_yes_probability()
        no_prob = self.get_no_probability()

        return yes_prob or no_prob

    def is_gold_sample(self):
        try:
            return self.goldsample is not None
        except:
            return False

    def get_label(self):
        if self.is_gold_sample():
            return self.goldsample.label
        return self.get_classified_label()

    @classmethod
    def sanitize_url(cls, url):
        kwargs = {'url': url}
        cls.objects._sanitize(None, kwargs)
        return kwargs['url']


# Worker types breakdown:
# odesk - worker from odesk. External id points to user's odesk id.
# internal - worker registered in our system. External id is the user's id.
# tagasauris - worker provided by tagasauris.
WORKER_TYPE_ODESK = 0
WORKER_TYPE_INTERNAL = 1
WORKER_TYPE_TAGASAURIS = 2

WORKER_TYPES = (
    (WORKER_TYPE_ODESK, 'oDesk'),
    (WORKER_TYPE_INTERNAL, 'internal'),
    (WORKER_TYPE_TAGASAURIS, 'tagasauris'),
)

worker_type_to_sample_source = {
    WORKER_TYPE_TAGASAURIS: SAMPLE_TAGASAURIS_WORKER,
    WORKER_TYPE_INTERNAL: SAMPLE_SOURCE_OWNER,
    WORKER_TYPE_ODESK: SAMPLE_SOURCE_OWNER,
}


class WorkerManager(models.Manager):
    def create_odesk(self, *args, **kwargs):
        kwargs['worker_type'] = WORKER_TYPE_ODESK

        return self.create(**kwargs)

    def create_tagasauris(self, *args, **kwargs):
        kwargs['worker_type'] = WORKER_TYPE_TAGASAURIS

        return self.create(**kwargs)

    def create_internal(self, *args, **kwargs):
        kwargs['worker_type'] = WORKER_TYPE_INTERNAL

        return self.create(**kwargs)

    def get_tagasauris(self, worker_id):
        return self.get(
            worker_type=WORKER_TYPE_TAGASAURIS,
            external_id=worker_id,
        )

    def get_or_create_odesk(self, worker_id):
        return self.get_or_create(
            worker_type=WORKER_TYPE_ODESK,
            external_id=worker_id,
        )

    def get_or_create_tagasauris(self, worker_id):
        """
            Gets or creates Tagasauris worker with given id.
            Returns a 2-tuple (object, created):
            `worker` - the Worker object
            `created` - whether the object has been created or not.
        """
        return self.get_or_create(
            worker_type=WORKER_TYPE_TAGASAURIS,
            external_id=worker_id,
        )

    def get_odesk(self, external_id):
        return self.get(
            external_id=external_id,
            worker_type=WORKER_TYPE_ODESK,
        )


class Worker(models.Model):
    """
        Represents the worker who has completed a HIT.
    """
    external_id = models.CharField(max_length=100)
    worker_type = models.IntegerField(max_length=100, choices=WORKER_TYPES)

    # For easier searching
    name = models.CharField(max_length=256, default='')

    objects = WorkerManager()

    def __unicode__(self):
        return self.get_name()

    def can_show_to_user(self):
        return self.worker_type != WORKER_TYPE_INTERNAL

    @cached
    def _get_name(self, cache):
        from urlannotator.crowdsourcing.odesk_helper import get_worker_name
        name = None

        if self.worker_type == WORKER_TYPE_ODESK:
            name = get_worker_name(ciphertext=self.external_id)

        if self.worker_type == WORKER_TYPE_TAGASAURIS:
            try:
                tc = make_tagapi_client()
                worker_info = tc.get_worker(worker_id=self.external_id)
                name = worker_info['name']
            except:
                name = None

        if name is None:
            log.exception(
                'Exception while getting worker %d\'s name. Using default.' % self.id
            )
            return 'Worker %d' % self.id

        if self.name != name:
            self.name = name
            Worker.objects.filter(pk=self.pk).update(name=name)
        return name

    def get_name(self, cache=True):
        """
            Returns worker's name.
        """
        key = 'worker-%d-name' % self.id
        # One day cache
        time = 60 * 60 * 24
        return self._get_name(cache_key=key, cache=cache, cache_time=time)

    def get_urls_collected_count_for_job(self, job, cache=False):
        """
            Returns count of urls collected by worker for given job.
        """
        return len(self.get_urls_collected_for_job(job=job, cache=cache))

    @cached
    def _get_urls_collected_for_job(self, job, cache):
        # Importing here due to loop imports higher in the scope.
        from urlannotator.classification.models import ClassifiedSample
        return ClassifiedSample.objects.filter(
            job=job,
            source_type=worker_type_to_sample_source[self.worker_type],
            source_val=self.external_id)

    def get_urls_collected_for_job(self, job, cache=False):
        """
            Returns urls collected by given worker for given job.
        """
        key = 'worker-%d-job-%d-urls-collected' % (self.id, job.id)
        return self._get_urls_collected_for_job(job=job,
            cache_key=key, cache=cache)

    def get_links_collected(self):
        """ Returns number of links collected.
        """
        # Importing here due to loop imports higher in the scope.
        from urlannotator.classification.models import ClassifiedSample
        return ClassifiedSample.objects.filter(
            source_val=self.external_id,
            source_type=worker_type_to_sample_source[self.worker_type]
        ).count()

    def log_time_for_job(self, job, time):
        """
            Logs time worker has worker for given job for.

            :param time: a Decimal instance, or any other type that a Decimal
                         can be constructed from.
        """
        assoc = WorkerJobAssociation.objects.filter(job=job, worker=self)
        assoc.update(worked_hours=F('worked_hours') + time)

    def get_hours_spent_for_job(self, job):
        """
            Returns hours spent by given worker for given job.
        """
        try:
            assoc = WorkerJobAssociation.objects.get(job=job, worker=self)
            return assoc.worked_hours
        except WorkerJobAssociation.DoesNotExist:
            return 0

    @cached
    def _get_votes_added_count_for_job(self, job, cache):
        return sum(1 for vote in self.get_votes_added_for_job(job))

    def get_votes_added_count_for_job(self, job, cache=False):
        """
            Returns count of votes added by given worker for given job.
        """
        key = 'worker-%d-job-%d-votes-count' % (self.id, job.id)
        return self._get_votes_added_count_for_job(job=job,
            cache_key=key, cache=cache)

    def get_votes_added_for_job(self, job):
        """
            Returns votes added by given worker for given job.
        """
        return ifilter(
            lambda x: x.sample.job == job and x.worker == self,
            self.workerqualityvote_set.filter(btm_vote=False).iterator()
        )

    def get_earned_for_job(self, job):
        """
            Returns total amount of money earned by the given worker during
            given job.
        """
        votes_gathered = self.get_votes_added_count_for_job(job, cache=True)
        urls_gathered = self.get_urls_collected_count_for_job(job, cache=True)
        btm_bonus = self.get_btm_bonus_paid(job)

        vote_earned = float(votes_gathered)
        vote_earned /= float(settings.TAGASAURIS_VOTE_MEDIA_PER_HIT)
        # Workers are paid for full HITs done
        vote_earned = math.floor(vote_earned) * \
            float(settings.TAGASAURIS_VOTE_PRICE)

        urls_earned = float(urls_gathered)
        urls_earned /= 5.0
        # Workers are paid for full HITs done
        urls_earned = math.floor(urls_earned) * \
            float(settings.TAGASAURIS_GATHER_PRICE)

        return btm_bonus + vote_earned + urls_earned

    def get_job_start_time(self, job):
        '''
            Returns the time the worker started to work on the job at.
        '''
        try:
            assoc = WorkerJobAssociation.objects.get(job=job, worker=self)
        except WorkerJobAssociation.DoesNotExist:
            return datetime.datetime.now()

        return assoc.started_on

    def get_estimated_quality_for_job(self, job):
        """
            Retuns worker's estimated quality for given job.
        """
        return self.workerjobassociation_set.get(job=job).get_estimated_quality()

    def get_btm_bonus(self, job):
        """
            Retuns worker's bonus points gathered on given job.
        """
        from urlannotator.crowdsourcing.models import BeatTheMachineSample
        points = BeatTheMachineSample.objects.from_worker(self).\
            filter(job=job).aggregate(Sum('points'))['points__sum']
        points = points if points else 0
        return points

    def get_btm_bonus_paid(self, job):
        """
            Retuns worker's points gathered on given job for which payment was
            created.
        """
        from urlannotator.payments.models import BTMBonusPayment
        points = BTMBonusPayment.objects.filter(worker=self, job=job
            ).aggregate(Sum('points_covered'))['points_covered__sum']
        points = points if points else 0
        return points

    def get_btm_unverified(self, job):
        from urlannotator.crowdsourcing.models import BeatTheMachineSample
        return BeatTheMachineSample.objects.get_btm_unverified(job, self)

    def _send_tagasauris_message(self, subject, content):
        tc = make_tagapi_client()
        tc.send_message(
            worker_id=int(self.external_id),
            subject=subject,
            content=content)

    def send_message(self, subject, content):
        self._send_tagasauris_message(subject, content)

    def _prepare_bonus_notification(self, job):
        from urlannotator.crowdsourcing.models import BeatTheMachineSample
        btms = BeatTheMachineSample.objects.for_notification(self, job)

        plaintext = get_template('bonus_notification.txt')
        return plaintext.render(Context({
            'job': job,
            'btms': btms,
            'bonus': self.get_btm_bonus(job),
            'bonus_paid': self.get_btm_bonus_paid(job),
        }))

    def send_bonus_notification(self, job):
        self.send_message(
            subject="Build A Classifier Beat The Machine: Bonus points",
            content=self._prepare_bonus_notification(job))


def update_worker_name(sender, instance, created, raw, **kwargs):
    if created and not raw:
        instance.get_name()

post_save.connect(update_worker_name, sender=Worker)


class WorkerJobManager(models.Manager):
    def associate(self, job, worker):
        exists = self.filter(job=job, worker=worker).count()
        if not exists:
            self.create(job=job, worker=worker)
            WorkerJobURLStatistics.objects.create(job=job, worker=worker)


class WorkerJobAssociation(models.Model):
    """
        Holds worker associations with jobs they have participated in.
    """
    job = models.ForeignKey(Job)
    worker = models.ForeignKey(Worker)
    started_on = models.DateTimeField(auto_now_add=True)
    worked_hours = models.DecimalField(default=0, decimal_places=2,
        max_digits=10)
    data = JSONField(default='{}')

    # For easier searching
    votes_gathered = models.IntegerField(default=0)
    urls_gathered = models.IntegerField(default=0)

    objects = WorkerJobManager()

    def get_estimated_quality(self):
        return self.data.get('estimated_quality', 0)

    def get_urls_collected(self, cache=True):
        val = self.worker.get_urls_collected_count_for_job(job=self.job,
            cache=cache)
        if val != self.urls_gathered:
            self.urls_gathered = val
            WorkerJobAssociation.objects.filter(pk=self.pk).\
                update(urls_gathered=val)
        return val

    def get_votes_added(self, cache=True):
        val = self.worker.get_votes_added_count_for_job(job=self.job,
            cache=cache)
        if val != self.votes_gathered:
            self.votes_gathered = val
            WorkerJobAssociation.objects.filter(pk=self.pk).\
                update(votes_gathered=val)
        return val

    @property
    def btm_gathered(self):
        return self.worker.get_btm_bonus(job=self.job)

    @property
    def btm_paid(self):
        return self.worker.get_btm_bonus_paid(job=self.job)

    @property
    def btm_pending(self):
        return self.btm_gathered - self.btm_paid

    @cached
    def _get_url_collected_stats(self, cache):
        from urlannotator.statistics.stat_extraction import extract_stat

        model = WorkerJobURLStatistics
        models = model.objects.filter(job=self.job, worker=self.worker).\
            order_by('date')
        return extract_stat(models, hours=False, minutes=False, seconds=False)

    def get_url_collected_stats(self, cache=True):
        cache_key = 'worker_job_assoc-{0}-urls-collected-stats'.format(self.pk)
        return self._get_url_collected_stats(cache=cache, cache_key=cache_key)


class GoldSample(models.Model):
    """
        Sample uploaded by project owner. It is already classified and is used
        to train classifier.
    """
    sample = models.OneToOneField(Sample)
    label = models.CharField(max_length=10, choices=LABEL_CHOICES)


class ProgressManager(models.Manager):
    def latest_for_job(self, job):
        """
            Returns progress statistic for given job.
        """
        els = super(ProgressManager, self).get_query_set().filter(job=job).\
            order_by('-date')
        if not els.count():
            return None

        return els[0]


class ProgressStatistics(models.Model):
    """
        Keeps track of job progress per hour.
    """
    job = models.ForeignKey(Job)
    date = models.DateTimeField(auto_now_add=True)
    value = models.IntegerField(default=0)

    objects = ProgressManager()


class SpentManager(models.Manager):
    def latest_for_job(self, job):
        """
            Returns spent statistic for given job.
        """
        els = super(SpentManager, self).get_query_set().filter(job=job).\
            order_by('-date')
        if not els.count():
            return None

        return els[0]


class SpentStatistics(models.Model):
    """
        Keeps track of job spent amount per hour.
    """
    job = models.ForeignKey(Job)
    date = models.DateTimeField(auto_now_add=True)
    value = models.IntegerField(default=0)

    objects = SpentManager()


class URLStatManager(models.Manager):
    def latest_for_job(self, job):
        """
            Returns url collected statistic for given job.
        """
        els = super(URLStatManager, self).get_query_set().filter(job=job).\
            order_by('-date')
        if not els.count():
            return None

        return els[0]


class URLStatistics(models.Model):
    """
        Keeps track of urls collected for a job per hour.
    """
    job = models.ForeignKey(Job)
    date = models.DateTimeField(auto_now_add=True)
    value = models.IntegerField(default=0)

    objects = URLStatManager()


class VotesStatManager(models.Manager):
    def latest_for_job(self, job):
        """
            Returns votes collected statistic for given job.
        """
        els = super(VotesStatManager, self).get_query_set().filter(job=job).\
            order_by('-date')
        if not els.count():
            return None

        return els[0]


class VotesStatistics(models.Model):
    """
        Keeps track of votes gathered for a job per hour.
    """
    job = models.ForeignKey(Job)
    date = models.DateTimeField(auto_now_add=True)
    value = models.IntegerField(default=0)

    objects = VotesStatManager()


class LinksStatManager(models.Manager):
    def latest_for_worker(self, worker):
        """ Returns url collected statistic for given worker.
        """
        els = super(LinksStatManager, self).get_query_set().filter(
            worker=worker).order_by('-date')
        if not els.count():
            return None

        return els[0]


class LinksStatistics(models.Model):
    """ Keeps track of urls collected for worker per day.
    """
    worker = models.ForeignKey(Worker)
    date = models.DateTimeField(auto_now_add=True)
    value = models.IntegerField(default=0)

    objects = LinksStatManager()


class WorkerJobManager(models.Manager):
    def latest_for_assoc(self, worker_assoc):
        els = self.filter(worker=worker_assoc.worker, job=worker_assoc.job).\
            order_by('-id')

        if not els:
            return None

        return els[0]


class WorkerJobURLStatistics(models.Model):
    """ Keeps track of urls collected by worker per day per job
    """
    worker = models.ForeignKey(Worker)
    job = models.ForeignKey(Job)
    date = models.DateTimeField(auto_now_add=True)
    value = models.IntegerField(default=0)

    objects = WorkerJobManager()


class FillSample(models.Model):
    """
        Contains a link to a webpage that will be added to a job as a negative
        gold sample.

        Amount of samples added is equal to total number of urls to find.
    """
    url = models.URLField(primary_key=True, max_length=500)
