"""Resolução de contexto: combina a mensagem atual com o contexto ativo da sessão.

Regras (determinísticas):
1. **Continuação** (follow_up, ou pedido sem ativos com contexto ativo): herda ativos, período
   e métricas que a mensagem omitiu.
2. **Correção explícita**: o valor corrigido substitui o anterior; o resto é mantido.
3. **Assunto novo** (ativos explícitos diferentes do contexto, sem marca de continuação):
   não herda nada do tópico anterior.
4. **Referência a resultado anterior**: procura as análises DA SESSÃO; um candidato → usa;
   vários com conjuntos de ativos diferentes → pede esclarecimento.
5. Nada é inventado: sem ativo resolvível, pede o ativo.
6. Cada campo registra de onde veio (mensagem atual ou contexto ativo).
"""

from app.intent.schemas import ConversationIntent, ExtractedRequest, ResolvedRequest
from app.persistence.sqlite_store import StoredAnalysis

MARKET_INTENTS = {
    ConversationIntent.MARKET_ANALYSIS,
    ConversationIntent.ASSET_COMPARISON,
    ConversationIntent.HISTORICAL_ANALYSIS,
}


def _market_intent_for(assets: list[str], period_days: int | None, fallback: ConversationIntent) -> ConversationIntent:
    if len(assets) >= 2:
        return ConversationIntent.ASSET_COMPARISON
    if period_days and period_days > 7:
        return ConversationIntent.HISTORICAL_ANALYSIS
    return fallback if fallback in MARKET_INTENTS else ConversationIntent.MARKET_ANALYSIS


def _describe(analysis: StoredAnalysis) -> str:
    assets = ", ".join(analysis.request.get("assets", [])) or "sem ativos"
    stamp = (analysis.data_retrieved_at or analysis.created_at).strftime("%Y-%m-%d %H:%M UTC")
    return f"{analysis.id} ({analysis.intent}: {assets}, dados de {stamp})"


