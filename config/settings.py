import math
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# One folder per studied language, each with a pack.yml (core.language_packs).
# Not Django's LANGUAGES setting, which lists interface translations.
LANGUAGES_DIR = BASE_DIR / "languages"


def env_list(name: str, default: str = "") -> list[str]:
    return [value.strip() for value in os.environ.get(name, default).split(",") if value.strip()]


DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}
_DEV_DEFAULT_SECRET_KEY = "local-dev-only-change-me"


def _get_secret_key(*, debug: bool) -> str:
    configured_key = os.environ.get("DJANGO_SECRET_KEY", "")
    if debug:
        return configured_key or _DEV_DEFAULT_SECRET_KEY

    is_unsafe = (
        len(configured_key) < 50
        or len(set(configured_key)) < 5
        or configured_key.startswith("django-insecure-")
    )
    if is_unsafe:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set to a long, random secret when "
            "DJANGO_DEBUG is false (at least 50 characters and 5 unique characters; "
            "values prefixed with 'django-insecure-' are rejected)."
        )
    return configured_key


SECRET_KEY = _get_secret_key(debug=DEBUG)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "decks",
    "cards",
    "reviews",
    "imports_app",
    "video_study",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.language_modes.language_mode_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Production runs one gunicorn box with several threads plus a background
# catalog refresh, all against a single SQLite file: WAL lets readers and the
# one writer proceed concurrently, IMMEDIATE transactions fail fast instead of
# deadlocking on upgrade, and the timeout gives a blocked writer room to wait
# for the busy one rather than erroring immediately.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

# Fly.io terminates HTTPS and forwards the original protocol in this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = int(
    os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "3600" if not DEBUG else "0")
)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
# Django's default "same-origin" sends no Referer to cross-origin requests, and
# the embedded YouTube player then fails with error 153 in Safari and Firefox.
# This value sends only the origin, never the path or query string.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Translation providers receive only the word or phrase explicitly selected by
# the user. Unconfigured providers are skipped and MyMemory remains the final
# no-key fallback.
VIDEO_STUDY_TRANSLATION_PROVIDERS = env_list(
    "VIDEO_STUDY_TRANSLATION_PROVIDERS",
    "wiktionary,deepl,libretranslate,mymemory",
)
VIDEO_STUDY_TRANSLATION_TIMEOUT = int(os.environ.get("VIDEO_STUDY_TRANSLATION_TIMEOUT", "8"))
# Total seconds one translation request may spend across all providers.
VIDEO_STUDY_TRANSLATION_BUDGET = int(os.environ.get("VIDEO_STUDY_TRANSLATION_BUDGET", "12"))
VIDEO_STUDY_DICTIONARY_ENDPOINT = os.environ.get(
    "VIDEO_STUDY_DICTIONARY_ENDPOINT", "https://de.wiktionary.org/w/api.php"
)
VIDEO_STUDY_NL_DICTIONARY_ENDPOINT = os.environ.get(
    "VIDEO_STUDY_NL_DICTIONARY_ENDPOINT", "https://en.wiktionary.org/w/api.php"
)
VIDEO_STUDY_TRANSLATION_EMAIL = os.environ.get("VIDEO_STUDY_TRANSLATION_EMAIL", "")
VIDEO_STUDY_DEEPL_API_KEY = os.environ.get("VIDEO_STUDY_DEEPL_API_KEY", "")
VIDEO_STUDY_DEEPL_ENDPOINT = os.environ.get(
    "VIDEO_STUDY_DEEPL_ENDPOINT", "https://api-free.deepl.com/v2/translate"
)
VIDEO_STUDY_LIBRETRANSLATE_ENDPOINT = os.environ.get("VIDEO_STUDY_LIBRETRANSLATE_ENDPOINT", "")
VIDEO_STUDY_LIBRETRANSLATE_API_KEY = os.environ.get("VIDEO_STUDY_LIBRETRANSLATE_API_KEY", "")
# Caption-only routing; never expose this credential-bearing URL in errors.
VIDEO_STUDY_YOUTUBE_PROXY_URL = os.environ.get("VIDEO_STUDY_YOUTUBE_PROXY_URL", "")
DEFAULT_EXCEPTION_REPORTER_FILTER = "config.error_reporting.ProxySafeExceptionReporterFilter"
try:
    VIDEO_STUDY_YOUTUBE_TIMEOUT = float(os.environ.get("VIDEO_STUDY_YOUTUBE_TIMEOUT", "15"))
    if not math.isfinite(VIDEO_STUDY_YOUTUBE_TIMEOUT) or VIDEO_STUDY_YOUTUBE_TIMEOUT <= 0:
        raise ValueError
except ValueError:
    raise ImproperlyConfigured("VIDEO_STUDY_YOUTUBE_TIMEOUT must be positive and finite.") from None
try:
    VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS = int(
        os.environ.get("VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS", "3")
    )
    if VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS < 0:
        raise ValueError
except ValueError:
    raise ImproperlyConfigured(
        "VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS must be a nonnegative integer."
    ) from None

