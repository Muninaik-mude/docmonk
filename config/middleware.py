from django.db import connection
from django.http import JsonResponse


class HealthCheckMiddleware:
    """
    Intercepts GET /health before any host validation runs.
    Django's ALLOWED_HOSTS check fires when request.get_host() is called
    (inside CommonMiddleware). Placing this middleware first lets Railway's
    internal health checker (Host: 100.64.0.2) receive a proper response
    instead of a 400 DisallowedHost.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/health':
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT 1')
            except Exception:
                return JsonResponse(
                    {'status': 'error', 'detail': 'database unreachable'},
                    status=503,
                )
            return JsonResponse({'status': 'ok'})
        return self.get_response(request)
