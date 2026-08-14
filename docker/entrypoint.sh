#!/bin/sh
# Dispatches the shared image into one of its two roles.
#
# Migrations run only in the `web` role. Running them from both containers would
# race on startup and, worse, make it ambiguous which one owns the schema — so
# the bot waits for the database to be migrated instead of migrating it.
set -e

wait_for_db() {
  [ -z "$POSTGRES_DB" ] && return 0
  echo "⏳ waiting for postgres at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432} ..."
  i=0
  until python -c "
import os, socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect((os.environ.get('POSTGRES_HOST', 'db'), int(os.environ.get('POSTGRES_PORT', '5432'))))
except OSError:
    sys.exit(1)
" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
      echo "❌ postgres never came up" >&2
      exit 1
    fi
    sleep 2
  done
  echo "✅ postgres is up"
}

case "$1" in
  web)
    wait_for_db
    python manage.py migrate --noinput
    # 2 workers is plenty: the panel is a single-operator tool, and each worker
    # holds its own long-lived Postgres connection (CONN_MAX_AGE).
    exec gunicorn telgame_site.wsgi:application \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers "${GUNICORN_WORKERS:-2}" \
      --timeout "${GUNICORN_TIMEOUT:-120}" \
      --access-logfile - --error-logfile -
    ;;
  bot)
    wait_for_db
    # The bot must not migrate; it waits until `web` has. Polling the schema is
    # cheaper and clearer than a compose healthcheck on a migration state.
    i=0
    until python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'telgame_site.settings')
django.setup()
from django.db.migrations.executor import MigrationExecutor
from django.db import connections
executor = MigrationExecutor(connections['default'])
raise SystemExit(1 if executor.migration_plan(executor.loader.graph.leaf_nodes()) else 0)
" 2>/dev/null; do
      i=$((i + 1))
      if [ "$i" -ge 60 ]; then
        echo "❌ migrations never finished; is the web container healthy?" >&2
        exit 1
      fi
      echo "⏳ waiting for migrations ..."
      sleep 3
    done
    exec python -m bot.main
    ;;
  shell)
    exec python manage.py shell
    ;;
  manage)
    shift
    exec python manage.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
