"""Rutas y constantes compartidas."""

import os
from pathlib import Path

API_BASE = "https://health.googleapis.com/v4"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

SCOPE_PREFIX = "https://www.googleapis.com/auth/googlehealth."
SCOPES = [
    SCOPE_PREFIX + "activity_and_fitness.readonly",
    SCOPE_PREFIX + "health_metrics_and_measurements.readonly",
    SCOPE_PREFIX + "sleep.readonly",
]


def data_dir() -> Path:
    """Carpeta con la base de datos, el token y los informes (FITBIT_AIR_HOME)."""
    d = Path(os.environ.get("FITBIT_AIR_HOME", Path.home() / ".fitbit-air"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "health.db"


def token_path() -> Path:
    return data_dir() / "token.json"


def client_secret_path() -> Path:
    return data_dir() / "client_secret.json"


def settings_path() -> Path:
    return data_dir() / "settings.json"
