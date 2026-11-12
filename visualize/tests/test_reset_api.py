"""Tests for the reset-test-environment wizard API.

Covers routers/reset.py + services/reset_service.py:
- GET  /api/reset/datasets : production dataset list + default n.
- POST /api/reset/plan     : sampling, volume estimates, warnings.
- POST /api/reset/execute  : drop/rebuild of the TEST schema from production,
  sampled COPY, status_statistic recompute.

Ground truth: the PRODUCTION mock is immutable (50 lerobots / 500 episodes
over ds_alpha(25)/ds_beta(15)/ds_gamma(10); see scripts/seed_manifest.json).
The TEST mock is rebuilt by every successful execute, so order-sensitive
assertions re-seed what they need (legacy_junk) instead of assuming the
initial state.
"""

from contextlib import contextmanager

import psycopg2
from app.config import get_conn_params
from app.services import reset_service
from app.sqlutil import fetch_table_columns, fetch_table_names
from psycopg2 import sql

KNOWN_TABLES = [
    "column_info",
    "data",
    "lerobot_episode",
    "status",
    "status_statistic",
    "step_key",
    "version_info",
]
META_ROWS = {"step_key": 2, "version_info": 3, "column_info": 4}
COPIED_KEYS = {"step_key", "version_info", "column_info", "status", "lerobot_episode", "data"}


@contextmanager
def _db(env):
    """Direct psycopg2 connection to one mock env (commit on clean exit)."""
    conn = psycopg2.connect(**get_conn_params(env))
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _plan(client, dataset_names, n):
    """POST /api/reset/plan and return the parsed 200 body."""
    resp = client.post(
        "/api/reset/plan", json={"dataset_names": dataset_names, "n": n}
    )
    assert resp.status_code == 200
    return resp.json()


def _prod_status_data_counts():
    with _db("production") as conn, conn.cursor() as cur:
        cur.execute("SELECT (SELECT count(*) FROM status), (SELECT count(*) FROM data)")
        return cur.fetchone()


