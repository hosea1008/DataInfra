"""Tests for GET /api/envs/{env}/versions/progress (version-progress feature).

Value assertions run against the production mock DB only (ground truth:
visualize/scripts/seed_manifest.json - status_statistic is a single row with
download_finish=50, register_finish=46, s_step_a_v_1=24, s_step_a_v_2=11,
s_step_b_v_1=10; status holds 50 rows). The test env's tables are volatile
(a parallel reset task drops/recreates them), so the test-env case only
accepts 200 or 404.
"""

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
    "s_step_a_v_1": 0.48,
    "s_step_a_v_2": 0.22,
    "s_step_b_v_1": 0.2,
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
        assert items[key]["count"] == count
        assert items[key]["ratio"] == EXPECTED_RATIOS[key]

    item = items["s_step_a_v_1"]
    assert item["step_key"] == "step_a"
    assert item["version_no"] == 1
    assert item["ratio"] == 0.48
    assert item["step_explain"] == "示例步骤A：质量打分"
    assert item["version_note"] == "v1：初版打分"

    download = items["download_finish"]
    assert download["step_key"] is None
    assert download["version_no"] is None
    assert download["step_explain"] is None
    assert download["version_note"] is None


def test_progress_keys_filter(client):
    response = client.get(PROD_URL, params={"keys": "s_step_a_v_2,s_step_b_v_1"})
    assert response.status_code == 200
    body = response.json()
    # available is unaffected by the filter
    assert len(body["available"]) == 5
    items = _by_key(body["items"])
    assert set(items) == {"s_step_a_v_2", "s_step_b_v_1"}
    assert items["s_step_a_v_2"]["count"] == 11
    assert items["s_step_a_v_2"]["version_note"] == "v2：打分算法升级"
    assert items["s_step_b_v_1"]["count"] == 10
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
