"""Tests for the table-preview API (routers/tables.py + services/preview_service.py).

Covers:
- GET /api/envs/{env}/tables: table list with est_rows / n_columns.
- GET /api/envs/{env}/tables/{table}/rows: paginated, truncated row preview.

Determinism notes: all content assertions run against the PRODUCTION mock,
which is seeded with exactly 7 tables, 500 rows in ``data``, and
``lerobot_episode.episodes_uuid_list`` values of 519-779 chars (so every
fetched row contains a truncated cell). The TEST env is volatile (a parallel
task drops/recreates its tables), so it is only shape-checked on /tables.
"""

KNOWN_TABLES = [
    "column_info",
    "data",
    "lerobot_episode",
    "status",
    "status_statistic",
    "step_key",
    "version_info",
]


# ---------------------------------------------------------------- tables list


def test_tables_list_production(client):
    resp = client.get("/api/envs/production/tables")
    assert resp.status_code == 200
    body = resp.json()
    assert body["env"] == "production"
    names = [t["name"] for t in body["tables"]]
    assert names == KNOWN_TABLES
    for t in body["tables"]:
        assert t["n_columns"] is not None and t["n_columns"] > 0
        assert "est_rows" in t
        assert t["est_rows"] is None or t["est_rows"] >= 0


def test_tables_list_test_env_shape_only(client):
    """Test env content is volatile: only the response shape is asserted."""
    resp = client.get("/api/envs/test/tables")
    assert resp.status_code == 200
    body = resp.json()
    assert body["env"] == "test"
    assert isinstance(body["tables"], list)
    for t in body["tables"]:
        assert isinstance(t["name"], str)
        assert "est_rows" in t
        assert "n_columns" in t


def test_tables_list_invalid_env(client):
    resp = client.get("/api/envs/staging/tables")
    assert resp.status_code == 400
    assert "detail" in resp.json()


# ---------------------------------------------------------------- row preview


def test_rows_data_limit_offset(client):
    resp = client.get(
        "/api/envs/production/tables/data/rows", params={"limit": 5, "offset": 10}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["table"] == "data"
    assert body["limit"] == 5
    assert body["offset"] == 10
    assert len(body["columns"]) > 0
    assert len(body["rows"]) == 5
    for row in body["rows"]:
        assert len(row) == len(body["columns"])
        for cell in row:
            assert set(cell.keys()) == {"v", "t"}
            assert isinstance(cell["t"], bool)
    assert "est_rows" in body
    assert body["est_rows"] is None or body["est_rows"] >= 0


def test_rows_default_limit(client):
    resp = client.get("/api/envs/production/tables/data/rows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["rows"]) == 50


def test_rows_limit_clamped_above_200(client):
    resp = client.get(
        "/api/envs/production/tables/data/rows", params={"limit": 500}
    )
    assert resp.status_code == 422


def test_rows_limit_below_1_rejected(client):
    resp = client.get("/api/envs/production/tables/data/rows", params={"limit": 0})
    assert resp.status_code == 422


def test_rows_negative_offset_rejected(client):
    resp = client.get("/api/envs/production/tables/data/rows", params={"offset": -1})
    assert resp.status_code == 422


def test_rows_truncation_lerobot_episode(client):
    """episodes_uuid_list is 519-779 chars -> every row has a truncated cell."""
    resp = client.get("/api/envs/production/tables/lerobot_episode/rows")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) > 0
    truncated = [cell for row in body["rows"] for cell in row if cell["t"]]
    assert truncated, "expected at least one truncated cell"
    for cell in truncated:
        assert isinstance(cell["v"], str)
        assert len(cell["v"]) == 500


def test_rows_order_by_lerobot_uuid_asc(client):
    resp = client.get(
        "/api/envs/production/tables/status/rows",
        params={"order_by": "lerobot_uuid", "order_dir": "asc", "limit": 20},
    )
    assert resp.status_code == 200
    body = resp.json()
    idx = body["columns"].index("lerobot_uuid")
    values = [row[idx]["v"] for row in body["rows"]]
    assert len(values) > 1
    assert values == sorted(values)


def test_rows_order_by_desc(client):
    resp = client.get(
        "/api/envs/production/tables/status/rows",
        params={"order_by": "lerobot_uuid", "order_dir": "desc", "limit": 20},
    )
    assert resp.status_code == 200
    body = resp.json()
    idx = body["columns"].index("lerobot_uuid")
    values = [row[idx]["v"] for row in body["rows"]]
    assert len(values) > 1
    assert values == sorted(values, reverse=True)


def test_rows_order_by_unknown_column(client):
    resp = client.get(
        "/api/envs/production/tables/status/rows",
        params={"order_by": "nonexistent_col"},
    )
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_rows_order_dir_invalid(client):
    resp = client.get(
        "/api/envs/production/tables/status/rows",
        params={"order_by": "lerobot_uuid", "order_dir": "sideways"},
    )
    assert resp.status_code in (400, 422)


def test_rows_unknown_table_404(client):
    resp = client.get("/api/envs/production/tables/no_such_table/rows")
    assert resp.status_code == 404
    assert "detail" in resp.json()


def test_rows_table_injection_never_500(client):
    resp = client.get("/api/envs/production/tables/data%3B%20DROP%20TABLE%20data/rows")
    assert resp.status_code in (400, 404)
    # The real table must be untouched.
    assert client.get("/api/envs/production/tables").status_code == 200


def test_rows_invalid_env(client):
    resp = client.get("/api/envs/staging/tables/data/rows")
    assert resp.status_code == 400
    assert "detail" in resp.json()
