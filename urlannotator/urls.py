from django.conf import settings
from django.conf.urls import patterns, include, url
from registration import backends
from django.contrib import admin, auth
admin.autodiscover()


def bad(request):
    """ Simulates a server error """
    1 / 0

urlpatterns = patterns('urlannotator',

    url(r'^$', 'main.views.index', name='index'),
    url(r'^register$', 'main.views.register_view', name='register'),
    url(r'^activation/(?P<key>.+)$', 'main.views.activation_view', name='activation'),
    url(r'^login$', 'main.views.login_view', name='login'),
    url(r'^settings$', 'main.views.settings', name='settings'),
    url(r'^logout$', 'main.views.logout_view', name='logout'),
    url(r'^odesk_disconnect$', 'main.views.odesk_disconnect', name='odesk_disconnect'),
    url(r'^odesk_login/complete$', 'main.views.odesk_complete', name='odesk_complete'),
    url(r'^odesk_login$', 'main.views.odesk_login', name='odesk_login'),
    url(r'^_admin/', include(admin.site.urls)),
    url(r'^auth/', include('social_auth.urls')),
    
    (r'^bad/$', bad),
)

urlpatterns += patterns('',

  url(r'^password_recovery$', 'django.contrib.auth.views.password_reset', {'template_name': 'main/password_reset.html',
                                                                           'email_template_name':'password_reset_email.txt',
                                                                           'subject_template_name':'password_reset_email_subject.txt',}, name='password_reset'),
  url(r'^password_recovery/done$', 'django.contrib.auth.views.password_reset_done',{'template_name':'main/password_reset_done.html'}, name='password_reset_done'),
  url(r'^password_recovery/complete$', 'django.contrib.auth.views.password_reset_complete', {'template_name':'main/password_reset_complete.html'}, name='password_reset_complete'),
  url(r'^password_recovery/confirm/(?P<uidb36>.*)/(?P<token>.*)$', 'django.contrib.auth.views.password_reset_confirm', {'template_name':'main/password_reset_confirm.html'},name='password_reset_confirm'),
)
## In DEBUG mode, serve media files through Django.
if settings.DEBUG:
    # Remove leading and trailing slashes so the regex matches.
    media_url = settings.MEDIA_URL.lstrip('/').rstrip('/')
    urlpatterns += patterns('',
        (r'^%s/(?P<path>.*)$' % media_url, 'django.views.static.serve',
         {'document_root': settings.MEDIA_ROOT}),
    )
