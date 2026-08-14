# Single image, two entrypoints: the same code runs the Telegram bot and the web
# panel, so they can never drift out of sync on the VPS. docker-compose picks
# which one a container becomes via its `command`.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# psycopg[binary] ships its own libpq, so no build-essential/libpq-dev here.
# `curl` is only for the compose healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# requirements first so a code change doesn't invalidate the dependency layer
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Collected here at build time so the web container starts serving immediately;
# whitenoise reads from this directory.
ENV DJANGO_STATIC_ROOT=/app/staticfiles
RUN DJANGO_SECRET_KEY=build-only DJANGO_DEBUG=false \
    python manage.py collectstatic --noinput

# Backups are written here; compose mounts a named volume over it so they
# outlive the container.
RUN mkdir -p /app/backups

# Non-root: the app never needs to write outside /app/backups.
RUN useradd --create-home --uid 10001 kaiju \
 && chown -R kaiju:kaiju /app
USER kaiju

COPY --chown=kaiju:kaiju docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
