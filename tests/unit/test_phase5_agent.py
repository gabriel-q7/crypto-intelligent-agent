"""Fase 5: agente integrado (create_agent), trajetória, histórico persistente, correção única e API."""

import json

from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake import FakeListLLM
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool, Tool

from app.agents.factory import KnowledgeSearcher
from app.api.container import AppContainer
from app.config import ConversationSettings, KnowledgeSettings, MarketDataSettings
from app.conversation.chatbot import ConversationService
from app.integrated.agent import build_integrated_agent, run_agent
from app.integrated.history import SessionHistoryAdapter
from app.integrated.service import IntegratedAgentService
from app.integrated.tools import TOOL_CATEGORY, AssetArgs, structured_tools
from app.knowledge.store import build_index
from app.main import create_app
from app.memory.long_term import InMemoryBackend
from app.persistence.sqlite_store import ConversationStore
from app.verification.schemas import ClaimVerification
from app.verification.service import VerificationService
from tests.unit.phase5_fakes import (
    OBS_BTC,
    OBS_ETH,
    OBS_RAG,
    ScriptedVerifier,
    result,
    scripted,
    tool_call,
)

SETTINGS = ConversationSettings(window_messages=4, summary_trigger_messages=2, context_budget_tokens=1500)


def fake_tools(calls: list | None = None) -> list[StructuredTool]:
    by_asset = {"BTC": OBS_BTC, "ETH": OBS_ETH}

    def market(asset: str) -> str:
        if calls is not None:
            calls.append(("get_market_data", asset))
        return json.dumps(by_asset.get(asset.upper(), {"ok": False, "tool": "get_market_data",
                                                       "error": {"kind": "not_found", "message": "x"}}))

    def knowledge(question: str) -> str:
        if calls is not None:
            calls.append(("search_knowledge_base", question))
        return json.dumps(OBS_RAG)

    return [
        StructuredTool.from_function(market, name="get_market_data", description="preço atual", args_schema=AssetArgs),
        StructuredTool.from_function(knowledge, name="search_knowledge_base", description="conceitos"),
    ]


# --------------------------------------------------------------------------- ferramentas e agente


def test_structured_tools_expose_asset_profile_when_available():
    base = [Tool(name="get_asset_profile", func=lambda x: json.dumps({"ok": True, "tool": "get_asset_profile", "data": x}),
                 description="perfil")]
    [tool] = structured_tools(base)
    assert tool.name == "get_asset_profile" and TOOL_CATEGORY[tool.name] == "api"
    assert json.loads(tool.invoke({"asset": "SOL"}))["data"] == "SOL"


def test_structured_tools_delegate_to_existing_single_input_tools():
    seen = []
    base = [Tool(name=n, func=lambda x, n=n: (seen.append((n, x)) or json.dumps({"ok": True, "tool": n})), description=n)
            for n in TOOL_CATEGORY]
    tools = {t.name: t for t in structured_tools(base)}
    assert set(tools) == set(TOOL_CATEGORY)
    tools["get_market_data"].invoke({"asset": "BTC"})
    tools["get_global_market"].invoke({})
    tools["calculate_percent_change"].invoke({"old_value": 100, "new_value": 110})
    assert seen == [("get_market_data", "BTC"), ("get_global_market", "global"), ("calculate_percent_change", "100.0, 110.0")]
    assert tools["query_market_database"].args == {"question": tools["query_market_database"].args["question"]}


def test_agent_combines_tools_and_trajectory_is_ordered():
    calls = []
    llm = scripted(
        tool_call("get_market_data", {"asset": "ETH"}, "c1"),
        tool_call("search_knowledge_base", {"question": "consenso do Ethereum"}, "c2"),
        "O ETH está em US$ 2.426,11 e usa Proof of Stake.",
    )
    run = run_agent(build_integrated_agent(llm, fake_tools(calls)), [], "Preço do ETH e consenso?")
    assert calls == [("get_market_data", "ETH"), ("search_knowledge_base", "consenso do Ethereum")]
    assert [(s.order, s.tool, s.category, s.ok) for s in run.steps] == [
        (1, "get_market_data", "api", True), (2, "search_knowledge_base", "rag", True)]
    assert run.steps[0].args == {"asset": "ETH"} and '"price_usd": 2426.11' in run.steps[0].output
    assert run.answer.startswith("O ETH") and len(run.observations) == 2


