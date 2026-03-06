"""
Gunicorn configuration for DocMonk — gevent workers for SSE streaming.

Why gevent:
  /ask, /retry, /regenerate are SSE endpoints that hold open connections for
  10–30 seconds while waiting on Groq LLM tokens. Sync workers block for the
  entire duration of each stream — 2 sync workers means 2 concurrent users
  before the app freezes. Gevent workers use cooperative multitasking: a
  greenlet yields during the Groq network wait, allowing other connections to
  receive chunks concurrently.

DB connection budget (Railway PostgreSQL hobby plan: 25 max connections):
  Each active SSE stream holds one Django DB connection for its full duration
  (conn_max_age=0 closes connections at request end, not between queries).
  Peak DB connections ≈ workers × worker_connections × fraction_in_SSE.
  Keep workers × worker_connections ≤ 25 unless PgBouncer is provisioned.
  Default: 2 workers × 10 connections = 20 max — safely under the limit.
  Override via GUNICORN_WORKERS and GUNICORN_WORKER_CONNECTIONS env vars.
"""
import os

# ── Worker ─────────────────────────────────────────────────────────────────────

worker_class = "gevent"

# Number of greenlets per worker.
# Keep workers × worker_connections ≤ Railway PostgreSQL max_connections.
# Add PgBouncer before increasing this beyond the DB limit.
worker_connections = int(os.environ.get("GUNICORN_WORKER_CONNECTIONS", 10))

# Number of worker processes.
# Do NOT use multiprocessing.cpu_count() — it returns host machine CPUs on
# Railway containers, not the container's actual vCPU allocation.
# Default 2 for Railway hobby tier (1–2 vCPUs). Set GUNICORN_WORKERS in the
# Railway environment dashboard to change without a code deploy.
workers = int(os.environ.get("GUNICORN_WORKERS", 2))

# ── Worker recycling (C1) ──────────────────────────────────────────────────────
# Recycle each worker after N requests to release memory accumulated from
# PyMuPDF C-level allocations and large extracted-text buffers that Python's
# GC cannot see. Jitter staggers recycling across the pool to avoid a
# thundering-herd restart where all workers recycle simultaneously.
max_requests        = 500
max_requests_jitter = 50

# Share imported module memory across workers via OS copy-on-write (C2).
# Cuts per-worker RSS by ~30–40 MB (Django + PyMuPDF + reportlab import cost).
# Workers still get independent memory after the first write (fork semantics).
preload_app = True

# ── Timeouts ───────────────────────────────────────────────────────────────────

# SSE streams can run 10–30 s depending on Groq response speed.
# 120 s gives comfortable headroom without leaving zombie workers.
timeout   = 120
keepalive = 5          # seconds to keep idle HTTP connections alive

# ── Binding ────────────────────────────────────────────────────────────────────

# Railway injects $PORT at runtime. Default to 8080 for local runs.
bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"

# ── Logging ────────────────────────────────────────────────────────────────────

accesslog = "-"    # stdout → Railway log stream
errorlog  = "-"    # stdout → Railway log stream
loglevel  = "info"
