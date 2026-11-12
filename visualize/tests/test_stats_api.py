"""Tests for the /api/stats/* pivot-statistics endpoints (production mock DB).

Ground truth is ``visualize/scripts/seed_manifest.json`` (produced by
``seed_mock.sql``); every assertion below is derived from that manifest, never
hand-copied from the implementation under test.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

MANIFEST = json.loads(
    (Path(__file__).resolve().parent.parent / "scripts" / "seed_manifest.json").read_text(
        encoding="utf-8"
    )
)
DATASETS = {d["dataset_name"]: d for d in MANIFEST["datasets"]}
DISTRIBUTIONS = MANIFEST["distributions"]
TOTAL_DURATION_S = sum(d["total_duration_s"] for d in DATASETS.values())

DATASET_URL = "/api/stats/datasets"
COLUMNS_URL = "/api/stats/columns"
DIST_URL = "/api/stats/column-distribution"


# ---------------------------------------------------------------------------
# GET /api/stats/datasets
# ---------------------------------------------------------------------------


def test_datasets_totals_and_pies_match_manifest(client):
    response = client.get(DATASET_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["cached_at"]

    total_lerobots = sum(d["lerobot_count"] for d in DATASETS.values())
    total_episodes = sum(d["episode_count"] for d in DATASETS.values())
    assert body["totals"]["lerobots"] == total_lerobots == 50
    assert body["totals"]["episodes"] == total_episodes == 500
    assert body["totals"]["hours"] == pytest.approx(TOTAL_DURATION_S / 3600)

    # pie name -> (manifest field giving the per-dataset value, pie total)
    pies = {
        "lerobots": ("lerobot_count", total_lerobots),
        "episodes": ("episode_count", total_episodes),
        "hours": ("total_duration_s", TOTAL_DURATION_S),
    }
    for pie_name, (field, pie_total) in pies.items():
        items = {item["dataset_name"]: item for item in body[pie_name]}
        assert set(items) == set(DATASETS), pie_name
        for name, manifest_row in DATASETS.items():
            raw = manifest_row[field]
            expected_value = raw / 3600 if pie_name == "hours" else raw
            item = items[name]
            assert item["value"] == pytest.approx(expected_value), (pie_name, name)
            assert item["ratio"] == pytest.approx(round(raw / pie_total, 6)), (pie_name, name)
        assert sum(item["ratio"] for item in items.values()) == pytest.approx(1.0), pie_name


# ---------------------------------------------------------------------------
# GET /api/stats/columns
# ---------------------------------------------------------------------------


def test_columns_static_then_dynamic_with_notes(client):
    response = client.get(COLUMNS_URL)
    assert response.status_code == 200
    columns = response.json()["columns"]
    assert columns, "expected a non-empty column list for table 'data'"

    by_name = {c["name"]: c for c in columns}
    assert by_name["has_wrist_cam"]["kind"] == "static"
    assert by_name["has_wrist_cam"]["note"] is None
    assert by_name["s_step_a_v_1_c_label"]["kind"] == "dynamic"
    assert by_name["s_step_a_v_1_c_label"]["note"] == "[text] 质量标签"

    # static block first, then dynamic block
    kinds = [c["kind"] for c in columns]
    assert kinds == sorted(kinds, key=lambda k: k != "static")


# ---------------------------------------------------------------------------
# GET /api/stats/column-distribution
# ---------------------------------------------------------------------------


def test_distribution_has_wrist_cam_matches_manifest(client):
    response = client.get(DIST_URL, params={"column": "has_wrist_cam"})
    assert response.status_code == 200
    body = response.json()
    assert body["column"] == "has_wrist_cam"
    assert body["cached_at"]

    expected = {b["value"]: b for b in DISTRIBUTIONS["has_wrist_cam"]}
    assert len(body["items"]) == len(expected) == 2
    assert body["other"] is None

    total_rows = sum(b["rows"] for b in expected.values())
    total_hours = sum(b["sum_duration_s"] for b in expected.values()) / 3600
    assert body["total_rows"] == total_rows
    assert body["total_hours"] == pytest.approx(total_hours)

    items = {item["value"]: item for item in body["items"]}
    for value, bucket in expected.items():
        item = items[value]
        assert item["rows"] == bucket["rows"]
        assert item["hours"] == pytest.approx(bucket["sum_duration_s"] / 3600)
        assert item["ratio_rows"] == pytest.approx(round(bucket["rows"] / total_rows, 6))
        assert item["ratio_hours"] == pytest.approx(
            round(bucket["sum_duration_s"] / 3600 / total_hours, 6)
        )
    # explicit check from the task contract
    assert items[True]["ratio_hours"] == pytest.approx(19090 / 39640)


def test_distribution_label_has_null_bucket_and_full_row_count(client):
    response = client.get(DIST_URL, params={"column": "s_step_a_v_1_c_label"})
    assert response.status_code == 200
    body = response.json()

    expected = {b["value"]: b for b in DISTRIBUTIONS["s_step_a_v_1_c_label"]}
    assert len(body["items"]) == len(expected) == 4
    assert body["other"] is None

    items = {item["value"]: item for item in body["items"]}
    assert None in items, "NULL must surface as a bucket with value=null"
    for value, bucket in expected.items():
        item = items[value]
        assert item["rows"] == bucket["rows"]
        assert item["hours"] == pytest.approx(bucket["sum_duration_s"] / 3600)
    assert items[None]["rows"] == 256
    assert sum(item["rows"] for item in body["items"]) == 500


def test_distribution_order_follows_weight_measure(client):
    column = "s_step_a_v_1_c_label"
    by_rows = client.get(DIST_URL, params={"column": column, "weight": "rows"})
    by_hours = client.get(DIST_URL, params={"column": column, "weight": "hours"})
    assert by_rows.status_code == 200
    assert by_hours.status_code == 200

    rows_items = by_rows.json()["items"]
    hours_items = by_hours.json()["items"]
    assert {i["value"] for i in rows_items} == {i["value"] for i in hours_items}

    rows_seq = [i["rows"] for i in rows_items]
    hours_seq = [i["hours"] for i in hours_items]
    assert rows_seq == sorted(rows_seq, reverse=True)
    assert hours_seq == sorted(hours_seq, reverse=True)

    # expected per-measure order derived independently from the manifest
    buckets = DISTRIBUTIONS[column]
    expected_rows_order = [
        b["value"] for b in sorted(buckets, key=lambda b: b["rows"], reverse=True)
    ]
    expected_hours_order = [
        b["value"] for b in sorted(buckets, key=lambda b: b["sum_duration_s"], reverse=True)
    ]
    assert [i["value"] for i in rows_items] == expected_rows_order
    assert [i["value"] for i in hours_items] == expected_hours_order


def test_distribution_top_k_aggregates_remainder_into_other(client):
    column = "s_step_a_v_1_c_label"
    response = client.get(
        DIST_URL, params={"column": column, "weight": "rows", "top_k": 2}
    )
    assert response.status_code == 200
    body = response.json()

    buckets = sorted(
        DISTRIBUTIONS[column], key=lambda b: b["rows"], reverse=True
    )
    top2, rest = buckets[:2], buckets[2:]
    assert len(body["items"]) == 2
    assert [i["value"] for i in body["items"]] == [b["value"] for b in top2]

    other = body["other"]
    assert other is not None
    assert other["value"] == "__other__"
    assert other["rows"] == sum(b["rows"] for b in rest)
    assert other["hours"] == pytest.approx(
        sum(b["sum_duration_s"] for b in rest) / 3600
    )
    # totals still cover every bucket, not just the visible ones
    assert body["total_rows"] == sum(b["rows"] for b in buckets) == 500


def test_distribution_rejects_sql_injection_in_column(client):
    response = client.get(DIST_URL, params={"column": "episode_uuid; DROP TABLE data"})
    assert response.status_code == 400


def test_distribution_rejects_unknown_column(client):
    response = client.get(DIST_URL, params={"column": "no_such_col"})
    assert response.status_code == 400


def test_distribution_rejects_unknown_weight(client):
    response = client.get(
        DIST_URL, params={"column": "has_wrist_cam", "weight": "bogus"}
    )
    assert response.status_code in (400, 422)


def test_distribution_caches_and_refresh_repopulates(client):
    params = {"column": "has_wrist_cam", "weight": "hours", "top_k": 5}
    first = client.get(DIST_URL, params=params)
    second = client.get(DIST_URL, params=params)
    assert first.status_code == 200
    assert second.status_code == 200
    cached_at_1 = datetime.fromisoformat(first.json()["cached_at"])
    cached_at_2 = datetime.fromisoformat(second.json()["cached_at"])
    assert cached_at_1 == cached_at_2, "same params must be served from the TTL cache"

    refreshed = client.get(DIST_URL, params={**params, "refresh": 1})
    assert refreshed.status_code == 200
    cached_at_3 = datetime.fromisoformat(refreshed.json()["cached_at"])
    assert cached_at_3 >= cached_at_1, "refresh=1 must repopulate the cache"

    # and the refreshed entry is what subsequent plain calls see
    fourth = client.get(DIST_URL, params=params)
    assert datetime.fromisoformat(fourth.json()["cached_at"]) == cached_at_3