def test_tool_error_is_recorded_in_trajectory():
    llm = scripted(tool_call("get_market_data", {"asset": "XYZ"}, "c1"), "Não encontrei o ativo XYZ.")
    run = run_agent(build_integrated_agent(llm, fake_tools()), [], "Preço do XYZ?")
    assert run.steps[0].ok is False and run.steps[0].error_kind == "not_found"


def test_agent_answers_without_tools_when_not_needed():
    run = run_agent(build_integrated_agent(scripted("Qual ativo você quer analisar?"), fake_tools()), [], "Esse ativo está caro?")
    assert run.steps == [] and run.answer.endswith("?")


# --------------------------------------------------------------------------- histórico


def test_history_adapter_reads_window_summary_and_memories_without_writing(tmp_path):
    store, memory = ConversationStore(tmp_path / "c.sqlite3"), InMemoryBackend()
    sid = store.create_session().id
    for i in range(6):
        store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"mensagem {i}")
    store.save_summary(sid, "O usuário analisou BTC.", {}, covers_until_seq=2, expected_version=0)
    memory.add_episode(sid, "Análise anterior de SOL com foco em risco", {"source_ref": "analysis:1"})
    current = store.append_message(sid, "user", "E o SOL?")
    bundle = SessionHistoryAdapter(store, memory, SETTINGS).load(sid, "SOL risco", before_seq=current.seq)
    assert isinstance(bundle.messages[0], SystemMessage) and "O usuário analisou BTC" in bundle.messages[0].content
    assert "foco em risco" in bundle.messages[0].content and bundle.memory_refs == ["analysis:1"]
    assert [m.content for m in bundle.messages[1:]] == ["mensagem 2", "mensagem 3", "mensagem 4", "mensagem 5"]
    assert isinstance(bundle.messages[1], HumanMessage) and isinstance(bundle.messages[2], AIMessage)
    assert len(store.list_messages(sid)) == 7  # o adaptador só lê


def test_history_isolated_by_session(tmp_path):
    store, memory = ConversationStore(tmp_path / "c.sqlite3"), InMemoryBackend()
    a, b = store.create_session().id, store.create_session().id
    store.append_message(a, "user", "Meu ativo é BTC")
    memory.add_episode(a, "Episódio de A", {})
    bundle = SessionHistoryAdapter(store, memory, SETTINGS).load(b, "BTC")
    assert bundle.messages == [] and bundle.memory_refs == []


# --------------------------------------------------------------------------- serviço: correção e persistência


def make_service(tmp_path, llm, verifier, db="c.sqlite3", prefetch_tools=()):
    store, memory = ConversationStore(tmp_path / db), InMemoryBackend()
    conversation = ConversationService(store, memory, FakeListChatModel(responses=["{}"]), None, lambda q: [],
                                       SETTINGS, background=False)
    service = IntegratedAgentService(build_integrated_agent(llm, fake_tools()), verifier, store,
                                     SessionHistoryAdapter(store, memory, SETTINGS), conversation, "fake",
                                     tools=prefetch_tools)
    return service, store, memory


def profile_tool(calls: list) -> StructuredTool:
    def profile(asset: str) -> str:
        calls.append(("get_asset_profile", asset))
        return json.dumps({"ok": True, "tool": "get_asset_profile",
                           "data": {"name": asset, "symbol": asset, "proof_type": "Proof of Stake"},
                           "source": {"provider": "CoinPaprika", "endpoint": f"/coins/{asset}"}})

    return StructuredTool.from_function(profile, name="get_asset_profile", description="perfil", args_schema=AssetArgs)


def test_network_comparison_tools_are_called_by_code_and_model_only_writes(tmp_path):
    calls: list = []
    tools = [*fake_tools(calls), profile_tool(calls)]
    llm = scripted("O Ethereum usa Proof of Stake desde 2022; o BTC está em US$ 81.545,05 (CoinPaprika).")
    service, store, _ = make_service(tmp_path, llm, ScriptedVerifier(result("verified")), prefetch_tools=tools)
    reply = service.handle(store.create_session().id, "faça uma comparação sobre bitcoin vs ethereum")
    expected = ["search_knowledge_base"] * 3 + ["get_asset_profile"] * 2 + ["get_market_data"] * 2
    assert [s.tool for s in reply.trajectory] == expected and [s.order for s in reply.trajectory] == list(range(1, 8))
    assert calls == [("search_knowledge_base", "faça uma comparação sobre bitcoin vs ethereum"),
                     ("search_knowledge_base", "Bitcoin: consenso, contratos inteligentes, finalidade e escalabilidade"),
                     ("search_knowledge_base", "Ethereum: consenso, contratos inteligentes, finalidade e escalabilidade"),
                     ("get_asset_profile", "BTC"), ("get_asset_profile", "ETH"),
                     ("get_market_data", "BTC"), ("get_market_data", "ETH")]
    assert not reply.verification.correction_attempted and reply.answer.startswith("O Ethereum usa Proof of Stake")
    assert {"get_asset_profile", "search_knowledge_base"} <= {s.tool for s in reply.data_sources}


