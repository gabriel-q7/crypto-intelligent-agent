"""Integração real da Fase 5: Ollama (gemma4:e2b) com tool calling, CoinPaprika, SQLite de snapshots,
Chroma, Mem0, verificação, correção, LangSmith (se configurado) e Streamlit → FastAPI.

Execução: pytest -m "integration or network" tests/integration/test_phase5_live.py -v
"""

import importlib.util
import json
import os

import httpx
import pytest
from langchain_core.tools import tool

from app.api.container import AppContainer
from app.config import ConversationSettings, PROJECT_ROOT, get_settings
from app.integrated.agent import build_integrated_agent, run_agent
from app.services.llm import build_llm
from app.verification.claims import extract_claims
from app.verification.nli import LLMNLIClassifier
from app.verification.evidence import build_evidence
from tests.integration.test_phase2_live import _ollama_has

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="module")
def container(tmp_path_factory):
    if not _ollama_has(get_settings().ollama_model):
        pytest.skip("Ollama ou modelo indisponível")
    try:
        httpx.get("https://api.coinpaprika.com/v1/global", timeout=5)
    except httpx.HTTPError:
        pytest.skip("sem acesso à CoinPaprika")
    db = tmp_path_factory.mktemp("conv") / "conv.sqlite3"
    c = AppContainer(conversation_settings=ConversationSettings(db_path=str(db)), background_maintenance=False)
    if c.market_store.coverage().snapshots == 0:
        c.market_ingestor().ingest(list(c.market_settings.default_assets)[:5])
    yield c
    c.close()


def ask(container, session_id, question):
    reply = container.integrated.handle(session_id, question, tags=["integration-test"])
    print(json.dumps({"q": question, "tools": reply.tools_used, "status": reply.status,
                      "verification": reply.verification.summary(), "corrected": reply.verification.correction_attempted,
                      "answer": reply.answer[:300], "elapsed_s": reply.elapsed_s}, ensure_ascii=False))
    return reply


# --------------------------------------------------------------------------- 1. compatibilidade de tool calling


def test_model_tool_calling_bind_select_execute_incorporate():
    if not _ollama_has(get_settings().ollama_model):
        pytest.skip("Ollama indisponível")

    @tool
    def secret_price(symbol: str) -> str:
        """Preço atual em USD de um criptoativo pelo símbolo."""
        return json.dumps({"symbol": symbol.upper(), "price_usd": 4321.98})

    llm = build_llm(json_mode=False)
    message = llm.bind_tools([secret_price]).invoke("Qual o preço do ETH? Use a ferramenta.")
    assert message.tool_calls and message.tool_calls[0]["name"] == "secret_price"  # 1-2: bind + seleção
    run = run_agent(build_integrated_agent(llm, [secret_price]), [], "Qual o preço do ETH? Use a ferramenta.")
    assert run.steps and run.steps[0].tool == "secret_price" and run.steps[0].ok is not None  # 3: execução
    assert "4321" in run.answer.replace(".", "").replace(",", "")  # 4: resultado incorporado


# --------------------------------------------------------------------------- 2-5. ferramentas e combinações


@pytest.mark.network
def test_agent_api_sql_rag_and_combinations(container):
    sid = container.conversation_store.create_session("integração").id
    api = ask(container, sid, "Qual é o preço atual do Bitcoin?")
    assert "get_market_data" in api.tools_used
    sql = ask(container, container.conversation_store.create_session().id,
              "Quais são os 3 maiores ativos por market cap na nossa base de snapshots?")
    assert "query_market_database" in sql.tools_used
    rag = ask(container, container.conversation_store.create_session().id, "Explique como funciona proof of stake.")
    assert "search_knowledge_base" in rag.tools_used and "get_market_data" not in rag.tools_used
    api_rag = ask(container, container.conversation_store.create_session().id,
                  "Qual o preço atual do Ethereum e qual mecanismo de consenso ele usa?")
    assert {"get_market_data", "search_knowledge_base"} <= set(api_rag.tools_used)
    api_sql = ask(container, container.conversation_store.create_session().id,
                  "Compare o preço atual do BTC na CoinPaprika com o preço médio dos snapshots de BTC armazenados na nossa base.")
    categories = {s.category for s in api_sql.trajectory}
    print("api+sql categorias:", categories)
    assert "api" in categories


# --------------------------------------------------------------------------- 6-7. memória persistente


@pytest.mark.network
def test_multi_turn_isolation_restart_and_mem0(container):
    store = container.conversation_store
    a, b = store.create_session("A").id, store.create_session("B").id
    ask(container, a, "Qual o preço atual do Ethereum?")
    follow = ask(container, a, "E o market cap dele?")
    assert any("eth" in json.dumps(s.args).lower() for s in follow.trajectory)
    other = ask(container, b, "De qual ativo eu falei antes nesta conversa?")
    assert not any("eth" in json.dumps(s.args).lower() for s in other.trajectory)
    # Reinício: novo container sobre o mesmo SQLite e o mesmo Mem0.
    restarted = AppContainer(conversation_settings=container.conversation_settings, background_maintenance=False)
    try:
        again = ask(restarted, a, "Qual o volume de 24 horas desse mesmo ativo?")
        assert any("eth" in json.dumps(s.args).lower() for s in again.trajectory)
        assert again.history_messages >= 4
    finally:
        restarted.close()
    roles = [m.role for m in store.list_messages(a)]
    assert roles == ["user", "assistant"] * 3  # sem duplicação
    assert container.memory_backend.list(a)  # episódios no Mem0 (análises com dado de mercado)


