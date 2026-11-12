"""Configuration loading for the visualize service.

The DBInfo JSON file holds one section per environment::

    {
        "production_env": {"host": ..., "port": ..., "user": ..., "password": ..., "dbname": ...},
        "test_env":       {"host": ..., "port": ..., "user": ..., "password": ..., "dbname": ...}
    }

DOMAIN POLICY (binding): the production environment is ALWAYS connected with
user ``reader`` / password ``postgres`` regardless of the credentials stored in
the file; only host/port/dbname are taken from the file. The test environment
uses the file's user/password as-is.

The DBInfo path is resolved in this order: explicit ``path`` argument, then the
``VISUALIZE_DBINFO`` environment variable, then the ``DBInfo.json`` at the
project root (derived from this package's location, so the project runs from
any directory).
"""

import json
import os
from pathlib import Path
from typing import Literal

# <project root>/DBInfo.json, where <project root> is two levels above this
# file (visualize/app/config.py -> visualize/ -> <project root>).
DEFAULT_DBINFO_PATH = str(Path(__file__).resolve().parents[2] / "DBInfo.json")
ENV_VAR = "VISUALIZE_DBINFO"

PRODUCTION_USER = "reader"
PRODUCTION_PASSWORD = "postgres"

# env name -> DBInfo section key
_ENV_SECTIONS = {"production": "production_env", "test": "test_env"}
_REQUIRED_KEYS = ("host", "port", "dbname", "user", "password")


class ConfigError(Exception):
    """Raised when the DBInfo file or an environment name is invalid."""


def load_dbinfo(path: str | None = None) -> dict:
    """Load and return the DBInfo JSON document.

    Resolution order: explicit ``path`` argument, ``VISUALIZE_DBINFO`` env var,
    then the default path. Raises ConfigError with a clear message when the
    file is missing, unreadable, or not a JSON object.
    """
    resolved = path or os.environ.get(ENV_VAR) or DEFAULT_DBINFO_PATH
    try:
        with open(resolved, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"DBInfo file not found: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"DBInfo file is not valid JSON: {resolved}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"DBInfo file is not readable: {resolved}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"DBInfo file must contain a JSON object: {resolved}")
    return data


def get_conn_params(env: Literal["production", "test"]) -> dict:
    """Return psycopg2 connection params for one environment.

    Keys: host, port, dbname, user, password.

    - ``production``: host/port/dbname from the DBInfo file, but user/password
      are ALWAYS ``reader`` / ``postgres`` (read-only policy).
    - ``test``: all five values from the DBInfo file as-is.

    Raises ConfigError for an unknown env name, a missing section, or missing
    keys inside the section.
    """
    try:
        section_key = _ENV_SECTIONS[env]
    except KeyError:
        expected = sorted(_ENV_SECTIONS)
        raise ConfigError(
            f"unknown env: {env!r} (expected one of {expected})"
        ) from None

    section = load_dbinfo().get(section_key)
    if not isinstance(section, dict):
        raise ConfigError(f"DBInfo missing object section {section_key!r}")
    missing = [key for key in _REQUIRED_KEYS if key not in section]
    if missing:
        raise ConfigError(f"DBInfo section {section_key!r} missing keys: {missing}")

    params = {
        "host": section["host"],
        "port": section["port"],
        "dbname": section["dbname"],
        "user": section["user"],
        "password": section["password"],
    }
    if env == "production":
        params["user"] = PRODUCTION_USER
        params["password"] = PRODUCTION_PASSWORD
    return params