def test_prefetch_skips_questions_that_are_not_network_comparisons(tmp_path):
    calls: list = []
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "O BTC está em US$ 81.545,05 (CoinPaprika).")
    service, store, _ = make_service(tmp_path, llm, ScriptedVerifier(result("verified")),
                                     prefetch_tools=[*fake_tools(calls), profile_tool(calls)])
    reply = service.handle(store.create_session().id, "Compare o preço de BTC e ETH")
    assert [s.tool for s in reply.trajectory] == ["get_market_data"]  # só mercado: o modelo escolhe


def test_verified_answer_is_delivered_without_correction_and_persisted_once(tmp_path):
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "O BTC está em US$ 81.545,05.")
    verifier = ScriptedVerifier(result("verified"))
    service, store, memory = make_service(tmp_path, llm, verifier)
    sid = store.create_session().id
    reply = service.handle(sid, "Preço do BTC?")
    assert reply.status == "complete" and not reply.verification.correction_attempted and reply.original_answer is None
    assert len(verifier.calls) == 1 and verifier.calls[0][2] == 1
    messages = store.list_messages(sid)
    assert [(m.role, m.content) for m in messages] == [("user", "Preço do BTC?"), ("assistant", "O BTC está em US$ 81.545,05.")]
    assert messages[1].metadata["verification"]["status"] == "verified" and messages[1].metadata["engine"] == "agent"
    # Dado de mercado real → análise + episódio no Mem0 (mesma memória da Fase 3).
    assert reply.analysis_id and memory.list(sid)


def test_exactly_one_correction_and_corrected_answer_passes(tmp_path):
    llm = scripted(
        tool_call("get_market_data", {"asset": "BTC"}, "c1"),
        "O BTC está em US$ 95.000,00.",
        "O BTC está em US$ 81.545,05 (CoinPaprika).",
    )
    verifier = ScriptedVerifier(result("contradicted"), result("verified"))
    service, store, _ = make_service(tmp_path, llm, verifier)
    sid = store.create_session().id
    reply = service.handle(sid, "Preço do BTC?")
    assert len(verifier.calls) == 2  # 1ª resposta + 1 correção
    assert verifier.calls[1][1] == "O BTC está em US$ 81.545,05 (CoinPaprika)." and verifier.calls[1][2] == 1
    assert reply.answer == "O BTC está em US$ 81.545,05 (CoinPaprika)." and reply.original_answer == "O BTC está em US$ 95.000,00."
    v = reply.verification
    assert v.correction_attempted and v.corrected_passed and v.original_status == "contradicted"
    # A correção reaproveitou a observação: nenhuma chamada de ferramenta nova.
    assert [s.tool for s in reply.trajectory] == ["get_market_data"]
    assert len(store.list_messages(sid)) == 2


def test_failed_correction_yields_cautious_answer_and_no_second_retry(tmp_path):
    bad = "O BTC está em US$ 95.000,00. O Ethereum usa Proof of Stake."
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), bad, bad)
    from app.verification.claims import extract_claims

    claims = extract_claims(bad)
    second = result("contradicted", claims=[
        ClaimVerification(claim=claims[0], label="contradiction", method="numeric_check"),
        ClaimVerification(claim=claims[1], label="entailment", method="llm_nli"),
    ])
    verifier = ScriptedVerifier(result("contradicted"), second)
    service, store, _ = make_service(tmp_path, llm, verifier)
    reply = service.handle(store.create_session().id, "Preço do BTC?")
    assert len(verifier.calls) == 2
    assert reply.verification.correction_attempted and reply.verification.corrected_passed is False
    assert "95.000" not in reply.answer.split("Aviso")[0] and "Proof of Stake" in reply.answer
    assert "Aviso: a verificação automática não confirmou" in reply.answer and reply.status == "partial"


