#!/bin/sh
# Pull the latest code from GitHub and redeploy. Safe to re-run.
#
#   ./update.sh
#
# Rebuilds the shared image, recreates the web + bot containers (so the bot
# reloads with the new code), and applies any new migrations. Your .env and
# docker-compose.override.yml are untracked, so a pull never touches them.
set -e
cd "$(dirname "$0")"

echo "==> pulling latest code"
git pull --ff-only

echo "==> rebuilding and restarting containers"
docker compose up -d --build

# The web container's entrypoint already runs migrations on startup, and the bot
# waits for them before serving. Running `migrate` here too used to race that
# startup migrate (both trying to CREATE a new table at once → IntegrityError), so
# we just wait for the entrypoint's migrate to settle, then show the final state.
echo "==> applying migrations (via the web container's entrypoint)"
sleep 8
docker compose exec -T web python manage.py migrate --noinput 2>&1 | tail -3 || true

echo "==> done: now on $(git rev-parse --short HEAD)"
docker compose ps --format "  {{.Name}}: {{.Status}}"
