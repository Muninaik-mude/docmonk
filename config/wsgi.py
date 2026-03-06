# gevent monkey-patching MUST be the very first thing imported.
#
# Why here and not in gunicorn.conf.py:
#   gunicorn's GeventWorker patches automatically *when using --worker-class gevent*,
#   but only after forking. If preload_app=True is ever set, the app gets imported in
#   the master process before forking — before any patching — breaking cooperative I/O
#   for all Groq/PostgreSQL network calls. Patching here is unconditional and safe:
#   monkey.patch_all() is idempotent (calling it twice is a no-op).
#
# Without this patch, gevent greenlets do NOT intercept stdlib socket/ssl/threading
# calls. Every Groq token chunk would block the entire worker thread instead of
# yielding cooperatively, defeating the purpose of gevent workers.
try:
    import threading
    from gevent import monkey
    # Only patch when running in the main thread (gunicorn).
    # Django's runserver loads wsgi.py in a non-main thread, and
    # monkey-patching there causes RuntimeError / lock corruption.
    if threading.current_thread() is threading.main_thread():
        monkey.patch_all()
except ImportError:
    # Dev environment without gevent installed — sync workers, no-op.
    pass

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()
