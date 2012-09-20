PIPELINE_CSS = {
    'bootstrap': {
        'source_filenames': (
            'less/bootstrap/bootstrap.less',
        ),
        'output_filename': 'css/bootstrap.css',
        'extra_context': {
            'rel': 'stylesheet/less',
        },
    },
    'bootstrap-responsive': {
        'source_filenames': (
            'less/bootstrap/responsive.less',
        ),
        'output_filename': 'css/bootstrap-responsive.css',
        'extra_context': {
            'rel': 'stylesheet/less',
        },
    },
    'base': {
        'source_filenames': (
            'less/base.less',
        ),
        'output_filename': 'css/base.css',
        'extra_context': {
            'rel': 'stylesheet/less',
        },
    },
    'wizard': {
        'source_filenames': (
            'less/wizard.less',
        ),
        'output_filename': 'css/wizard.css',
        'extra_context': {
            'rel': 'stylesheet/less',
        },
    },
    'project': {
        'source_filenames': (
            'less/project.less',
        ),
        'output_filename': 'css/project.css',
        'extra_context': {
            'rel': 'stylesheet/less',
        },
    },
}