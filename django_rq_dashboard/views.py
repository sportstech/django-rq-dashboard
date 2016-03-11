import json
import pytz
import redis
from itertools import groupby

from django.contrib import messages
from django.contrib.admin.views.main import PAGE_VAR, ERROR_FLAG
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _, ugettext as __
from django.views import generic

from rq import Queue, Worker, get_failed_queue, push_connection
from rq.exceptions import NoSuchJobError
from rq.job import Job

try:
    from rq_scheduler import Scheduler
except ImportError:
    # rq_scheduler is not installed
    Scheduler = None

from .forms import QueueForm, JobForm


utc = pytz.timezone('UTC')


def serialize_job(job):
    return dict(
        id=job.id,
        key=job.key,
        created_at=timezone.make_aware(job.created_at, utc),
        enqueued_at=timezone.make_aware(job.enqueued_at,
                                        utc) if job.enqueued_at else None,
        ended_at=timezone.make_aware(job.ended_at,
                                     utc) if job.ended_at else None,
        origin=job.origin,
        result=job.result,
        exc_info=job.exc_info,
        description=job.description,
    )


def serialize_queue(queue):
    return dict(
        name=queue.name,
        count=queue.count,
        url=reverse('rq_queue', args=[queue.name]),
    )


def serialize_worker(worker):
    return dict(
        name=worker.name,
        queues=[q.name for q in worker.queues],
        state=worker.get_state().decode('utf-8'),
        url=reverse('rq_worker', args=[worker.name]),
    )


def serialize_scheduled_job(job, next_run):
    adict = serialize_job(job)
    adict['repeat'] = job.meta.get('repeat', None)
    adict['interval'] = job.meta.get('interval', None)
    adict['next_run'] = next_run
    return adict


def serialize_scheduled_queues(queue):
    return dict(
        url=reverse('rq_scheduler', args=[queue['name']]),
        **queue)


class SuperUserMixin(object):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied

        opts = getattr(settings, 'RQ', {}).copy()
        opts.pop('eager', None)
        self.connection = redis.Redis(**opts)
        push_connection(self.connection)

        return super(SuperUserMixin, self).dispatch(request, *args, **kwargs)


class Stats(SuperUserMixin, generic.TemplateView):
    template_name = 'rq/stats.html'

    def get_context_data(self, **kwargs):
        ctx = super(Stats, self).get_context_data(**kwargs)
        ctx.update({
            'queues': Queue.all(connection=self.connection),
            'workers': Worker.all(connection=self.connection),
            'has_permission': True,
            'title': 'RQ Status',
        })
        if Scheduler:
            scheduler = Scheduler(self.connection)
            get_queue = lambda job: job.origin
            all_jobs = sorted(scheduler.get_jobs(), key=get_queue)
            ctx['scheduler'] = scheduler
            ctx['scheduled_queues'] = [
                {'name': queue, 'job_count': len(list(jobs))}
                for queue, jobs in groupby(all_jobs, get_queue)]
        return ctx

    def render_to_response(self, context, **response_kwargs):
        if self.request.is_ajax():
            data = json.dumps({
                'queues': [serialize_queue(q) for q in context['queues']],
                'workers': [serialize_worker(w) for w in context['workers']],
                'scheduled_queues': [serialize_scheduled_queues(q)
                                     for q in context.get('scheduled_queues', [])],
            })
            return HttpResponse(
                data, content_type='application/json; charset=UTF-8',
            )
        return super(Stats, self).render_to_response(context,
                                                     **response_kwargs)
stats = Stats.as_view()


class JobList(list):
    """
    list-like object that uses get_jobs and count for slicing and counting
    """

    def __init__(self, queue):
        self._queue = queue

    def __getslice__(self, i, j):
        return [serialize_job(job) for job in self._queue.get_jobs(i, j)]

    def __iter__(self):
        return (serialize_job(job) for job in self._queue.jobs)

    def __len__(self):
        return self.count()

    def count(self):
        return self._queue.count


class SimpleChangeList(object):

    def __init__(self, request, paginator):
        self.paginator = paginator
        self.result_count = paginator.count
        self.full_result_count = paginator.count
        self.show_full_result_count = False
        self.show_all = False
        self.can_show_all = False
        self.multi_page = self.result_count > paginator.per_page
        try:
            self.page_num = int(request.GET.get(PAGE_VAR, 0))
        except ValueError:
            self.page_num = 0
        self.params = dict(request.GET.items())
        if PAGE_VAR in self.params:
            del self.params[PAGE_VAR]
        if ERROR_FLAG in self.params:
            del self.params[ERROR_FLAG]

    def get_query_string(self, new_params=None, remove=None):
        if new_params is None:
            new_params = {}
        if remove is None:
            remove = []
        p = self.params.copy()
        for r in remove:
            for k in list(p):
                if k.startswith(r):
                    del p[k]
        for k, v in new_params.items():
            if v is None:
                if k in p:
                    del p[k]
            else:
                p[k] = v
        return '?%s' % urlencode(sorted(p.items()))


