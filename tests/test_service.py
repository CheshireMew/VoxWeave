from __future__ import annotations

from fastapi.testclient import TestClient

from voxweave.config import Settings
from voxweave.service import create_app


def test_health_describe_and_stable_error(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    with TestClient(create_app(settings, token="secret")) as client:
        assert client.get("/v1/health").json()["ok"] is True
        headers = {"Authorization": "Bearer secret"}
        handshake = client.get("/v1/handshake", headers=headers).json()
        assert handshake["protocol"] == "voxweave-control"
        assert handshake["version"] == 1
        description = client.get("/v1/describe", headers=headers).json()
        assert description["protocol"] == "voxweave-control"
        response = client.post(
            "/v1/execute",
            headers=headers,
            json={
                "protocol": "voxweave-control",
                "version": 1,
                "operation": "conversion.run",
                "arguments": {"input": "relative.wav"},
            },
        ).json()
        assert response["ok"] is False
        assert response["error_type"] == "invalid_arguments"


def test_token_is_required(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    with TestClient(create_app(settings, token="secret")) as client:
        assert client.get("/v1/describe").status_code == 401
        assert client.get("/v1/handshake").status_code == 401


def test_authenticated_task_websocket_streams_real_events(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    with TestClient(create_app(settings, token="secret")) as client:
        headers = {"Authorization": "Bearer secret"}
        submitted = client.post(
            "/v1/execute",
            headers=headers,
            json={
                "protocol": "voxweave-control",
                "version": 1,
                "operation": "model.import",
                "arguments": {"model": str(tmp_path / "missing.pth")},
            },
        ).json()
        task_id = submitted["result"]["id"]
        with client.websocket_connect(f"/v1/events?token=secret&task_id={task_id}") as socket:
            event = socket.receive_json()
        assert event["task_id"] == task_id
        assert event["state"] == "queued"
