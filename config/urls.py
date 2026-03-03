from django.urls import path, include

urlpatterns = [
    path('v1/', include('analyzer.urls')),
    path('v1/qa/', include('qa.urls')),
]
