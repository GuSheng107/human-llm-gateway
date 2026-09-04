from app.core.readiness import ReadinessState


def test_readyz_reports_all_startup_checks(client) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["service"] == "human-llm-gateway"
    assert body["checks"] == {
        "startup": True,
        "database": True,
        "encryption": True,
        "protocols": True,
        "coordinators": True,
    }
    assert "sandbox" not in body["checks"]
    assert response.headers["x-trace-id"]


def test_readiness_state_is_not_ready_before_startup() -> None:
    snapshot = ReadinessState().snapshot("human-llm-gateway")

    assert snapshot["status"] == "not_ready"
    assert not any(snapshot["checks"].values())
