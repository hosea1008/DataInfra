"""Tests for the /api/health endpoint against the live mock databases."""


def test_health_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["envs"] == {"production": True, "test": True}
    assert "time" in body
