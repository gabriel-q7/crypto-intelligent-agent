"""Integração real da Fase 3: Ollama (gemma4:e2b + nomic-embed-text), Mem0/Chroma, SQLite e CoinPaprika.

Execução: pytest -m "integration and network" tests/integration/test_phase3_live.py -v -s
"""

import os

import httpx
import pytest

from app.agents.factory import KnowledgeSearcher
from app.config import ConversationSettings, get_knowledge_settings, get_settings
from app.conversation.chatbot import ConversationService
from app.memory.long_term import Mem0Backend
from app.persistence.sqlite_store import ConversationStore
from app.services.llm import build_llm
from app.tools.coinpaprika import CoinPaprikaClient

pytestmark = [pytest.mark.integration, pytest.mark.network]


def _ready() -> bool:
    settings, knowledge = get_settings(), get_knowledge_settings()
    try:
        names = {m["name"].removesuffix(":latest") for m in httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3).json()["models"]}
        httpx.get("https://api.coinpaprika.com/v1/global", timeout=5)
    except httpx.HTTPError:
        return False
    return {settings.ollama_model.removesuffix(":latest"), knowledge.embedding_model} <= names


@pytest.fixture(scope="module")
def dirs(tmp_path_factory):
    if not _ready():
        pytest.skip("Ollama/modelos ou CoinPaprika indisponíveis")
    os.environ["MEM0_TELEMETRY"] = "False"
    return tmp_path_factory.mktemp("phase3")


def service(dirs) -> ConversationService:
    """Cria um serviço novo sobre os MESMOS arquivos: simula um reinício do processo."""
    settings = ConversationSettings(
        db_path=str(dirs / "conv.sqlite3"), memory_store_dir=str(dirs / "mem"), memory_history_db=str(dirs / "mem0.db"),
        window_messages=4, summary_trigger_messages=2,
    )
    memory = Mem0Backend(get_settings(), settings, get_knowledge_settings())
    return ConversationService(
        ConversationStore(settings.db_path), memory, build_llm(), CoinPaprikaClient(),
        KnowledgeSearcher(get_knowledge_settings()), settings, background=False,
    )


def show(label, reply):
    print(f"\n[{label}] {reply.effective_intent.value} {reply.status} assets={reply.entities.assets} "
          f"period={reply.entities.period_days} sources={reply.context_sources}\n    {reply.answer[:400]}")


def test_live_multi_turn_restart_and_isolation(dirs):
    svc = service(dirs)
    session = svc.store.create_session("live")
    other = svc.store.create_session("outra")

    a1 = svc.handle(session.id, "Compare BTC e ETH no mercado atual")
    show("A1", a1)
    assert a1.effective_intent.value == "asset_comparison" and a1.status in {"complete", "partial"}
    assert {s.endpoint for s in a1.data_sources} >= {"/tickers/btc-bitcoin", "/tickers/eth-ethereum"}

    a2 = svc.handle(session.id, "Considere os últimos 30 dias")
    show("A2", a2)
    assert a2.entities.assets == ["BTC", "ETH"] and a2.entities.period_days == 30
    assert "30 dias" in a2.answer and not a2.clarification_needed

    d = svc.handle(session.id, "Eu quis dizer 7 dias, não 30")
    show("D", d)
    assert d.entities.period_days == 7 and d.entities.assets == ["BTC", "ETH"]

    b = svc.handle(session.id, "E quanto a DeFi?")
    show("B", b)
    assert b.effective_intent.value == "defi_analysis" and b.status == "unavailable"

    pref = svc.handle(session.id, "Prefiro sempre análises com foco em risco")
    show("pref", pref)
    svc.handle(other.id, "Analise SOL")
    svc.close()

    # --- reinício: novo serviço, mesmos SQLite e Chroma ---
    svc2 = service(dirs)
    c = svc2.handle(session.id, "Agora compare os dois com o resultado anterior")
    show("C (após reinício)", c)
    assert c.effective_intent.value == "previous_result_reference" and c.referenced_analyses

    memories = svc2.memory.list(session.id)
    print("\nmemórias da sessão:", [(m.metadata.get("doc_type"), m.text[:90]) for m in memories])
    assert any(m.metadata.get("doc_type") == "analysis" for m in memories)
    assert all("SOL" not in m.text for m in memories)  # isolamento
    assert all(not svc2.memory.list(other.id) or "BTC" not in m.text for m in svc2.memory.list(other.id))
    hits = svc2.memory.search(session.id, "foco em risco", 3)
    print("busca 'foco em risco':", [(round(h.score, 3), h.text[:80]) for h in hits])
    svc2.close()