def resolve(
    extracted: ExtractedRequest,
    active_context: dict | None,
    analyses: list[StoredAnalysis],
) -> ResolvedRequest:
    active = active_context or {}
    active_assets: list[str] = active.get("assets", [])
    sources: dict[str, str] = {}

    def pick(field: str, current, inherited):
        if current:
            sources[field] = "current_message"
            return current
        if inherited:
            sources[field] = "active_context"
            return inherited
        return current

    # 4. Referência a resultado anterior -------------------------------------------------
    if extracted.intent == ConversationIntent.PREVIOUS_RESULT_REFERENCE:
        # O contexto ativo só desambigua com referência explícita ("compare-os", "them");
        # sem ela, várias análises com ativos diferentes geram pergunta de esclarecimento.
        wanted = extracted.assets or (active_assets if extracted.refers_to_active else [])
        candidates = [a for a in analyses if a.intent in {i.value for i in MARKET_INTENTS}]
        if wanted:
            matching = [a for a in candidates if set(a.request.get("assets", [])) == set(wanted)]
            candidates = matching or [a for a in candidates if set(wanted) & set(a.request.get("assets", []))]
        if not candidates:
            return ResolvedRequest(
                intent=extracted.intent,
                effective_intent=extracted.intent,
                assets=wanted,
                clarification_question="Não encontrei nenhuma análise anterior nesta conversa para comparar. "
                "Qual análise você quer usar como referência?",
            )
        asset_sets = {frozenset(a.request.get("assets", [])) for a in candidates}
        if len(asset_sets) > 1:
            options = "; ".join(_describe(a) for a in candidates[-3:])
            return ResolvedRequest(
                intent=extracted.intent,
                effective_intent=extracted.intent,
                clarification_question=f"Há mais de uma análise anterior possível: {options}. Qual delas?",
            )
        reference = candidates[-1]  # mesma combinação de ativos: a mais recente
        ref_assets = reference.request.get("assets", [])
        sources.update(assets="active_context" if not extracted.assets else "current_message")
        return ResolvedRequest(
            intent=extracted.intent,
            effective_intent=ConversationIntent.PREVIOUS_RESULT_REFERENCE,
            assets=ref_assets,
            period=reference.request.get("period"),
            period_days=reference.request.get("period_days"),
            metrics=reference.request.get("metrics", []),
            referenced_analysis_id=reference.id,
            requires_external_data=True,
            context_sources=sources,
        )

    # 3. Assunto novo: ativos explícitos diferentes, sem continuação nem correção ----------
    explicit_new_assets = bool(extracted.assets) and set(extracted.assets) != set(active_assets)
    continuation = extracted.intent == ConversationIntent.FOLLOW_UP or extracted.is_correction or (
        not extracted.assets and bool(active_assets) and extracted.intent != ConversationIntent.GENERAL_RESEARCH
    )
    if explicit_new_assets and not extracted.is_correction and extracted.intent != ConversationIntent.FOLLOW_UP:
        continuation = False

    if extracted.intent == ConversationIntent.GENERAL_RESEARCH and not continuation:
        return ResolvedRequest(
            intent=extracted.intent,
            effective_intent=ConversationIntent.GENERAL_RESEARCH,
            assets=extracted.assets,
            metrics=extracted.metrics,
            context_sources={"assets": "current_message"} if extracted.assets else {},
            missing_information=extracted.missing_information,
            assumptions=extracted.assumptions,
        )

    if continuation and active:
        # 1 e 2. Continuação / correção: a mensagem atual prevalece campo a campo.
        assets = pick("assets", extracted.assets, active_assets)
        period = pick("period", extracted.period, active.get("period"))
        period_days = extracted.period_days if extracted.period else active.get("period_days")
        metrics = pick("metrics", extracted.metrics, active.get("metrics", []))
        protocols = pick("protocols", extracted.protocols, active.get("protocols", []))
        base_intent = ConversationIntent(active.get("effective_intent", ConversationIntent.MARKET_ANALYSIS))
        if extracted.intent == ConversationIntent.DEFI_ANALYSIS:
            effective = ConversationIntent.DEFI_ANALYSIS  # nova dimensão (cenário B)
        elif extracted.intent in MARKET_INTENTS:
            effective = _market_intent_for(assets, period_days, extracted.intent)
        else:
            effective = _market_intent_for(assets, period_days, base_intent) if base_intent in MARKET_INTENTS else base_intent
        return ResolvedRequest(
            intent=extracted.intent,
            effective_intent=effective,
            assets=assets,
            protocols=protocols or [],
            networks=extracted.networks,
            metrics=metrics or [],
            period=period,
            period_days=period_days,
            requires_external_data=effective != ConversationIntent.GENERAL_RESEARCH,
            missing_information=extracted.missing_information,
            assumptions=extracted.assumptions,
            context_sources=sources,
            is_correction=extracted.is_correction,
        )

    # Pedido novo e independente.
    assets = extracted.assets
    if assets:
        sources["assets"] = "current_message"
    if extracted.period:
        sources["period"] = "current_message"
    if extracted.intent == ConversationIntent.FOLLOW_UP:
        # Continuação sem contexto anterior: não há o que herdar.
        return ResolvedRequest(
            intent=extracted.intent,
            effective_intent=extracted.intent,
            clarification_question="Não há uma análise anterior nesta conversa para continuar. Qual ativo você quer analisar?",
        )
    effective = extracted.intent
    if effective in MARKET_INTENTS:
        if not assets:
            return ResolvedRequest(
                intent=extracted.intent,
                effective_intent=effective,
                clarification_question="Qual criptoativo você quer analisar? Informe o nome ou o símbolo (ex.: BTC).",
            )
        effective = _market_intent_for(assets, extracted.period_days, effective)
    return ResolvedRequest(
        intent=extracted.intent,
        effective_intent=effective,
        assets=assets,
        protocols=extracted.protocols,
        networks=extracted.networks,
        metrics=extracted.metrics,
        period=extracted.period,
        period_days=extracted.period_days,
        requires_external_data=effective != ConversationIntent.GENERAL_RESEARCH,
        missing_information=extracted.missing_information,
        assumptions=extracted.assumptions,
        context_sources=sources,
        new_topic=bool(active_assets) and explicit_new_assets,
    )


def active_context_from(resolved: ResolvedRequest) -> dict:
    """O que fica como contexto ativo após um pedido executado (só pedidos de mercado/DeFi)."""
    return {
        "effective_intent": resolved.effective_intent.value,
        "assets": resolved.assets,
        "period": resolved.period,
        "period_days": resolved.period_days,
        "metrics": resolved.metrics,
        "protocols": resolved.protocols,
    }
