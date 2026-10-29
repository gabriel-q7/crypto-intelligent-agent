"""Chains da Fase 2 em LCEL.

- Sequencial (análise de mercado): identificar ativos → buscar dados → validar → gerar análise.
- Paralela (comparação): busca concorrente de cada ativo + visão global (``RunnableParallel``),
  aceitando falhas parciais.
- Conhecimento: busca semântica (Chroma) → resposta baseada só nos trechos recuperados.
- DeFi: capacidade indisponível nesta fase (DeFiLlama) — relatada explicitamente.

Todas devolvem ``CryptoReport``. Achados, fontes e horários vêm das observações (grounding);
o LLM só redige o resumo, e valores sem correspondência nos dados são removidos.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import Runnable, RunnableLambda, RunnableParallel
from pydantic import BaseModel, Field, ValidationError

from app.knowledge.store import KnowledgeHit, KnowledgeIndexError
from app.prompts.ptcf import json_system_prompt
from app.schemas.analysis import strip_market_figures
from app.schemas.report import CryptoReport, DataSource, Finding, ReportStatus
from app.services.grounding import (
    findings_from,
    format_pct,
    format_usd,
    ranking,
    sources_from,
    verify_figures,
    verify_items,
)
from app.services.llm import with_transient_retry
from app.tools.coinpaprika import CoinPaprikaClient, CoinPaprikaError
from app.tools.market_tools import observe_global, observe_ticker

logger = logging.getLogger(__name__)

MAX_ASSETS = 5
# Dado da fonte mais antigo que isso em relação à consulta é sinalizado como possivelmente defasado.
STALE_AFTER = timedelta(hours=1)


class AssetExtraction(BaseModel):
    assets: list[str] = Field(default=[], max_length=MAX_ASSETS)


class ChainAnalysis(BaseModel):
    summary: str = Field(min_length=1)
    key_points: list[str] = []
    uncertainties: list[str] = []


class KnowledgeAnswer(BaseModel):
    answer: str = Field(min_length=1)
    used_sources: list[str] = []


EXTRACTION_HINT = (
    'Esquema: {"assets": ["nome ou símbolo"]}. Liste apenas os criptoativos citados na pergunta '
    "(no máximo 5), sem inventar; lista vazia se nenhum for citado."
)
ANALYSIS_HINT = (
    'Esquema: {"summary": "2 a 4 frases", "key_points": ["..."], "uncertainties": ["..."]}. '
    "Use SOMENTE os valores presentes em DADOS; cite a fonte e o horário de atualização. "
    "Se faltar dado, diga isso em uncertainties."
)
KNOWLEDGE_HINT = (
    'Esquema: {"answer": "2 a 4 frases", "used_sources": ["arquivo.md"]}. Responda apenas com base '
    "nos TRECHOS; se eles não cobrirem a pergunta, diga isso. Não cite preços."
)


def _ask_json(llm: BaseChatModel, system: str, human: str, model: type[BaseModel]) -> BaseModel:
    """Chamada JSON validada.

    Falha transitória de conexão: 1 nova tentativa com backoff. Erros de parsing/validação
    sobem para quem chamou tratar (sem retry).
    """
    message = with_transient_retry(llm).invoke([SystemMessage(content=system), HumanMessage(content=human)])
    return model.model_validate(JsonOutputParser().invoke(message))


# --------------------------------------------------------------------------- etapas


def identify_assets_step(llm: BaseChatModel, client: CoinPaprikaClient) -> Runnable:
    def identify(inputs: dict) -> dict:
        names = inputs.get("assets") or []
        uncertainties = list(inputs.get("uncertainties", []))
        if not names:
            try:
                names = _ask_json(llm, json_system_prompt(EXTRACTION_HINT), inputs["question"], AssetExtraction).assets
            except (ValidationError, ValueError) as exc:
                logger.warning("asset extraction invalid: %s", exc)
                uncertainties.append("não foi possível extrair os ativos da pergunta (resposta do modelo inválida)")
        resolved, observations = [], list(inputs.get("observations", []))
        for name in names[:MAX_ASSETS]:
            try:
                asset = client.resolve_asset(name)
            except CoinPaprikaError as exc:
                observations.append({"ok": False, "tool": "search_crypto_asset", "error": {"kind": exc.kind, "message": f"{name}: {exc}"}})
                continue
            if asset.id not in {a.id for a in resolved}:
                resolved.append(asset)
        return {**inputs, "resolved": resolved, "observations": observations, "uncertainties": uncertainties}

    return RunnableLambda(identify, name="identify_assets")


def fetch_sequential_step(client: CoinPaprikaClient) -> Runnable:
    def fetch(inputs: dict) -> dict:
        obs = [observe_ticker(client, a.id) for a in inputs["resolved"]]
        return {**inputs, "observations": inputs["observations"] + obs}

    return RunnableLambda(fetch, name="fetch_market_data")


def fetch_parallel_step(client: CoinPaprikaClient, max_concurrency: int = 4) -> Runnable:
    """Busca cada ativo em paralelo (batch) e, ao mesmo tempo, a visão global (RunnableParallel)."""
    one = RunnableLambda(lambda coin_id: observe_ticker(client, coin_id), name="fetch_one_asset")

    def fetch_assets(inputs: dict) -> list[dict]:
        ids = [a.id for a in inputs["resolved"]]
        return one.batch(ids, config={"max_concurrency": max_concurrency}) if ids else []

    parallel = RunnableParallel(
        assets=RunnableLambda(fetch_assets, name="fetch_assets_concurrently"),
        global_market=RunnableLambda(lambda _: observe_global(client), name="fetch_global_market"),
    )

    def combine(inputs: dict) -> dict:
        result = parallel.invoke(inputs)
        return {**inputs, "observations": inputs["observations"] + result["assets"] + [result["global_market"]]}

    return RunnableLambda(combine, name="fetch_parallel")


def validate_step() -> Runnable:
    """Confere dados essenciais e frescor; registra problemas como incertezas."""

    def validate(inputs: dict) -> dict:
        uncertainties = list(inputs["uncertainties"])
        for obs in inputs["observations"]:
            if not obs.get("ok"):
                uncertainties.append(f"{obs['tool']} falhou: {obs['error']['kind']} — {obs['error']['message']}")
                continue
            source = obs.get("source") or {}
            if obs["tool"] == "get_market_data" and obs["data"].get("price_usd") is None:
                uncertainties.append(f"{obs['data'].get('symbol')}: preço ausente na resposta da fonte")
            updated, retrieved = source.get("source_updated_at"), source.get("retrieved_at")
            if updated and retrieved:
                age = datetime.fromisoformat(retrieved) - datetime.fromisoformat(updated)
                if age > STALE_AFTER:
                    uncertainties.append(f"{obs['tool']}: dado da fonte atualizado há {age} (possivelmente defasado)")
        market_ok = [o for o in inputs["observations"] if o.get("ok") and o["tool"] == "get_market_data"]
        return {**inputs, "uncertainties": uncertainties, "market_ok": market_ok}

    return RunnableLambda(validate, name="validate_results")


# Variações que a CoinPaprika gratuita entrega por período. 30d vem como 0 de preenchimento
# (tratado como ausente no cliente); outros períodos exigem histórico (HTTP 402 no plano gratuito).
PERIOD_FIELDS = {1: "percent_change_24h", 7: "percent_change_7d", 30: "percent_change_30d"}
CONTEXT_HEADER = "CONTEXTO DA CONVERSA (histórico; NÃO são dados atuais):"


def period_coverage(observations: list[dict], period_days: int | None) -> tuple[str | None, str | None]:
    """(campo disponível para o período, motivo da indisponibilidade)."""
    if not period_days:
        return None, None
    field = PERIOD_FIELDS.get(period_days)
    tickers = [o["data"] for o in observations if o.get("ok") and o.get("tool") == "get_market_data"]
    if field and tickers and all(t.get(field) is not None for t in tickers):
        return field, None
    return None, (
        f"variação de {period_days} dias indisponível: o plano gratuito da CoinPaprika não fornece histórico "
        "(OHLCV histórico → HTTP 402) e devolve 0 fixo na variação de 30 dias; exibindo as variações de 24h e 7 dias"
    )


def generate_step(llm: BaseChatModel, route: str, min_assets: int = 1) -> Runnable:
    def generate(inputs: dict) -> CryptoReport:
        observations, uncertainties = inputs["observations"], list(inputs["uncertainties"])
        base = dict(
            question=inputs["question"],
            mode=f"route:{route}",
            findings=findings_from(observations),
            data_sources=sources_from(observations),
            decision_summary=inputs.get("decisions", []),
        )
        if not inputs["resolved"]:
            return CryptoReport(**base, status=ReportStatus.FAILED,
                                answer="Não identifiquei nenhum criptoativo na pergunta. Informe o nome ou símbolo (ex.: BTC).",
                                uncertainties=uncertainties or ["nenhum ativo identificado"])
        if len(inputs["market_ok"]) < min_assets:
            return CryptoReport(**base, status=ReportStatus.FAILED,
                                answer="Não foi possível obter os dados de mercado necessários; nenhuma análise foi gerada e nenhum valor foi estimado.",
                                uncertainties=uncertainties)

        data = [{k: o[k] for k in ("tool", "data", "source")} for o in observations if o.get("ok")]
        human = f"PERGUNTA: {inputs['question']}\n\nDADOS (CoinPaprika):\n{json.dumps(data, ensure_ascii=False)}"
        period_days = inputs.get("period_days")
        period_field, period_gap = period_coverage(observations, period_days)
        if period_days:
            human += f"\nPERÍODO SOLICITADO: {period_days} dias" + (f" (indisponível: {period_gap})" if period_gap else "")
        if inputs.get("conversation_context"):
            human += f"\n\n{CONTEXT_HEADER}\n{inputs['conversation_context']}"
        if period_gap:
            uncertainties.append(period_gap)
        # Ordenações calculadas no código: o modelo errava comparações (qual caiu mais, qual vale mais).
        ranking_sentences = []
        rank_fields = [("variação 24h", "percent_change_24h", format_pct), ("market cap", "market_cap_usd", format_usd)]
        if period_field and period_field != "percent_change_24h":
            rank_fields.insert(0, (f"variação {period_days}d", period_field, format_pct))
        elif period_gap:
            rank_fields.insert(1, ("variação 7d", "percent_change_7d", format_pct))
        for label, field, fmt in rank_fields:
            rows = ranking(observations, field)
            if len(rows) >= 2:
                text = " > ".join(f"{symbol} {fmt(value)}" for symbol, value in rows)
                human += f"\nRANKING CALCULADO ({label}, maior para menor): {text}"
                base["findings"] = [*base["findings"], Finding(statement=f"Ranking {label}: {text}", source_tool="get_market_data")]
                ranking_sentences.append(f"{label.capitalize()}, do maior para o menor: {text}.")
        try:
            analysis = _ask_json(llm, json_system_prompt(ANALYSIS_HINT), human, ChainAnalysis)
        except (ValidationError, ValueError) as exc:
            logger.warning("analysis generation invalid: %s", exc)
            return CryptoReport(**base, status=ReportStatus.PARTIAL,
                                answer="Os dados foram obtidos (ver achados), mas a análise do modelo veio em formato inválido e foi descartada.",
                                uncertainties=[*uncertainties, "análise do modelo inválida"])

        answer, removed = verify_figures(analysis.summary, observations)
        if route == "asset_comparison" and ranking_sentences:
            # Na comparação, a conclusão vem do código: mesmo recebendo o ranking, o modelo
            # escreveu ordens invertidas ("BTC teve a maior queda" quando foi o ETH).
            # O resumo livre do LLM é descartado; os pontos-chave (verificados) permanecem.
            stamp = max((o["source"].get("source_updated_at") or "" for o in data if o.get("source")), default="")
            answer = "Comparação com dados da CoinPaprika" + (f" (atualizados até {stamp})" if stamp else "") + ". " + " ".join(ranking_sentences)
        elif period_field and period_field != "percent_change_24h":
            changes = "; ".join(f"{t['symbol']} {format_pct(t[period_field])}" for t in (o["data"] for o in data if o["tool"] == "get_market_data"))
            answer = f"Variação em {period_days} dias (CoinPaprika): {changes}. " + answer
        if period_gap:
            answer = f"Não há dados de {period_days} dias nesta fonte ({period_gap.split(': ', 1)[1]}). " + answer
        key_points, removed_points = verify_items(analysis.key_points, observations)
        # Incertezas do LLM também passam pela verificação (ele às vezes coloca preços nelas).
        llm_uncertainties, removed_unc = verify_items(analysis.uncertainties, observations)
        if removed := removed + removed_points + removed_unc:
            uncertainties.append("valores sem correspondência nos dados foram removidos: " + ", ".join(removed))
        failures = [o for o in observations if not o.get("ok")]
        return CryptoReport(
            **base,
            status=ReportStatus.PARTIAL if failures or not answer or period_gap else ReportStatus.COMPLETE,
            answer=answer or "A análise gerada continha apenas valores não confirmados e foi descartada.",
            key_points=key_points,
            uncertainties=[*uncertainties, *llm_uncertainties],
        )

    return RunnableLambda(generate, name=f"generate_{route}")


# --------------------------------------------------------------------------- chains


def build_market_analysis_chain(llm: BaseChatModel, client: CoinPaprikaClient) -> Runnable:
    """Sequencial: identificar → buscar (um a um) → validar → analisar."""
    return (
        identify_assets_step(llm, client)
        | fetch_sequential_step(client)
        | validate_step()
        | generate_step(llm, "market_analysis")
    )


def build_comparison_chain(llm: BaseChatModel, client: CoinPaprikaClient, max_concurrency: int = 4) -> Runnable:
    """Paralela: identificar → buscar ativos e mercado global em paralelo → validar → comparar.

    Falha parcial: se pelo menos um ativo vier, a comparação sai como ``partial`` e diz o que faltou.
    """
    return (
        identify_assets_step(llm, client)
        | fetch_parallel_step(client, max_concurrency)
        | validate_step()
        | generate_step(llm, "asset_comparison", min_assets=1)
    )


def build_knowledge_chain(llm: BaseChatModel, search: Callable[[str], list[KnowledgeHit]]) -> Runnable:
    def answer(inputs: dict) -> CryptoReport:
        base = dict(question=inputs["question"], mode="route:knowledge_retrieval", decision_summary=inputs.get("decisions", []))
        try:
            hits = search(inputs["question"])
        except KnowledgeIndexError as exc:
            return CryptoReport(**base, status=ReportStatus.FAILED, answer=f"Base de conhecimento indisponível: {exc}", uncertainties=[str(exc)])
        if not hits:
            return CryptoReport(**base, status=ReportStatus.FAILED, answer="Nenhum trecho relevante encontrado na base de conhecimento.")
        excerpts = "\n\n".join(f"[{h.source}] {h.content}" for h in hits)
        sources = [DataSource(provider="base de conhecimento local (Chroma)", tool="search_knowledge_base", endpoint=s)
                   for s in dict.fromkeys(h.source for h in hits)]
        try:
            human = f"PERGUNTA: {inputs['question']}\n\nTRECHOS:\n{excerpts}"
            if inputs.get("conversation_context"):
                human += f"\n\n{CONTEXT_HEADER}\n{inputs['conversation_context']}"
            result = _ask_json(llm, json_system_prompt(KNOWLEDGE_HINT), human, KnowledgeAnswer)
        except (ValidationError, ValueError) as exc:
            logger.warning("knowledge answer invalid: %s", exc)
            return CryptoReport(**base, status=ReportStatus.PARTIAL, data_sources=sources,
                                answer="Trechos relevantes foram encontrados (ver fontes), mas a resposta do modelo veio em formato inválido.",
                                uncertainties=["resposta do modelo inválida"])
        # Conhecimento não traz dado atual: qualquer valor de mercado é removido (trava da Fase 1).
        text, removed_figures = strip_market_figures(result.answer)
        uncertainties = ["base local: conceitos estáveis, sem dados de mercado atuais"]
        if removed_figures:
            uncertainties.append("valores de mercado removidos da resposta: " + ", ".join(removed_figures))
        return CryptoReport(**base, status=ReportStatus.COMPLETE, answer=text, data_sources=sources, uncertainties=uncertainties)

    return RunnableLambda(answer, name="knowledge_retrieval")


DEFI_UNAVAILABLE = (
    "Análise DeFi (TVL, protocolos, rendimentos) depende do DeFiLlama, previsto para uma fase futura "
    "e ainda não integrado. Não há dados DeFi nesta versão e nenhum valor foi estimado. "
    "Posso explicar conceitos DeFi pela base de conhecimento ou consultar o preço do token na CoinPaprika."
)


def build_defi_chain() -> Runnable:
    return RunnableLambda(
        lambda inputs: CryptoReport(
            question=inputs["question"], mode="route:defi_analysis", status=ReportStatus.UNAVAILABLE,
            answer=DEFI_UNAVAILABLE, uncertainties=["capacidade DeFi indisponível nesta fase (DeFiLlama não integrado)"],
            decision_summary=inputs.get("decisions", []),
        ),
        name="defi_unavailable",
    )
