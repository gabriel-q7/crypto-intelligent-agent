"""Cliente HTTP da interface: única ponte entre o Streamlit e a API FastAPI.

Streamlit → FastAPI (/conversations/...) → agente integrado → verificação. Nada de agente,
memória, recuperação ou verificação roda aqui.
"""

import os

import httpx

DEFAULT_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
# Um turno do agente em CPU leva de 30 s a alguns minutos (agente + verificação + correção).
TIMEOUT_S = float(os.getenv("BACKEND_TIMEOUT", "600"))


class BackendError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Backend:
    def __init__(self, base_url: str = DEFAULT_URL, transport: httpx.BaseTransport | None = None) -> None:
        self.http = httpx.Client(base_url=base_url, timeout=TIMEOUT_S, transport=transport)

    def _call(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise BackendError("A API demorou demais para responder. Tente de novo.") from exc
        except httpx.TransportError as exc:
            raise BackendError(f"API indisponível em {self.http.base_url}.") from exc
        if response.status_code == 404:
            raise BackendError("Conversa não encontrada.", 404)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:200]
            raise BackendError(f"Erro da API ({response.status_code}): {detail}", response.status_code)
        return response.json()

    def health(self) -> dict:
        return self._call("GET", "/health")

    def create_conversation(self, title: str | None = None) -> dict:
        return self._call("POST", "/conversations", json={"title": title})

    def get_conversation(self, session_id: str) -> dict:
        return self._call("GET", f"/conversations/{session_id}")

    def history(self, session_id: str) -> list[dict]:
        return self._call("GET", f"/conversations/{session_id}/messages")["messages"]

    def ask(self, session_id: str, message: str) -> dict:
        return self._call("POST", f"/conversations/{session_id}/agent", json={"message": message})


def verification_summary(verification: dict) -> dict:
    """Contagens exibidas no bloco compacto de verificação."""
    labels = [c.get("label") for c in verification.get("claims", [])]
    return {
        "status": verification.get("status"),
        "corrected": bool(verification.get("correction_attempted")),
        "corrected_passed": verification.get("corrected_passed"),
        "supported": labels.count("entailment"),
        "neutral": labels.count("neutral"),
        "contradicted": labels.count("contradiction"),
        "logical_errors": [f for f in verification.get("logical_findings", []) if f.get("severity") == "error"],
        "logical_warnings": [f for f in verification.get("logical_findings", []) if f.get("severity") == "warning"],
    }
