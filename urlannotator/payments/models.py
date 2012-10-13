from django.db import models

from urlannotator.main.models import Job, Worker
from tenclouds.django.jsonfield.fields import JSONField

# Payment statuses breakdown:
PAYMENT_STATUS_INITIALIZED = 0  # Payment has been initialized by us.
PAYMENT_STATUS_ISSUED = 1  # Payment has been issued by us using given backend.
PAYMENT_STATUS_PENDING = 2  # Payment has been issued and we are awaiting completion.
PAYMENT_STATUS_FINALIZATION = 3  # Payment is being finalized.
PAYMENT_STATUS_COMPLETED = 3  # Payment has been completed successfully.
PAYMENT_STATUS_INITIALIZATION = 4  # Payment is being initialized.

# Error statuses - sub_status field is filled with additional data if possible.
PAYMENT_STATUS_INITIALIZATION_ERROR = 4  # There was an error while initializing the payment.
PAYMENT_STATUS_ISSUED_ERROR = 5  # There was an error while issuing the payment.
PAYMENT_STATUS_FINALIZATION_ERROR = 6  # There was an error during payment finalization.

PAYMENT_STATUS_CHOICES = (
    (PAYMENT_STATUS_INITIALIZED, PAYMENT_STATUS_INITIALIZED),
    (PAYMENT_STATUS_ISSUED, PAYMENT_STATUS_ISSUED),
    (PAYMENT_STATUS_PENDING, PAYMENT_STATUS_PENDING),
    (PAYMENT_STATUS_FINALIZATION, PAYMENT_STATUS_FINALIZATION),
    (PAYMENT_STATUS_COMPLETED, PAYMENT_STATUS_COMPLETED),
    (PAYMENT_STATUS_INITIALIZATION_ERROR, PAYMENT_STATUS_INITIALIZATION_ERROR),
    (PAYMENT_STATUS_ISSUED_ERROR, PAYMENT_STATUS_ISSUED_ERROR),
    (PAYMENT_STATUS_FINALIZATION_ERROR, PAYMENT_STATUS_FINALIZATION_ERROR),
)

# Job tasks breakdown:
JOB_TASK_SAMPLE_GATHERING = 0
JOB_TASK_VOTING = 1
JOB_TASK_BTM = 2

SAMPLE_GATHERING_TASK_NAME = 'gathering'
VOTING_TASK_NAME = 'voting'
BTM_TASK_NAME = 'btm'


task_to_name = {
    JOB_TASK_SAMPLE_GATHERING: SAMPLE_GATHERING_TASK_NAME,
    JOB_TASK_VOTING: VOTING_TASK_NAME,
    JOB_TASK_BTM: BTM_TASK_NAME,
}


JOB_TASK_CHOICES = (
    (JOB_TASK_SAMPLE_GATHERING, JOB_TASK_SAMPLE_GATHERING),
    (JOB_TASK_VOTING, JOB_TASK_VOTING),
    (JOB_TASK_BTM, JOB_TASK_BTM),
)


class WorkerPaymentManager(models.Manager):
    def pay_task(self, worker, job, amount, backend, task):
        return self.create(
            worker=worker,
            job=job,
            amount=amount,
            backend=backend,
            job_task=task,
            status=PAYMENT_STATUS_INITIALIZATION,
        )

    def pay_sample_gathering(self, worker, job, amount, backend):
        return self.create(
            worker=worker,
            job=job,
            amount=amount,
            backend=backend,
            job_task=JOB_TASK_SAMPLE_GATHERING,
            status=PAYMENT_STATUS_INITIALIZATION,
        )

    def pay_voting(self, worker, job, amount, backend):
        return self.create(
            worker=worker,
            job=job,
            amount=amount,
            backend=backend,
            job_task=JOB_TASK_VOTING,
            status=PAYMENT_STATUS_INITIALIZATION,
        )

    def pay_btm(self, worker, job, amount, backend):
        return self.create(
            worker=worker,
            job=job,
            amount=amount,
            backend=backend,
            job_task=JOB_TASK_BTM,
            status=PAYMENT_STATUS_INITIALIZATION,
        )

    def get_sample_gathering(self, job):
        return self.filter(
            job=job,
            job_task=JOB_TASK_SAMPLE_GATHERING,
        )

    def get_voting(self, job):
        return self.filter(
            job=job,
            job_task=JOB_TASK_VOTING,
        )

    def get_btm(self, job):
        return self.filter(
            job=job,
            job_task=JOB_TASK_BTM,
        )

    def _filter_completed(self, payments):
        return payments.filter(status=PAYMENT_STATUS_COMPLETED)

    def _get_total(self, payments):
        return self._filter_completed(
            payments
        ).aggregate(models.Sum('amount'))['amount__sum']

    def get_sample_gathering_total(self, job):
        return self._get_total(
            self.get_sample_gathering(
                job=job,
            )
        )

    def get_voting_total(self, job):
        return self._get_total(
            self.get_voting(
                job=job,
            )
        )

    def get_btm_total(self, job):
        return self._get_total(
            self.get_btm(
                job=job,
            )
        )


class JobPaymentSettings(models.Model):
    job = models.ForeignKey(Job)
    split_budget = JSONField(default={})
    backend = models.CharField(max_length=25)
    main = models.BooleanField(default=True)

    class Meta:
        unique_together = ['job', 'main']


class WorkerPayment(models.Model):
    job = models.ForeignKey(Job)
    worker = models.ForeignKey(Worker)
    amount = models.DecimalField(default=0, decimal_places=2, max_digits=10)
    backend = models.CharField(max_length=25)
    status = models.PositiveIntegerField(choices=PAYMENT_STATUS_CHOICES)
    sub_status = models.CharField(max_length=50)
    job_task = models.PositiveIntegerField(choices=JOB_TASK_CHOICES)
    combined_payment = models.ForeignKey('WorkerPayment', null=True, blank=True)
    created_on = models.DateTimeField(auto_now_add=True)
    additional_data = JSONField(default={})

    objects = WorkerPaymentManager()

    def is_completed(self):
        return self.status == PAYMENT_STATUS_COMPLETED

    def set_status(self, status):
        self.status = status
        WorkerPayment.objects.filter(id=self.id).update(status=status)

    def complete(self):
        self.set_status(PAYMENT_STATUS_COMPLETED)

    def is_issued(self):
        return self.status == PAYMENT_STATUS_ISSUED

    def is_pending(self):
        return self.status == PAYMENT_STATUS_PENDING

    def is_finalizing(self):
        return self.status == PAYMENT_STATUS_FINALIZATION

    def is_combined_payment(self):
        return self.combined_payment_id is not None