from django.urls import path
from .views import PolicyAnalyzerView, PolicyRuleExtractView

urlpatterns = [
    path("extract-rules", PolicyRuleExtractView.as_view(), name="policy-extract-rules"),
    path("analyze",       PolicyAnalyzerView.as_view(),    name="policy-analyze"),
]
