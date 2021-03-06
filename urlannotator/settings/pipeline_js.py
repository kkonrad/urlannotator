PIPELINE_JS = {
    'core': {
        'source_filenames': (
            'js/jquery-1.7.2.js',
            'js/ejs.min.js',
            'js/underscore.js',
            'js/json2.js',
            'js/backbone.js',
            'js/bootstrap.js',
            'js/bootstrap-tooltip.js',
            'js/coffee-script.js',
        ),
        'output_filename': 'js/core.min.js',
    },
    'crud': {
        'source_filenames': (
            'tenclouds/crud/js/init.js',
            'tenclouds/crud/js/settings.js',
            'tenclouds/crud/js/events.js',
            'tenclouds/crud/js/models.js',
            'tenclouds/crud/js/views.js',
            'tenclouds/crud/js/widgets.js',
        ),
        'output_filename': 'crud.js',
    },
    'samples_crud': {
        'source_filenames': (
            'js/main/samples/models.coffee',
            'js/main/samples/crud.coffee',
        ),
        'output_filename': 'js/samples_crud.js',
    },
    'workers_crud': {
        'source_filenames': (
            'js/main/workers/models.coffee',
            'js/main/workers/crud.coffee',
            'js/main/workers/widgets.js',
        ),
        'output_filename': 'js/workers_crud.js',
    },
    'less': {
        'source_filenames': (
            'js/less-1.3.0.js',
        ),
        'output_filename': 'js/less.min.js',
    }
}
