import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-secret-change-me")
DEBUG = _env_bool("DJANGO_DEBUG", "true")
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]

# Django 4+ requires the scheme-qualified origin for cross-origin POSTs. Behind a
# reverse proxy on a VPS the panel is served over https on a real domain, and
# without this every form submit fails CSRF with a confusing error.
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "bio_lab",
    "panel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # serves the admin/panel static files without a separate nginx location block
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "telgame_site.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "telgame_site.wsgi.application"
ASGI_APPLICATION = "telgame_site.asgi.application"

# Postgres in production, SQLite for local dev and the smoke tests.
#
# The switch is POSTGRES_DB: set it (as docker-compose does) and the whole app —
# bot and panel alike — talks to Postgres. Leave it unset and nothing about the
# existing SQLite workflow changes, which is what keeps DJANGO_DB_PATH working
# for the throwaway test databases.
if os.environ.get("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ.get("POSTGRES_USER", "kaiju"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "db"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            # one long-lived connection per worker; the bot is a single process
            # that would otherwise reconnect on every handler
            "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "600")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "game.db")),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fa"
# The game's day boundary is Tehran midnight, not UTC — daily missions, login
# streaks and season rollovers all key off local dates, and players reasonably
# expect "a new day" to start when their own clock says so. USE_TZ stays on, so
# timestamps are still stored in UTC; only date arithmetic and presentation are
# local, via timezone.localdate() / localtime(), which read this setting.
TIME_ZONE = os.environ.get("GAME_TIMEZONE", "Asia/Tehran")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = os.environ.get("DJANGO_STATIC_ROOT", str(BASE_DIR / "staticfiles"))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Where game/backup.py writes archives. On the VPS this is a mounted volume, so
# backups survive `docker compose down` — the whole point of taking them.
BACKUP_DIR = os.environ.get("BACKUP_DIR", str(BASE_DIR / "backups"))

# Uploaded backups are read straight from the request; keeping them in memory up
# to 32 MB avoids a temp-file round trip for all realistic archive sizes.
FILE_UPLOAD_MAX_MEMORY_SIZE = 32 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024 * 1024

LOGIN_URL = "panel:login"
LOGIN_REDIRECT_URL = "panel:dashboard"
LOGOUT_REDIRECT_URL = "panel:login"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Behind a reverse proxy that terminates TLS, Django only knows the request was
# https via this header — without it secure cookies never get set and redirects
# come back as http://.
if _env_bool("DJANGO_BEHIND_PROXY"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

if not DEBUG:
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = False  # the panel posts plain forms, JS never reads it
    SESSION_COOKIE_SECURE = _env_bool("DJANGO_SECURE_COOKIES", "true")
    CSRF_COOKIE_SECURE = _env_bool("DJANGO_SECURE_COOKIES", "true")
    X_FRAME_OPTIONS = "DENY"
