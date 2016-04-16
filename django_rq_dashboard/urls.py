from django.conf.urls import url

from . import views


urlpatterns = [
    url(r'^$',
        views.Stats.as_view(), name='rq_stats'),
    url(r'^queues/(?P<queue>.+)/$',
        views.QueueDetails.as_view(), name='rq_queue'),
    url(r'^workers/(?P<worker>.+)/$',
        views.WorkerDetails.as_view(), name='rq_worker'),
    url(r'^jobs/(?P<job>.+)/$',
        views.JobDetails.as_view(), name='rq_job'),
    url(r'^scheduler/(?P<queue>.+)/$',
        views.SchedulerDetails.as_view(), name='rq_scheduler'),
]