def test_empty_final_answer_uses_the_single_correction(tmp_path):
    # Caso real (2026-11-08): o modelo chamou as ferramentas e devolveu a resposta final vazia.
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "", "O BTC está em US$ 81.545,05 (CoinPaprika).")
    verifier = ScriptedVerifier(result("verified"), result("verified"))
    service, store, _ = make_service(tmp_path, llm, verifier)
    reply = service.handle(store.create_session().id, "Preço do BTC?")
    assert len(verifier.calls) == 2 and reply.answer == "O BTC está em US$ 81.545,05 (CoinPaprika)."
    assert reply.verification.correction_attempted and reply.verification.corrected_passed
    assert [s.tool for s in reply.trajectory] == ["get_market_data"]  # sem chamada nova


def test_refusal_without_lookup_of_cited_assets_is_corrected(tmp_path):
    # Caso real (2026-11-08): "Qual devo investir: SOL ou ETH?" → recusa sem chamar ferramenta.
    llm = scripted(
        "Não posso fornecer recomendações. Qual ativo você gostaria de analisar primeiro?",
        tool_call("get_market_data", {"asset": "BTC"}, "c1"),
        "O BTC está em US$ 81.545,05 (CoinPaprika). Não faço recomendação de investimento.",
    )
    verifier = ScriptedVerifier(result("verified"), result("verified"))
    service, store, _ = make_service(tmp_path, llm, verifier)
    reply = service.handle(store.create_session().id, "Qual devo investir: BTC ou ETH?")
    assert reply.verification.correction_attempted and [s.tool for s in reply.trajectory] == ["get_market_data"]
    assert reply.answer.startswith("O BTC está em US$ 81.545,05") and len(verifier.calls) == 2


def test_advice_question_always_carries_the_disclaimer(tmp_path):
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "O BTC está em US$ 81.545,05 (CoinPaprika).")
    service, store, _ = make_service(tmp_path, llm, ScriptedVerifier(result("verified")))
    reply = service.handle(store.create_session().id, "Vale a pena investir em BTC?")
    assert reply.answer.endswith("os dados acima servem apenas para comparação.")
    plain = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "O BTC está em US$ 81.545,05 (CoinPaprika).")
    service, store, _ = make_service(tmp_path, plain, ScriptedVerifier(result("verified")), db="d.sqlite3")
    assert "recomendação" not in service.handle(store.create_session().id, "Preço do BTC?").answer


def test_skipped_lookup_only_flags_refusals_with_cited_assets():
    from app.integrated.agent import AgentRun
    from app.integrated.service import skipped_lookup

    refusal = AgentRun(answer="Não tenho dados de mercado atuais para comparar SOL e ETH.")
    assert skipped_lookup("Qual devo investir: SOL ou ETH?", refusal) == ["SOL", "ETH"]
    assert skipped_lookup("Esse ativo está caro?", AgentRun(answer="Qual ativo você quer analisar?")) == []
    concept = AgentRun(answer="O Ethereum usa Proof of Stake desde 2022.")
    assert skipped_lookup("Explique o consenso do ETH.", concept) == []  # respondeu: nada a corrigir


def test_skipped_concepts_flags_network_comparisons_without_knowledge_base():
    from app.integrated.agent import AgentRun
    from app.integrated.schemas import ToolStep
    from app.integrated.service import skipped_concepts

    def step(tool: str) -> ToolStep:
        return ToolStep(order=1, tool=tool, category=TOOL_CATEGORY[tool], args={}, ok=True, output="{}")

    market_only = AgentRun(answer="SOL custa US$ 81,54 e ETH US$ 2.014,18.", steps=[step("get_market_data")])
    assert skipped_concepts("faça uma comparação sobre solana vs ethereum", market_only) == ["SOL", "ETH"]
    assert skipped_concepts("Qual a diferença entre Ethereum e Solana?", market_only) == ["ETH", "SOL"]
    with_rag = AgentRun(answer="...", steps=[step("get_market_data"), step("search_knowledge_base")])
    assert skipped_concepts("faça uma comparação sobre solana vs ethereum", with_rag) == []
    assert skipped_concepts("Qual a diferença do Ethereum para o PoW?", market_only) == []  # um ativo só
    assert skipped_concepts("Compare o preço de BTC e ETH", market_only) == []  # só mercado
    assert skipped_concepts("Compare a rede e o preço de BTC e ETH", market_only) == ["BTC", "ETH"]


