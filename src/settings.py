import os

APP_VERSION = os.environ.get("APP_VERSION", "0.0.0")
SENTRY_DSN = os.getenv("SENTRY_DSN", default=None)
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_SA_LEVEL = os.getenv("LOG_SA_LEVEL", default="WARNING")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)s] %(message)s",
            "datefmt": "%d.%m.%Y %H:%M:%S",
        },
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "loggers": {
        "uvicorn": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "modules": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "common": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "app": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "yt_dlp": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "rq.worker": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "sqlalchemy.engine": {"handlers": ["console"], "level": LOG_SA_LEVEL, "propagate": False},
        "jinja2": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