class QueueDetails(SuperUserMixin, generic.ListView, generic.FormView):
    template_name = 'rq/queue.html'
    form_class = QueueForm
    paginate_by = 100
    page_kwarg = PAGE_VAR

    def get(self, request, *args, **kwargs):
        # offset page kwarg for admin.
        try:
            page_num = int(request.GET.get(PAGE_VAR, 0))
        except ValueError:
            page_num = 0
        self.kwargs[PAGE_VAR] = page_num + 1
        return super(QueueDetails, self).get(request, *args, **kwargs)

    def get_queryset(self):
        queue = Queue(self.kwargs['queue'], connection=self.connection)
        # not really a queryset...
        return JobList(queue)

    def get_success_url(self):
        return reverse('rq_queue', kwargs=self.kwargs)

    def get_context_data(self, **kwargs):
        ctx = super(QueueDetails, self).get_context_data(**kwargs)
        queue = Queue(self.kwargs['queue'], connection=self.connection)

        if 'form' not in ctx:
            ctx['form'] = self.get_form()

        # create fake ChangeList-like object so we can use the admin
        # pagination template helpers.
        paginator = ctx.get('paginator')
        ctx.update({
            'queue': queue,
            'jobs': ctx.get('object_list'),
            'pagination_required': ctx.get('is_paginated'),
            'cl': SimpleChangeList(self.request, paginator),
            'has_permission': True,
            'title': "'%s' queue" % queue.name,
            'failed': queue.name == 'failed',
        })
        return ctx

    def get_form_kwargs(self):
        kwargs = super(QueueDetails, self).get_form_kwargs()
        kwargs['queue'] = Queue(self.kwargs['queue'],
                                connection=self.connection)
        return kwargs

    def form_valid(self, form):
        form.save()
        msgs = {
            'compact': __('Queue compacted'),
            'empty': __('Queue emptied'),
            'requeue': __('Jobs requeued'),
        }
        messages.success(self.request, msgs[form.cleaned_data])
        return super(QueueDetails, self).form_valid(form)
queue = QueueDetails.as_view()


class JobDetails(SuperUserMixin, generic.FormView):
    template_name = 'rq/job.html'
    form_class = JobForm

    def get_form_kwargs(self):
        kwargs = super(JobDetails, self).get_form_kwargs()
        self.job = Job.fetch(self.kwargs['job'], connection=self.connection)
        kwargs['job'] = self.job
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super(JobDetails, self).get_context_data(**kwargs)
        try:
            job = Job.fetch(self.kwargs['job'], connection=self.connection)
        except NoSuchJobError:
            raise Http404
        if job.is_failed:
            queue = get_failed_queue(connection=self.connection)
        else:
            queue = Queue(job.origin, connection=self.connection)
        ctx.update({
            'job': serialize_job(job),
            'queue': queue,
            'has_permission': True,
            'title': _('Job %s') % job.id,
        })
        return ctx

    def form_valid(self, form):
        queue = 'failed' if form.job.is_failed else form.job.origin
        form.save()
        msgs = {
            'requeue': __('Job requeued'),
            'cancel': __('Job canceled'),
        }
        messages.success(self.request, msgs[form.cleaned_data])
        return redirect(reverse('rq_queue', args=[queue]))
job = JobDetails.as_view()


class WorkerDetails(SuperUserMixin, generic.TemplateView):
    template_name = 'rq/worker.html'

    def get_context_data(self, **kwargs):
        ctx = super(WorkerDetails, self).get_context_data(**kwargs)
        key = Worker.redis_worker_namespace_prefix + self.kwargs['worker']
        worker = Worker.find_by_key(key, connection=self.connection)
        ctx.update({
            'worker': worker,
            'has_permission': True,
            'title': _('Worker %s') % worker.name,
        })
        return ctx
worker = WorkerDetails.as_view()


class SchedulerDetails(SuperUserMixin, generic.TemplateView):
    template_name = 'rq/scheduler.html'

    def get_context_data(self, **kwargs):
        ctx = super(SchedulerDetails, self).get_context_data(**kwargs)
        if Scheduler is None:
            # rq_scheduler is not installed
            raise Http404
        scheduler = Scheduler(self.connection)
        queue = Queue(self.kwargs['queue'], connection=self.connection)

        def cond(job_tuple):
            job, next_run = job_tuple
            return job.origin == queue.name
        jobs = filter(cond, scheduler.get_jobs(with_times=True))

        ctx.update({
            'queue': queue,
            'jobs': [serialize_scheduled_job(job, next_run)
                     for job, next_run in jobs],
            'has_permission': True,
            'title': "Jobs scheduled on '%s' queue" % queue.name,
        })
        return ctx

scheduler = SchedulerDetails.as_view()
