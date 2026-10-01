from app import __version__


def test_app_starts_and_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["env"] == "test"


def test_unknown_route_404(client):
    assert client.get("/nope").status_code == 404
