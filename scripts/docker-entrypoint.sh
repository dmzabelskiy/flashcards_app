#!/usr/bin/env bash
set -euo pipefail

# Make the container authoritative on the DB path so mkdir, migrate, and gunicorn
# (via Django settings) all agree — regardless of whether the deploy environment
# (e.g. a missing fly.toml [env] key) sets DJANGO_DB_PATH. Without this, Django's
# own fallback (BASE_DIR/db.sqlite3, i.e. /app) could silently diverge from the
# mounted volume and every redeploy would wipe user data.
export DJANGO_DB_PATH="${DJANGO_DB_PATH:-/data/db.sqlite3}"

# The volume is mounted at /data; ensure it exists and run migrations against it.
mkdir -p "$(dirname "$DJANGO_DB_PATH")"

# Take an online backup before migrating, in the foreground so failures are
# visible in boot logs. Skipped on the very first boot, when the database
# file does not exist yet. A failed backup logs loudly but does not abort
# startup — the machine must still serve even if /data/backups is
# unwritable or the volume is full.
if [ -f "$DJANGO_DB_PATH" ]; then
  python manage.py backup_database \
    || echo "WARNING: database backup failed; continuing startup" >&2
fi

# The lemma cache must have been baked into the image at build time
# (build_lemma_cache runs in the Dockerfile); building it here would peak at
# ~350 MB and OOM-kill this 256 MB machine, so this only checks, never builds.
python manage.py build_lemma_cache --check \
  || echo "WARNING: lemma cache missing; word capture would exhaust memory" >&2

python manage.py migrate --noinput

# Scheduled backups: one background timer on this machine, started after
# migrate so the schema exists. It compares the wall clock with persisted
# success timestamps every <=60 s, so a backup that came due while the machine
# was suspended is taken right after it wakes. A lock file prevents a second
# timer; --parent-pid ($$ stays the same after the exec below) makes it exit
# when this process goes away. It runs as `python -m` rather than manage.py to
# skip loading Django's apps (memory is tight). A crash or misconfiguration
# only logs; the server keeps serving. Off-site upload stays off unless
# BACKUP_REMOTE_ENABLED is set; see docs/backups.md.
{ python -m core.backup_scheduler --parent-pid "$$" \
    || echo "WARNING: backup scheduler exited abnormally; backups only run at boot" >&2; } &

# Refresh the video/podcast catalogs at most once per day. The machine
# suspends instead of stopping when idle, so a wake-up skips this entrypoint;
# only a deploy or a cold boot runs it. video_study.services.start_kurzgesagt_prefetch,
# called from the dashboard view, is what keeps the Kurzgesagt catalog and its
# transcripts fresh the rest of the time. Backgrounded so it never delays
# serving; --if-stale keeps it from re-fetching on every start; the command
# swallows upstream errors on its own.
python manage.py refresh_media_catalogs --if-stale 24 &

# Threads let a slow synchronous translation provider call block only the
# request that made it, instead of tying up an entire sync worker: without
# them, one sync worker stuck waiting on a slow provider would stall every
# other user for the duration of the call.
exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-8080}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --threads "${WEB_THREADS:-4}" \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
