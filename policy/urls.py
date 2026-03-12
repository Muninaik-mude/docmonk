from django.urls import path
from .views import PdfToHtmlView, PolicyAnalyzerView

urlpatterns = [
    path("analyze", PolicyAnalyzerView.as_view(), name="policy-analyze"),
    path("render-pdf", PdfToHtmlView.as_view(), name="policy-render-pdf"),
]
