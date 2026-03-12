from django.urls import path
from .views import PolicyAnalyzerView

urlpatterns = [
    path("analyze", PolicyAnalyzerView.as_view(), name="policy-analyze"),
]
