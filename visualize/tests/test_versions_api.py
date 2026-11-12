"""Version progress API tests against the seeded mock PostgreSQL databases."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
import psycopg2
from psycopg2.extras import DictCursor

from app.config import get_conn_params
from app.services import version_service
from app.services.version_service import _compute_durations

MANIFEST = json.loads((Path(__file__).resolve().parents[1] / "scripts/seed_manifest.json").read_text())

PROD_URL = "/api/envs/production/versions/progress"
TEST_URL = "/api/envs/test/versions/progress"

EXPECTED_COUNTS = {
    "download_finish": 50,
    "register_finish": 46,
    "s_step_a_v_1": 24,
    "s_step_a_v_2": 11,
    "s_step_b_v_1": 10,
}

EXPECTED_RATIOS = {
    "download_finish": 1.0,
    "register_finish": 0.92,
}


def _by_key(entries):
    return {entry["key"]: entry for entry in entries}


def test_progress_production_available(client):
    response = client.get(PROD_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["env"] == "production"
    assert body["total_lerobots"] == 50

    available = body["available"]
    # builtin (non-version) keys first in column order, then by step_key/version_no
    assert [a["key"] for a in available] == [
        "download_finish",
        "register_finish",
        "s_step_a_v_1",
        "s_step_a_v_2",
        "s_step_b_v_1",
    ]
    by_key = _by_key(available)
    assert by_key["download_finish"]["step_key"] is None
    assert by_key["download_finish"]["version_no"] is None
    assert by_key["register_finish"]["step_key"] is None
    assert by_key["register_finish"]["version_no"] is None
    assert by_key["s_step_a_v_1"]["step_key"] == "step_a"
    assert by_key["s_step_a_v_1"]["version_no"] == 1
    assert by_key["s_step_a_v_2"]["step_key"] == "step_a"
    assert by_key["s_step_a_v_2"]["version_no"] == 2
    assert by_key["s_step_b_v_1"]["step_key"] == "step_b"
    assert by_key["s_step_b_v_1"]["version_no"] == 1


def test_progress_production_items_unpivoted(client):
    response = client.get(PROD_URL)
    assert response.status_code == 200
    items = _by_key(response.json()["items"])
    assert set(items) == set(EXPECTED_COUNTS)
    for key, count in EXPECTED_COUNTS.items():
        assert items[key]["count"] == (None if key.startswith("s_") else count)
    for key, ratio in EXPECTED_RATIOS.items():
        assert items[key]["ratio"] == ratio

    item = items["s_step_a_v_1"]
    assert item["step_key"] == "step_a"
    assert item["version_no"] == 1
    total_s = sum(dataset["total_duration_s"] for dataset in MANIFEST["datasets"])
    null_bucket = next(b for b in MANIFEST["distributions"]["s_step_a_v_1_c_label"] if b["value"] is None)
    completed_s = total_s - null_bucket["sum_duration_s"]
    assert item["completed_hours"] == pytest.approx(completed_s / 3600)
    assert item["total_hours"] == pytest.approx(total_s / 3600)
    assert item["ratio"] == pytest.approx(round(completed_s / total_s, 6))
    assert item["progress_kind"] == "hours"
    assert item["step_explain"] == "示例步骤A：质量打分"
    assert item["version_note"] == "v1：初版打分"

    download = items["download_finish"]
    assert download["step_key"] is None
    assert download["version_no"] is None
    assert download["step_explain"] is None
    assert download["version_note"] is None
    assert download["progress_kind"] == "count"


def test_progress_keys_filter(client):
    response = client.get(PROD_URL, params={"keys": "s_step_a_v_2,s_step_b_v_1"})
    assert response.status_code == 200
    body = response.json()
    # available is unaffected by the filter
    assert len(body["available"]) == 5
    items = _by_key(body["items"])
    assert set(items) == {"s_step_a_v_2", "s_step_b_v_1"}
    assert items["s_step_a_v_2"]["count"] is None
    assert items["s_step_a_v_2"]["completed_hours"] is not None
    assert items["s_step_a_v_2"]["version_note"] == "v2：打分算法升级"
    assert items["s_step_b_v_1"]["count"] is None
    assert items["s_step_b_v_1"]["completed_hours"] is not None
    assert items["s_step_b_v_1"]["step_explain"] == "示例步骤B：语义打标"
    assert items["s_step_b_v_1"]["version_note"] == "v1：初版打标"


def test_progress_unknown_key_400(client):
    response = client.get(PROD_URL, params={"keys": "bogus_key"})
    assert response.status_code == 400
    assert "bogus_key" in response.json()["detail"]


def test_progress_injection_key_400_not_500(client):
    response = client.get(PROD_URL, params={"keys": "a; DROP TABLE status"})
    assert response.status_code == 400


def test_progress_unknown_env_400(client):
    response = client.get("/api/envs/staging/versions/progress")
    assert response.status_code == 400


def test_progress_test_env_200_or_404(client):
    """Test env is volatile (parallel reset): either state is legitimate."""
    response = client.get(TEST_URL)
    assert response.status_code in (200, 404)
    if response.status_code == 404:
        assert "status_statistic" in response.json()["detail"]


def test_version_hours_count_a_row_once_when_any_column_is_present():
    """A temporary table proves the OR rule without changing persistent data."""
    class TempPool:
        def __init__(self, connection):
            self.connection = connection

        @contextmanager
        def cursor(self, env, dict_cursor=False):
            assert env == "test"
            with self.connection.cursor(cursor_factory=DictCursor) as cur:
                yield cur

    with psycopg2.connect(**get_conn_params("test")) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE data (origin_video_duration_s numeric, a numeric, b text)")
            cur.execute("INSERT INTO data VALUES (3600, 1, NULL), (7200, NULL, 'done'), "
                        "(10800, 2, 'done'), (14400, NULL, NULL)")
        result = _compute_durations(TempPool(conn), "test", {"s_demo_v_1": ["a", "b"]})
    assert result["total_hours"] == 10
    assert result["hours"]["s_demo_v_1"] == 6


def test_exact_duration_cache_and_refresh(client):
    first = client.get(PROD_URL).json()
    second = client.get(PROD_URL, params={"keys": "s_step_b_v_1"}).json()
    assert first["cached_at"] == second["cached_at"]
    assert second["items"][0]["completed_hours"] == pytest.approx(
        _by_key(first["items"])["s_step_b_v_1"]["completed_hours"]
    )
    refreshed = client.get(PROD_URL, params={"refresh": 1}).json()
    assert refreshed["cached_at"] >= first["cached_at"]
    assert refreshed["items"][2]["completed_hours"] == first["items"][2]["completed_hours"]


def test_text_version_number_uses_version_metadata_and_hours(monkeypatch):
    """A real SQL aggregation with text version_no and nonnumeric version_key."""
    class TempPool:
        @contextmanager
        def cursor(self, env, dict_cursor=False):
            assert env == "isolated"
            with connection.cursor(cursor_factory=DictCursor if dict_cursor else None) as cur:
                yield cur

    table_columns = {
        "status_statistic": ["download_finish", "s_quality_v_release-a", "s_missing_v_alpha"],
        "version_info": ["version_key", "step_key", "version_no", "version_note", "created_data_columns"],
        "column_info": ["column_key"],
        "step_key": ["step_key", "step_explain"],
        "data": ["origin_video_duration_s", "s_quality_v_release-a_c_score", "s_quality_v_release-a_c_label"],
    }
    monkeypatch.setattr(version_service, "fetch_table_columns", lambda cur, table: table_columns.get(table, []))

    with psycopg2.connect(**get_conn_params("test")) as connection:
        with connection.cursor() as cur:
            cur.execute('CREATE TEMP TABLE status_statistic (download_finish int, "s_quality_v_release-a" int, s_missing_v_alpha int)')
            cur.execute('INSERT INTO status_statistic VALUES (1, 1, 1)')
            cur.execute('CREATE TEMP TABLE status (id int)')
            cur.execute('INSERT INTO status VALUES (1)')
            cur.execute('CREATE TEMP TABLE step_key (step_key text, step_explain text)')
            cur.execute("INSERT INTO step_key VALUES ('quality', 'quality step')")
            cur.execute('CREATE TEMP TABLE version_info (version_key text, step_key text, version_no text, version_note text, created_data_columns text)')
            cur.execute("INSERT INTO version_info VALUES ('s_quality_v_release-a', 'quality', 'release-a', 'text version', '[score, label]')")
            cur.execute('CREATE TEMP TABLE column_info (column_key text)')
            cur.execute("INSERT INTO column_info VALUES ('s_quality_v_release-a_c_score')")
            cur.execute('CREATE TEMP TABLE data (origin_video_duration_s numeric, "s_quality_v_release-a_c_score" numeric, "s_quality_v_release-a_c_label" text)')
            cur.execute("INSERT INTO data VALUES (3600, 1, NULL), (7200, NULL, 'done'), (10800, 2, 'done'), (14400, NULL, NULL)")
        body = version_service.build_version_progress(TempPool(), "isolated", None, refresh=True)
        selected = version_service.build_version_progress(TempPool(), "isolated", "s_quality_v_release-a")

    items = {item.key: item for item in body.items}
    item = items["s_quality_v_release-a"]
    assert (item.step_key, item.version_no, item.version_note) == ("quality", "release-a", "text version")
    assert item.progress_kind == "hours"
    assert item.count is None
    assert item.completed_hours == 6
    assert item.total_hours == 10
    assert item.ratio == 0.6
    assert [entry.key for entry in selected.items] == ["s_quality_v_release-a"]
    missing = items["s_missing_v_alpha"]
    assert missing.progress_kind == "hours"
    assert missing.count is None
    assert missing.completed_hours is None
    assert missing.ratio is None
