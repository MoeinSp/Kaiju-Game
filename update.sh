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

echo "==> applying migrations"
docker compose exec -T web python manage.py migrate --noinput

echo "==> done: now on $(git rev-parse --short HEAD)"
docker compose ps --format "  {{.Name}}: {{.Status}}"