def _expected_status_statistic(cur):
    """{bool_col: count(true)} over the TEST status rows, computed live."""
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'status'
          AND data_type = 'boolean' ORDER BY column_name
        """
    )
    expected = {}
    for (col,) in cur.fetchall():
        cur.execute(
            sql.SQL("SELECT count(*) FROM status WHERE {}").format(sql.Identifier(col))
        )
        expected[col] = cur.fetchone()[0]
    return expected


# ------------------------------------------------------------------ datasets


def test_reset_datasets(client):
    resp = client.get("/api/reset/datasets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_n"] == 10
    assert [(d["dataset_name"], d["lerobot_count"]) for d in body["datasets"]] == [
        ("ds_alpha", 25),
        ("ds_beta", 15),
        ("ds_gamma", 10),
    ]


# ---------------------------------------------------------------------- plan


def test_plan_basic(client):
    body = _plan(client, ["ds_alpha"], 5)
    assert body["dataset_names"] == ["ds_alpha"]
    assert body["n"] == 5
    uuids = body["lerobot_uuids"]
    assert len(uuids) == 5
    assert all(u.startswith("lr-ds_alpha-") for u in uuids)

    vol = body["volumes"]
    assert vol["status_rows"] == 5
    assert vol["lerobot_episode_rows"] == 5
    with _db("production") as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(sum(episode_count), 0) FROM lerobot_episode"
            " WHERE lerobot_uuid = ANY(%s)",
            (uuids,),
        )
        assert vol["episode_rows_est"] == cur.fetchone()[0]
    assert vol["meta_rows"] == META_ROWS


def test_plan_empty_datasets_400(client):
    resp = client.post("/api/reset/plan", json={"dataset_names": [], "n": 5})
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_plan_unknown_dataset_400(client):
    resp = client.post(
        "/api/reset/plan", json={"dataset_names": ["ds_alpha", "ds_nope"], "n": 5}
    )
    assert resp.status_code == 400
    assert "ds_nope" in resp.json()["detail"]


def test_plan_oversample_warns_but_succeeds(client):
    body = _plan(client, ["ds_gamma"], 50)
    assert len(body["lerobot_uuids"]) == 10
    assert "only 10 lerobots available (requested 50)" in body["warnings"]


# ---------------------------------------------------------- execute: invalid


def test_execute_empty_uuids_400(client):
    resp = client.post(
        "/api/reset/execute",
        json={"dataset_names": ["ds_alpha"], "n": 3, "lerobot_uuids": []},
    )
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_execute_uuid_dataset_mismatch_400(client):
    plan = _plan(client, ["ds_alpha"], 3)
    with _db("production") as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT lerobot_uuid FROM status WHERE dataset_name = 'ds_beta' LIMIT 1"
        )
        beta_uuid = cur.fetchone()[0]
    resp = client.post(
        "/api/reset/execute",
        json={
            "dataset_names": ["ds_alpha"],
            "n": 3,
            "lerobot_uuids": plan["lerobot_uuids"][:2] + [beta_uuid],
        },
    )
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_execute_unknown_uuid_400(client):
    resp = client.post(
        "/api/reset/execute",
        json={
            "dataset_names": ["ds_alpha"],
            "n": 3,
            "lerobot_uuids": ["lr-ds_alpha-9999"],
        },
    )
    assert resp.status_code == 400
    assert "detail" in resp.json()


# ------------------------------------------------------------- execute: core


def test_execute_core(client):
    # Re-seed legacy_junk so the drop is observable regardless of test order
    # (other reset runs in this file may have rebuilt the test schema already).
    with _db("test") as conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS legacy_junk (id int)")
        cur.execute("INSERT INTO legacy_junk (id) VALUES (1), (2), (3)")

    plan = _plan(client, ["ds_alpha", "ds_beta"], 10)
    uuids = plan["lerobot_uuids"]
    assert len(uuids) == 10

    resp = client.post(
        "/api/reset/execute",
        json={
            "dataset_names": ["ds_alpha", "ds_beta"],
            "n": 10,
            "lerobot_uuids": uuids,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["duration_s"] is not None and body["duration_s"] >= 0
    assert "legacy_junk" in body["dropped_tables"]
    assert body["created_tables"] == KNOWN_TABLES

    copied = body["copied"]
    assert set(copied) == COPIED_KEYS
    assert copied["status"] == 10
    assert copied["lerobot_episode"] == 10
    assert copied["data"] == plan["volumes"]["episode_rows_est"]
    for table, rows in META_ROWS.items():
        assert copied[table] == rows

    with _db("test") as tconn, tconn.cursor() as tcur, _db(
        "production"
    ) as pconn, pconn.cursor() as pcur:
        # Schema parity: same columns in the same order on every table.
        for table in KNOWN_TABLES:
            assert fetch_table_columns(tcur, table) == fetch_table_columns(
                pcur, table
            ), table

        # Meta tables: full copies, content identical to production.
        for table in META_ROWS:
            tcur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
            test_rows = sorted(map(repr, tcur.fetchall()))
            pcur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
            prod_rows = sorted(map(repr, pcur.fetchall()))
            assert test_rows == prod_rows, table

        # Sampled tables: exactly the submitted uuids.
        tcur.execute("SELECT lerobot_uuid FROM status")
        assert {r[0] for r in tcur.fetchall()} == set(uuids)
        tcur.execute("SELECT count(*) FROM lerobot_episode")
        assert tcur.fetchone()[0] == 10

        # data rows == episodes of the sampled uuids (cross-checked on prod).
        tcur.execute("SELECT count(*) FROM data")
        test_data_rows = tcur.fetchone()[0]
        pcur.execute(
            "SELECT count(*) FROM data WHERE lerobot_uuid = ANY(%s)", (uuids,)
        )
        assert test_data_rows == pcur.fetchone()[0]

        # status_statistic: one row, counts recomputed over the TEST status.
        expected = _expected_status_statistic(tcur)
        tcur.execute("SELECT * FROM status_statistic")
        stat_rows = tcur.fetchall()
        assert len(stat_rows) == 1
        stat_cols = [d.name for d in tcur.description]
        assert dict(zip(stat_cols, stat_rows[0])) == expected
        assert body["status_statistic"] == expected

        # legacy_junk is gone.
        assert "legacy_junk" not in fetch_table_names(tcur)


# -------------------------------------------------------- execute: cap guard


def test_execute_cap_guard(client, monkeypatch):
    # Imported here (not at module top) so the red phase of TDD fails with
    # 404s instead of a collection ImportError while the module doesn't exist.
    from app.services import reset_service

    plan = _plan(client, ["ds_alpha"], 3)
    payload = {
        "dataset_names": ["ds_alpha"],
        "n": 3,
        "lerobot_uuids": plan["lerobot_uuids"],
    }
    monkeypatch.setattr(reset_service, "MAX_COPY_ROWS_EST", 1)

    resp = client.post("/api/reset/execute", json=payload)
    assert resp.status_code == 400
    assert "force" in resp.json()["detail"]

    resp = client.post("/api/reset/execute", json={**payload, "force": True})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ------------------------------------------- execute: safety + re-runnability


def test_execute_leaves_production_untouched(client):
    assert _prod_status_data_counts() == (50, 500)
    plan = _plan(client, ["ds_beta"], 4)
    resp = client.post(
        "/api/reset/execute",
        json={
            "dataset_names": ["ds_beta"],
            "n": 4,
            "lerobot_uuids": plan["lerobot_uuids"],
        },
    )
    assert resp.status_code == 200
    assert _prod_status_data_counts() == (50, 500)


def test_execute_is_rerunnable(client):
    for _ in range(2):
        plan = _plan(client, ["ds_alpha"], 3)
        resp = client.post(
            "/api/reset/execute",
            json={
                "dataset_names": ["ds_alpha"],
                "n": 3,
                "lerobot_uuids": plan["lerobot_uuids"],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


def test_env_targets_equal_compares_host_port_dbname():
    prod = {"host": "h", "port": 5432, "dbname": "d", "user": "reader"}
    assert reset_service.env_targets_equal(prod, dict(prod)) is True
    assert not reset_service.env_targets_equal(prod, {**prod, "port": 5433})
    assert not reset_service.env_targets_equal(prod, {**prod, "dbname": "other"})


def test_execute_refused_when_test_env_points_at_production(client, monkeypatch):
    monkeypatch.setattr(
        reset_service, "get_conn_params", lambda env: {"host": "h", "port": 1, "dbname": "d"}
    )
    resp = client.post(
        "/api/reset/execute",
        json={
            "dataset_names": ["ds_alpha"],
            "n": 1,
            "lerobot_uuids": ["lr-ds_alpha-0001"],
        },
    )
    assert resp.status_code == 500
    assert "same database" in resp.json()["detail"]