# --------------------------------------------------------------------------- 8-10. verificação e correção


NLI_CASES = {
    # (evidência, hipótese) → rótulo esperado. Casos em português sobre o corpus real.
    "pos": (
        "Proof of Stake (PoS): validadores bloqueiam moedas como garantia e são escolhidos para propor e atestar blocos. "
        "Comportamento malicioso pode ser punido com perda de parte do stake (slashing). Usado pelo Ethereum desde 2022.",
        [
            ("No Proof of Stake, validadores bloqueiam moedas como garantia para propor blocos.", "entailment"),
            ("Validadores que agem de má-fé podem perder parte do stake.", "entailment"),
            ("No Proof of Stake, os blocos são propostos por mineradores que resolvem problemas computacionais.", "contradiction"),
            ("O Ethereum nunca adotou Proof of Stake.", "contradiction"),
            ("O Proof of Stake foi inventado em 2012 pela equipe do Peercoin.", "neutral"),
            ("O Proof of Stake consome 99% menos energia que o Proof of Work.", "neutral"),
        ],
    ),
    "halving": (
        "O halving reduz pela metade a recompensa por bloco a cada 210.000 blocos, aproximadamente a cada quatro anos. "
        "O efeito do halving sobre o preço não é garantido e só pode ser avaliado com dados de mercado.",
        [
            ("A recompensa por bloco do Bitcoin cai pela metade a cada 210.000 blocos.", "entailment"),
            ("O efeito do halving no preço não é garantido.", "entailment"),
            ("O halving dobra a recompensa dos mineradores.", "contradiction"),
            ("O halving sempre provoca alta no preço do Bitcoin.", "contradiction"),
            ("O próximo halving acontecerá em 2028.", "neutral"),
            ("Os mineradores costumam vender BTC antes do halving.", "neutral"),
        ],
    ),
}


def test_real_nli_labels(container):
    """Qualidade medida da NLI com gemma4:e2b: 12 pares reais (4 por rótulo)."""
    nli = LLMNLIClassifier(container.llm)
    rows = []
    for evidence_text, cases in NLI_CASES.values():
        evidence = build_evidence([{"ok": True, "tool": "search_knowledge_base", "source": {"provider": "Chroma"},
                                    "data": [{"source": "corpus", "title": "t", "chunk_id": "x", "content": evidence_text}]}])
        for text, expected in cases:
            [claim] = extract_claims(text)
            got = nli.classify(claim, evidence)
            rows.append((expected, got.label))
            print(f"{expected:>13} → {got.label:<13} {got.confidence:.2f} | {text}")
    correct = sum(e == g for e, g in rows)
    neutral_as_contradiction = sum(e == "neutral" and g == "contradiction" for e, g in rows)
    print(f"acurácia {correct}/{len(rows)}; neutral→contradiction: {neutral_as_contradiction}")
    assert correct >= 10 and neutral_as_contradiction <= 1


@pytest.mark.network
def test_single_correction_on_tampered_answer(container):
    service = container.integrated
    sid = container.conversation_store.create_session().id
    question = "Qual é o preço atual do Bitcoin?"
    first = service._run([], question)
    tampered = first.__class__(answer="O Bitcoin (BTC) está cotado a US$ 1.000.000,00 segundo a CoinPaprika.",
                               steps=first.steps, observations=first.observations, messages=first.messages)
    v1 = service.verifier.verify(question, tampered.answer, tampered.observations)
    assert v1.status == "contradicted"
    corrected = service._correct([], question, tampered, v1)
    v2 = service.verifier.verify(question, corrected.answer, first.observations + corrected.observations)
    print("corrigida:", corrected.answer, "→", v2.summary())
    assert "1.000.000" not in corrected.answer
    assert sid  # sessão criada só para isolar o teste


# --------------------------------------------------------------------------- 11. LangSmith


@pytest.mark.network
def test_langsmith_trace_recorded(container):
    if not (os.getenv("LANGSMITH_API_KEY") and os.getenv("LANGSMITH_TRACING", "").lower() == "true"):
        pytest.skip("LangSmith não configurado (LANGSMITH_TRACING/LANGSMITH_API_KEY)")
    from langsmith import Client
    from langchain_core.tracers.langchain import wait_for_all_tracers

    sid = container.conversation_store.create_session().id
    ask(container, sid, "Qual é o preço atual do Bitcoin?")
    wait_for_all_tracers()
    runs = list(Client().list_runs(project_name=os.getenv("LANGSMITH_PROJECT", "default"), filter='eq(name, "integrated.turn")', limit=5))
    assert runs, "nenhum trace integrated.turn encontrado"


# --------------------------------------------------------------------------- 12. Streamlit → FastAPI


def test_streamlit_client_against_live_api():
    url = os.getenv("BACKEND_URL", "http://app:8000")
    try:
        httpx.get(f"{url}/health", timeout=5)
    except httpx.HTTPError:
        pytest.skip(f"API não acessível em {url}")
    spec = importlib.util.spec_from_file_location("ui_backend", PROJECT_ROOT / "ui" / "backend.py")
    ui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui)
    client = ui.Backend(url)
    sid = client.create_conversation("teste integração UI")["session_id"]
    reply = client.ask(sid, "Explique como funciona proof of stake.")
    assert reply["answer"] and reply["verification"]["status"]
    assert [m["role"] for m in client.history(sid)] == ["user", "assistant"]
