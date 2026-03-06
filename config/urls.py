from django.db import connection
from django.http import JsonResponse
from django.urls import path, include


def health(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "error", "detail": "database unreachable"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path('health', health),
    path('v1/', include('analyzer.urls')),
    path('v1/qa/', include('qa.urls')),
]
