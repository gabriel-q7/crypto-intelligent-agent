"""Fase 5: cliente HTTP da interface Streamlit (ui/backend.py) contra uma API simulada."""

import importlib.util
import json

import httpx
import pytest

from app.config import PROJECT_ROOT

spec = importlib.util.spec_from_file_location("ui_backend", PROJECT_ROOT / "ui" / "backend.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)

SID = "a" * 32
REPLY = {"answer": "O BTC está em US$ 81.545,05.", "verification": {
    "status": "partially_verified", "correction_attempted": True, "corrected_passed": False,
    "claims": [{"label": "entailment"}, {"label": "neutral"}, {"label": "contradiction"}],
    "logical_findings": [{"severity": "error", "kind": "calculation"}, {"severity": "warning", "kind": "period_mismatch"}]}}


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "POST" and path == "/conversations":
        return httpx.Response(201, json={"session_id": SID})
    if path == f"/conversations/{SID}/agent":
        assert json.loads(request.content)["message"] == "Preço do BTC?"
        return httpx.Response(200, json=REPLY)
    if request.method == "GET" and path == "/conversations":
        assert request.url.params["limit"] == "50"
        return httpx.Response(200, json={"sessions": [{"session_id": SID, "title": "Preço do BTC"}]})
    if path == f"/conversations/{SID}/messages":
        return httpx.Response(200, json={"messages": [{"role": "user", "content": "oi"}]})
    if path.startswith("/conversations/"):
        return httpx.Response(404, json={"detail": "sessão não encontrada"})
    return httpx.Response(503, json={"detail": "Ollama indisponível"})


def test_client_flow_and_errors():
    client = ui.Backend("http://api.test", transport=httpx.MockTransport(handler))
    assert client.create_conversation()["session_id"] == SID
    assert client.ask(SID, "Preço do BTC?")["answer"].startswith("O BTC")
    assert client.history(SID) == [{"role": "user", "content": "oi"}]
    assert client.list_conversations() == [{"session_id": SID, "title": "Preço do BTC"}]
    with pytest.raises(ui.BackendError) as missing:
        client.get_conversation("b" * 32)
    assert missing.value.status == 404
    with pytest.raises(ui.BackendError) as down:
        client.health()
    assert down.value.status == 503 and "Ollama" in str(down.value)


def test_unreachable_api_is_clear_error():
    def boom(request):
        raise httpx.ConnectError("recusado")

    with pytest.raises(ui.BackendError, match="API indisponível"):
        ui.Backend("http://api.test", transport=httpx.MockTransport(boom)).health()


def test_verification_summary_counts():
    s = ui.verification_summary(REPLY["verification"])
    assert (s["supported"], s["neutral"], s["contradicted"]) == (1, 1, 1)
    assert s["corrected"] and s["corrected_passed"] is False
    assert len(s["logical_errors"]) == 1 and len(s["logical_warnings"]) == 1
