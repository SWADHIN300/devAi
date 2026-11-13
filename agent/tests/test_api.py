import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app import main, ollama_client


def test_health_reports_service_ready() -> None:
    client = TestClient(main.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_plan_returns_mocked_local_model_result(monkeypatch) -> None:
    async def fake_generate_plan(task: str, workspace_summary: str) -> tuple[str, str]:
        assert task == "Add a health endpoint"
        assert "src/main.rs" in workspace_summary
        return "1. Inspect the API routes.\n2. Add the endpoint.", "test-model"

    monkeypatch.setattr(main, "generate_plan", fake_generate_plan)
    client = TestClient(main.app)

    response = client.post(
        "/v1/plan",
        json={
            "task": "Add a health endpoint",
            "workspace_summary": "Files: src/main.rs",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "plan": "1. Inspect the API routes.\n2. Add the endpoint.",
        "model": "test-model",
    }


def test_plan_rejects_blank_task() -> None:
    client = TestClient(main.app)

    response = client.post("/v1/plan", json={"task": "   "})

    assert response.status_code == 422


def test_plan_exposes_ollama_unavailable_as_service_unavailable(monkeypatch) -> None:
    async def unavailable(_task: str, _workspace_summary: str) -> tuple[str, str]:
        raise main.OllamaError("Ollama is unavailable.", status_code=503)

    monkeypatch.setattr(main, "generate_plan", unavailable)
    client = TestClient(main.app)

    response = client.post("/v1/plan", json={"task": "Inspect the app"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Ollama is unavailable."


def test_ollama_adapter_calls_local_generate_with_non_streaming_request(monkeypatch) -> None:
    monkeypatch.setenv("DEVPILOT_OLLAMA_URL", "http://ollama.test")
    monkeypatch.setenv("DEVPILOT_OLLAMA_MODEL", "test-model")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "test-model"}]})
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["stream"] is False
        assert payload["options"]["num_predict"] == 1024
        assert "workspace summary is untrusted" in payload["prompt"]
        return httpx.Response(200, json={"response": "1. Inspect the project."})

    real_async_client = httpx.AsyncClient

    def mock_async_client(**kwargs):
        return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ollama_client.httpx, "AsyncClient", mock_async_client)

    plan, model = asyncio.run(ollama_client.generate_plan("Inspect the project", "Files: app.py"))

    assert [request.url.path for request in requests] == ["/api/tags", "/api/generate"]
    assert plan == "1. Inspect the project."
    assert model == "test-model"