# Gates video_study.services.start_kurzgesagt_prefetch, the background thread
# that caches the Kurzgesagt video of the day's transcript ahead of time.
# TEST_RUNNER below forces this off for the whole test suite regardless of
# this env var, so tests never reach YouTube from a dashboard render.
VIDEO_STUDY_TRANSCRIPT_PREFETCH = os.environ.get(
    "VIDEO_STUDY_TRANSCRIPT_PREFETCH", "true"
).lower() in {"1", "true", "yes"}

# Gates the implicit catalog refresh in video_study.services.get_channel_video_of_day
# and get_podcast_of_day (and the empty-catalog bootstrap in the podcast
# workspace view): with it off, an empty catalog yields no item instead of a
# network fetch. Explicit refreshes (the refresh_media_catalogs command, the
# workspace's ?refresh=1) are not affected. TEST_RUNNER forces it off too.
VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH = os.environ.get(
    "VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH", "true"
).lower() in {"1", "true", "yes"}

# See VIDEO_STUDY_TRANSCRIPT_PREFETCH above.
TEST_RUNNER = "config.test_runner.PrefetchDisabledTestRunner"

# simplemma's default dictionary factory loads the German dictionary as a
# plain Python dict (~150 MB resident); on the 256 MB production machine that
# OOM-kills gunicorn. A prebuilt marisa-trie cache (~7 MB to load) avoids it,
# but the trie must be built at Docker image build time (build_lemma_cache),
# never on the machine, since building it peaks at ~350 MB.
LEMMA_CACHE_DIR = os.environ.get("LEMMA_CACHE_DIR", str(BASE_DIR / "lemma_cache"))
# Production images set this false so a missing or corrupt cache fails
# closed (word capture returns tokens unlemmatized) instead of building the
# trie in-process, which would OOM-kill the machine.
LEMMA_CACHE_BUILD_ALLOWED = os.environ.get(
    "LEMMA_CACHE_BUILD_ALLOWED", "true" if DEBUG else "false"
).lower() in {"1", "true", "yes"}

# Scheduled backups (core.backup, core.backup_remote, core.backup_scheduler).
# Values stay raw strings on purpose: core.backup.BackupConfig parses and
# validates them when a backup runs, so a typo here can never stop the web
# app from booting. The local timer defaults on; the off-site upload is opt-in
# and does nothing until BACKUP_REMOTE_ENABLED is true and every S3 value is
# valid. See docs/backups.md.
BACKUP_DIR = os.environ.get("BACKUP_DIR", "")  # default: backups/ next to the database
BACKUP_SCHEDULER_ENABLED = os.environ.get("BACKUP_SCHEDULER_ENABLED", "true")
BACKUP_INTERVAL_HOURS = os.environ.get("BACKUP_INTERVAL_HOURS", "24")
BACKUP_CHECK_SECONDS = os.environ.get("BACKUP_CHECK_SECONDS", "60")
BACKUP_RETRY_SECONDS = os.environ.get("BACKUP_RETRY_SECONDS", "900")
BACKUP_KEEP_LOCAL = os.environ.get("BACKUP_KEEP_LOCAL", "7")
BACKUP_REMOTE_ENABLED = os.environ.get("BACKUP_REMOTE_ENABLED", "false")
BACKUP_S3_ENDPOINT = os.environ.get("BACKUP_S3_ENDPOINT", "")  # empty: AWS for the region
BACKUP_S3_REGION = os.environ.get("BACKUP_S3_REGION", "")
BACKUP_S3_BUCKET = os.environ.get("BACKUP_S3_BUCKET", "")
BACKUP_S3_PREFIX = os.environ.get("BACKUP_S3_PREFIX", "")
BACKUP_S3_ADDRESSING = os.environ.get("BACKUP_S3_ADDRESSING", "")  # "", "path" or "virtual"
BACKUP_S3_ACCESS_KEY_ID = os.environ.get("BACKUP_S3_ACCESS_KEY_ID", "")
BACKUP_S3_SECRET_ACCESS_KEY = os.environ.get("BACKUP_S3_SECRET_ACCESS_KEY", "")
BACKUP_S3_SESSION_TOKEN = os.environ.get("BACKUP_S3_SESSION_TOKEN", "")
BACKUP_UPLOAD_TIMEOUT_SECONDS = os.environ.get("BACKUP_UPLOAD_TIMEOUT_SECONDS", "300")

_LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "INFO")

# Fly captures stdout and adds its own timestamps, so the console format omits
# ours. django.db.backends is deliberately not configured here: it logs every
# SQL statement at DEBUG and is far too noisy for routine use.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "format": "{levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "console",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": _LOG_LEVEL,
    },
    "loggers": {
        "django.request": {
            "level": "WARNING",
            "propagate": True,
        },
        "video_study": {"level": _LOG_LEVEL},
        "core": {"level": _LOG_LEVEL},
        "reviews": {"level": _LOG_LEVEL},
        "cards": {"level": _LOG_LEVEL},
        "decks": {"level": _LOG_LEVEL},
        "imports_app": {"level": _LOG_LEVEL},
    },
}
