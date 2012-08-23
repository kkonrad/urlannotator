# Log types breakdown:
LOG_TYPE_DEFAULT = -1  # Default log type settings
LOG_TYPE_JOB_INIT_START = 0  # Job initialization has been started
LOG_TYPE_JOB_INIT_DONE = 1  # Job initialization has been completed
LOG_TYPE_NEW_SAMPLE_START = 2  # New sample creation has been initiated
LOG_TYPE_NEW_GOLD_SAMPLE = 3  # New gold sample has been created
LOG_TYPE_NEW_SAMPLE_DONE = 4  # New sample creation has been finished
LOG_TYPE_CLASS_TRAIN_START = 5  # Classifier training has been started
LOG_TYPE_CLASS_TRAIN_DONE = 6  # Classifier training has been finished
LOG_TYPE_SAMPLE_CLASSIFIED = 7  # A new sample has been classified

# Long action type breakdown:
LONG_ACTION_TRAINING = 1  # Classifier training

# Log configurations
# Configuration template:
# 'Single_text' - event description in singular form.
# 'Plural_text' - event description in plural form.
# 'Box_entry' - dictionary of settings used in formatting updates box entries.
#   'Title' - entry title
#   'Text' - entry text.
#   'Image_url' - url of alert's image.
#   'By_id' - id of worker that triggered the log.
#   'By' - name of the worker that triggered the log.
# 'Show_users' - whether log can be shown to users in alerts/updates box.
# 'Console_out' - event description when printing out to the console. Also
#                 used in entry.__unicode__() method
#
# Following variables are available in strings:
#   'job_url', 'job_id', any variable from entry.log_val dictionary,
#   'log_val', 'log_type', 'worker_id' (defaults to 0), 'worker_name' (defaults
#   to an empty string)
# If a config entry is missing some values, default ones are used.

log_config = {
    LOG_TYPE_DEFAULT: {
        'Single_text': 'Event',
        'Plural_text': 'Events',
        'Box_entry': {
            'Title': 'Event',
            'Text': 'Event text.',
            'Image_url': '',
            'By_id': 0,
            'By': '',
        },
        'Show_users': False,
        'Console_out': 'Event %(log_type)s (%(log_val)s).',
    },
    LOG_TYPE_JOB_INIT_START: {
        'Console_out': 'Job %(job_id)d\'s initialization has been started.',
    },
    LOG_TYPE_JOB_INIT_DONE: {
        'Console_out': 'Job %(job_id)d\'s initialization has been completed.',
    },
    LOG_TYPE_NEW_SAMPLE_START: {
        'Console_out': 'New sample is being created (%(log_val)s).',
    },
    LOG_TYPE_NEW_GOLD_SAMPLE: {
        'Console_out': 'New gold sample is being created (%(log_val)s).',
    },
    LOG_TYPE_NEW_SAMPLE_DONE: {
        'Console_out': 'New sample has been created (%(log_val)s).',
        'Single_text': 'New sample (%(sample_url)s) has been created.',
        'Plural_text': 'New samples have been created.',
        'Show_users': True,
        'Box_entry': {
            'Title': 'New Sample',
            'Text': '<a href="%(sample_url)s">%(sample_url)s</a>',
        },
    },
    LOG_TYPE_CLASS_TRAIN_START: {
        'Console_out': 'Classifier is being trained (%(log_val)s).',
    },
    LOG_TYPE_CLASS_TRAIN_DONE: {
        'Console_out': 'Classifier training has finished (%(log_val)s).',
    },
    LOG_TYPE_SAMPLE_CLASSIFIED: {
        'Console_out': 'Sample has been classified (%(log_val)s).',
        'Single_text': 'New sample (%(class_url)s) has been created.',
        'Plural_text': 'New samples have been created.',
        'Show_users': True,
        'Box_entry': {
            'Title': 'New Sample',
            'Text': '<a href="%(class_url)s">%(class_url)s</a>',
        },
    },
}


def log_config_get(log_type, attrs):
    """
        Gets given config attr for log_type. If not present, gets the attrs for
        default config. If missing, returns None.
        Attrs can be a list of attributes for nested lookup.
    """
    if not log_type in log_config:
        return None

    default = log_config[LOG_TYPE_DEFAULT]
    value = log_config[log_type]
    for attr in attrs:
        value = value.get(attr, None)
        default = default[attr]
        if not value:
            value = default

    return value


def generate_log_types():
    """
        Generates a list of tuples (log_id, log_text) and returns it.
    """
    log_list = [(log_id, log_config_get(log_id, ['Single_text']))
        for log_id, log in log_config.items()]
    return list(log_list)

# Long actions formats
long_single = {
    LONG_ACTION_TRAINING:
    '<a href="%(job_url)s">Your job\'s</a> classifier is under training.',
}

long_plural = {
    LONG_ACTION_TRAINING: 'Classifiers are being trained.',
}