def test_network_comparison_without_knowledge_base_is_corrected(tmp_path):
    # Caso real (2026-11-12): "solana vs ethereum" respondido só com preço, rank e volume.
    llm = scripted(
        tool_call("get_market_data", {"asset": "ETH"}, "c1"),
        "O ETH está em US$ 2.426,11 (CoinPaprika).",
        tool_call("search_knowledge_base", {"question": "comparação Ethereum vs Bitcoin"}, "c2"),
        "O Ethereum usa Proof of Stake desde 2022; o ETH está em US$ 2.426,11 (CoinPaprika).",
    )
    verifier = ScriptedVerifier(result("verified"), result("verified"))
    service, store, _ = make_service(tmp_path, llm, verifier)
    reply = service.handle(store.create_session().id, "faça uma comparação sobre bitcoin vs ethereum")
    assert reply.verification.correction_attempted and len(verifier.calls) == 2
    assert [s.tool for s in reply.trajectory] == ["get_market_data", "search_knowledge_base"]
    assert reply.answer.startswith("O Ethereum usa Proof of Stake")


def test_verification_failure_is_reported_not_corrected(tmp_path):
    llm = scripted("O Ethereum usa Proof of Stake.")
    verifier = ScriptedVerifier(result("verification_failed"))
    service, store, _ = make_service(tmp_path, llm, verifier)
    reply = service.handle(store.create_session().id, "Consenso do ETH?")
    assert len(verifier.calls) == 1 and "não foi verificada" in reply.answer and reply.status == "partial"


def test_follow_up_uses_persisted_history_and_survives_restart(tmp_path):
    seen_inputs = []

    class Spy(type(scripted("x"))):
        def _generate(self, messages, *args, **kwargs):
            seen_inputs.append([m.content for m in messages])
            return super()._generate(messages, *args, **kwargs)

    llm = Spy(messages=iter([AIMessage(content="Anotado: BTC."), AIMessage(content="Você falou do BTC.")]))
    service, store, _ = make_service(tmp_path, llm, ScriptedVerifier(result("verified"), result("verified")))
    sid = store.create_session().id
    service.handle(sid, "Meu ativo preferido é BTC.")
    # "Reinício": novo serviço e novo store sobre o mesmo arquivo SQLite.
    service2, store2, _ = make_service(tmp_path, llm, ScriptedVerifier(result("verified")))
    service2.handle(sid, "Qual é o meu ativo preferido?")
    last_call = seen_inputs[-1]
    assert "Meu ativo preferido é BTC." in last_call and "Anotado: BTC." in last_call
    assert len(store2.list_messages(sid)) == 4  # 2 turnos × 2 mensagens, sem duplicação


# --------------------------------------------------------------------------- API


def test_api_agent_endpoint_and_shared_history(tmp_path):
    knowledge = KnowledgeSettings(corpus_dir="knowledge/corpus", store_dir=str(tmp_path / "chroma"), embedding_model="fake")
    embeddings = DeterministicFakeEmbedding(size=32)
    build_index(knowledge, embeddings)
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "O BTC está em US$ 81.545,05.")
    nli = FakeListChatModel(responses=[json.dumps({"issues": []})])

    def factory():
        container = AppContainer(
            knowledge_settings=knowledge, llm=nli, agent_llm=FakeListLLM(responses=["Final Answer: ok"]),
            knowledge=KnowledgeSearcher(knowledge, embeddings), memory_backend=InMemoryBackend(),
            conversation_settings=ConversationSettings(db_path=str(tmp_path / "conv.sqlite3")),
            market_settings=MarketDataSettings(db_path=str(tmp_path / "m.sqlite3")),
            tool_llm=llm, background_maintenance=False,
        )
        container.integrated.agent = build_integrated_agent(llm, fake_tools())
        return container

    with TestClient(create_app(factory)) as client:
        sid = client.post("/conversations").json()["session_id"]
        reply = client.post(f"/conversations/{sid}/agent", json={"message": "Preço do BTC?"}).json()
        assert reply["verification"]["status"] == "verified" and reply["tools_used"] == ["get_market_data"]
        assert reply["trajectory"][0]["output"] == ""  # sem include_trace
        history = client.get(f"/conversations/{sid}/messages").json()
        assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
        assert client.post("/conversations/" + "0" * 32 + "/agent", json={"message": "oi oi"}).status_code == 404
