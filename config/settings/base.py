import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = [x for x in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if x]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "django.contrib.sitemaps",
    "apps.core", "apps.football", "apps.ingestion", "apps.scoring", "apps.rankings",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "apps.core.context_processors.metadata"]}}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql"):
    import urllib.parse
    _db = urllib.parse.urlparse(DATABASE_URL)
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": _db.path.lstrip("/"), "USER": _db.username, "PASSWORD": _db.password, "HOST": _db.hostname, "PORT": _db.port or 5432}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
REDIS_URL = os.getenv("REDIS_URL", "")
CACHES = {"default": {"BACKEND": "django_redis.cache.RedisCache", "LOCATION": REDIS_URL, "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"}}} if REDIS_URL else {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
FORCE_SCRIPT_NAME = os.getenv("DJANGO_FORCE_SCRIPT_NAME") or None
STATIC_URL = f"{FORCE_SCRIPT_NAME.rstrip('/')}/static/" if FORCE_SCRIPT_NAME else "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
FOOTBALL_PROVIDER = os.getenv("FOOTBALL_PROVIDER", "mock")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE_URL = os.getenv("API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io")
API_FOOTBALL_REQUEST_INTERVAL_SECONDS = float(os.getenv("API_FOOTBALL_REQUEST_INTERVAL_SECONDS", "6.1"))
FORMULA_VERSION = os.getenv("FORMULA_VERSION", "1.2")
DEFAULT_SEASON_SLUG = os.getenv("DEFAULT_SEASON_SLUG", "2026-27")
PUBLIC_RANKING_WEEKDAY = int(os.getenv("PUBLIC_RANKING_WEEKDAY", "0"))
PUBLIC_RANKING_HOUR_UTC = int(os.getenv("PUBLIC_RANKING_HOUR_UTC", "6"))
CACHE_TTL = 86400
MERIT_PRODUCTION_ALERTS = os.getenv("MERIT_PRODUCTION_ALERTS", "False").lower() == "true"
MERIT_ALERT_EMAIL = os.getenv("MERIT_ALERT_EMAIL", "merit@tuloss.com")
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "False").lower() == "true"
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "False").lower() == "true"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Merit <merit@tuloss.com>")
