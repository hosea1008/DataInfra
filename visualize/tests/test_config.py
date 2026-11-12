"""Tests for app.config: DBInfo loading and per-env connection parameters."""

import json

import pytest
from app.config import ConfigError, get_conn_params, load_dbinfo

DBINFO_PAYLOAD = {
    "production_env": {
        "host": "prod.example.com",
        "port": 5433,
        "user": "postgres",
        "password": "file_secret",
        "dbname": "proddb",
    },
    "test_env": {
        "host": "test.example.com",
        "port": 5434,
        "user": "testuser",
        "password": "testpass",
        "dbname": "testdb",
    },
}


@pytest.fixture
def dbinfo_file(tmp_path, monkeypatch):
    """Write the payload to a tmp file and point VISUALIZE_DBINFO at it."""
    path = tmp_path / "dbinfo.json"
    path.write_text(json.dumps(DBINFO_PAYLOAD), encoding="utf-8")
    monkeypatch.setenv("VISUALIZE_DBINFO", str(path))
    return path


def test_production_forces_reader_credentials(dbinfo_file):
    """Production ignores file user/password: always reader/postgres."""
    params = get_conn_params("production")
    assert params == {
        "host": "prod.example.com",
        "port": 5433,
        "dbname": "proddb",
        "user": "reader",
        "password": "postgres",
    }


def test_test_env_passes_file_values_through(dbinfo_file):
    """Test env uses all five values from the file as-is."""
    params = get_conn_params("test")
    assert params == {
        "host": "test.example.com",
        "port": 5434,
        "dbname": "testdb",
        "user": "testuser",
        "password": "testpass",
    }


def test_env_var_override_is_honored(dbinfo_file):
    """VISUALIZE_DBINFO points load_dbinfo() at the given file."""
    assert load_dbinfo() == DBINFO_PAYLOAD


def test_explicit_path_argument_wins(dbinfo_file, monkeypatch):
    """An explicit path argument is used even without the env var."""
    monkeypatch.delenv("VISUALIZE_DBINFO")
    assert load_dbinfo(str(dbinfo_file)) == DBINFO_PAYLOAD


def test_missing_file_raises_config_error(tmp_path, monkeypatch):
    monkeypatch.setenv("VISUALIZE_DBINFO", str(tmp_path / "does-not-exist.json"))
    with pytest.raises(ConfigError, match="not found"):
        load_dbinfo()


def test_invalid_json_raises_config_error(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("VISUALIZE_DBINFO", str(bad))
    with pytest.raises(ConfigError):
        load_dbinfo()


def test_unknown_env_raises_config_error(dbinfo_file):
    with pytest.raises(ConfigError, match="unknown env"):
        get_conn_params("staging")
