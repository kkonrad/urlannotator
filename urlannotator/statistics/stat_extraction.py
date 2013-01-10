import datetime
import json

from django.utils.timezone import now

from urlannotator.main.models import (SpentStatistics, ProgressStatistics,
    URLStatistics, LinksStatistics, LABEL_YES, LABEL_NO, VotesStatistics)
from urlannotator.classification.models import ClassifierPerformance


def format_date_val(val, time=True):
    """
        Formats a date statistics value into a Date.UTC(y,m,j,H,i,s) format.
    """
    date_string = '%Y,%m-1,%d'
    if time:
        date_string += ',%H,%M,%S'
    arg_string = val['date'].strftime(date_string)
    return '[Date.UTC(%s),%f]' % (arg_string, val['value'])


def extract_stat(stats, interval=datetime.timedelta(hours=1), time=True):
    """
        Returns a string representing a list of statistics samples formatted
        for use in Highcharts. The closest, earliest value is always used.

        If `time` is set, stat's time is also insert alongside to date.
    """
    return extract_stat_by_val(stats, lambda x: x.value, interval, time)


def extract_progress_stats(job, context):
    '''
        Extracts job's progress statistics as difference per hour.
    '''
    stats = ProgressStatistics.objects.filter(job=job).order_by('date')
    context['progress_stats'] = extract_stat(stats)


def extract_spent_stats(job, context):
    '''
        Extracts job's money spent statistics as difference per hour.
    '''
    stats = SpentStatistics.objects.filter(job=job).order_by('date')
    context['spent_stats'] = extract_stat(stats)


def extract_url_stats(job, context):
    '''
        Extracts job's url statistics as difference per hour.
    '''
    stats = URLStatistics.objects.filter(job=job).order_by('date')
    context['url_stats'] = extract_stat(stats)


def extract_votes_stats(job, context):
    '''
        Extracts job's votes gathered statistics as difference per hour.
    '''
    stats = VotesStatistics.objects.filter(job=job).order_by('date')
    context['votes_stats'] = extract_stat(stats)


def extract_workerlinks_stats(worker, context):
    '''
        Extracts job's url statistics as difference per hour.
    '''
    stats = LinksStatistics.objects.filter(worker=worker).order_by('date')
    context['workerlinks_stats'] = extract_stat(stats)


def extract_stat_by_val(stats, val_fun, interval=datetime.timedelta(hours=1),
        time=True):
    '''
        Extracts stat using a val_fun to take value from entry.
    '''
    stats = list(stats)
    stats_count = len(stats)
    now_time = now()
    idx = 1
    actual_time = datetime.datetime(
        year=stats[0].date.year,
        month=stats[0].date.month,
        day=stats[0].date.day,
        hour=stats[0].date.hour,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=stats[0].date.tzinfo,
    )
    actual_time += interval
    list_stats = []

    # Do-until-loop
    while True:
        # Find next closest sample
        while idx < stats_count:
            if stats[idx].date > actual_time:
                break
            idx += 1

        stat = stats[idx - 1]
        list_stats.append({
            'date': actual_time,
            'value': val_fun(stat)
        })
        actual_time += interval
        if actual_time > now_time:
            break

    stats = ','.join([format_date_val(v, time) for v in list_stats])
    return stats


def extract_performance_stats(job, context):
    '''
        Extracts job's performance statistics as difference per hour.
    '''
    extract_TPR = lambda x: x.value.get('TPR', 0)
    extract_TNR = lambda x: x.value.get('TNR', 0)
    extract_AUC = lambda x: x.value.get('AUC', 0)
    stats = ClassifierPerformance.objects.filter(job=job).order_by('date')

    context['performance_TPR'] = extract_stat_by_val(
        stats,
        extract_TPR
    )
    context['performance_TNR'] = extract_stat_by_val(
        stats,
        extract_TNR
    )
    context['performance_AUC'] = extract_stat_by_val(
        stats,
        extract_AUC
    )


def TruePositiveMetric(classifier, job, matrix):
    '''
        Calculates probability of saying True if the label is True in real.
    '''
    yes = matrix.get(LABEL_YES, {LABEL_YES: 0.0, LABEL_NO: 0.0})
    yesCount = yes.get(LABEL_YES, 0.0)
    noCount = yes.get(LABEL_NO, 0.0)
    div = (yesCount + noCount) or 1.0
    return ('TPR', round(100.0 * yesCount / div, 4))


def TrueNegativeMetric(classifier, job, matrix):
    '''
        Calculates probability of saying No if the label is No in real.
    '''
    no = matrix.get(LABEL_NO, {LABEL_YES: 0.0, LABEL_NO: 0.0})
    yesCount = no.get(LABEL_YES, 0.0)
    noCount = no.get(LABEL_YES, 0.0)
    div = (yesCount + noCount) or 1.0
    return ('TNR', round(100.0 * noCount / div, 4))


def AUCMetric(classifier, job, matrix):
    '''
        Calculates 'Area Under the Curve' metric.
    '''
    TPR = TruePositiveMetric(classifier, job, matrix)
    TNR = TrueNegativeMetric(classifier, job, matrix)
    return ('AUC', round((TPR[1] + TNR[1]) / 2.0, 4))

# List of classifier performance metrics to be calculated, stored and displayed
# on the performance chart
CLASSIFIER_PERFORMANCE_METRICS = (
    TruePositiveMetric,
    TrueNegativeMetric,
    AUCMetric,
)


def update_classifier_stats(classifier, job):
    '''
        Updates classifier performance statistics based on given metrics.
    '''
    stats = {}
    analyze = classifier.analyze()
    matrix = analyze['modelDescription']['confusionMatrix']
    for metric in CLASSIFIER_PERFORMANCE_METRICS:
        try:
            v = metric(classifier, job, matrix)
            stats[v[0]] = v[1]
        except Exception, e:
            raise Exception(
                '%s error while computing metric %s with data %s'
                % (e.message, metric, json.dumps(analyze))
            )
    stats['matrix'] = matrix

    ClassifierPerformance.objects.create(
        job=job,
        value=json.dumps(stats)
    )
    job.get_performance_stats(cache=False)
    job.get_confusion_matrix(cache=False)
