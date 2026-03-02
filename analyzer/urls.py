from django.urls import path
from .views import ClauseAnalyzerView
from .job_views import JobStatusView, JobResumeView

urlpatterns = [
    path('analyze',                  ClauseAnalyzerView.as_view(), name='clause-analyze'),
    path('jobs/<uuid:job_id>',       JobStatusView.as_view(),      name='job-status'),
    path('jobs/<uuid:job_id>/resume', JobResumeView.as_view(),     name='job-resume'),
]
