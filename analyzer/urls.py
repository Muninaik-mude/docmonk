from django.urls import path
from .views import ClauseAnalyzerView

urlpatterns = [
    path('analyze/', ClauseAnalyzerView.as_view(), name='clause-analyze'),
]
