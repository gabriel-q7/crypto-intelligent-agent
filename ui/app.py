"""Interface Streamlit do Crypto Intelligence Agent (Fase 5).

Execução: ``streamlit run ui/app.py`` (local) ou serviço ``ui`` do Docker Compose.
Configuração: ``BACKEND_URL`` (padrão http://localhost:8000).
"""

import streamlit as st
from backend import Backend, BackendError, verification_summary

STATUS_LABEL = {
    "verified": ("Verificada", "green"),
    "partially_verified": ("Parcialmente verificada", "orange"),
    "unsupported": ("Sem sustentação", "orange"),
    "contradicted": ("Com contradições", "red"),
    "verification_failed": ("Não verificada", "gray"),
}
EXAMPLES = [
    "Qual é o preço atual do Bitcoin?",
    "Qual o preço atual do Ethereum e qual mecanismo de consenso ele usa?",
    "Quais são os 3 maiores ativos por market cap na nossa base de snapshots?",
    "Por que a recompensa dos mineradores cai pela metade?",
]

st.set_page_config(page_title="Crypto Intelligence Agent", page_icon="📈", layout="centered")


@st.cache_resource
def backend() -> Backend:
    return Backend()


def render_verification(reply: dict) -> None:
    v = reply.get("verification") or {}
    s = verification_summary(v)
    label, color = STATUS_LABEL.get(s["status"], (s["status"], "gray"))
    corrected = " · corrigida 1×" + (" (aprovada)" if s["corrected_passed"] else " (pendências)") if s["corrected"] else ""
    st.caption(
        f":{color}[● {label}]{corrected} · ✓ {s['supported']} sustentadas · ○ {s['neutral']} neutras · "
        f"✗ {s['contradicted']} contraditas · lógica: {len(s['logical_errors'])} erro(s)"
    )
    with st.expander("Detalhes da verificação e fontes"):
        for claim in v.get("claims", []):
            icon = {"entailment": "✓", "neutral": "○", "contradiction": "✗"}.get(claim["label"], "?")
            conf = f" ({claim['confidence']:.2f})" if claim.get("confidence") is not None else ""
            st.markdown(f"{icon} **{claim['label']}**{conf} — {claim['claim']['text']}  \n"
                        f"<small>{claim['method']}: {claim.get('rationale', '')} · evidências {', '.join(claim.get('evidence_ids', [])) or '—'}</small>",
                        unsafe_allow_html=True)
        for finding in v.get("logical_findings", []):
            st.markdown(f"{'✗' if finding['severity'] == 'error' else '⚠'} lógica · {finding['kind']} ({finding['method']}): {finding['description']}")
        if v.get("verifier_errors"):
            st.warning("Verificação incompleta: " + "; ".join(v["verifier_errors"]))
        if reply.get("original_answer"):
            st.markdown("**Resposta antes da correção:**")
            st.text(reply["original_answer"])
        tools = reply.get("tools_used") or []
        if tools:
            st.markdown("**Ferramentas:** " + " → ".join(tools))
        for src in reply.get("data_sources", []):
            when = src.get("source_updated_at") or src.get("retrieved_at") or ""
            st.markdown(f"- {src['provider']} · `{src['tool']}` {src.get('endpoint') or ''} {when}")
        for limitation in reply.get("limitations", []):
            st.markdown(f"- ⚠ {limitation}")


def load_session(session_id: str) -> None:
    try:
        backend().get_conversation(session_id)
        messages = backend().history(session_id)
    except BackendError as exc:
        st.session_state.error = str(exc)
        return
    st.session_state.session_id = session_id
    st.session_state.messages = [{"role": m["role"], "content": m["content"], "reply": None} for m in messages]
    st.session_state.error = None


def new_session() -> None:
    try:
        info = backend().create_conversation("Streamlit")
    except BackendError as exc:
        st.session_state.error = str(exc)
        return
    st.session_state.session_id = info["session_id"]
    st.session_state.messages = []
    st.session_state.error = None
    st.query_params["session"] = info["session_id"]


# --------------------------------------------------------------------------- estado

if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.session_id = None
    st.session_state.error = None
    if sid := st.query_params.get("session"):
        load_session(sid)

with st.sidebar:
    st.header("Conversa")
    if st.button("Nova conversa", use_container_width=True):
        new_session()
    typed = st.text_input("Retomar pelo id da sessão", value=st.session_state.session_id or "")
    if st.button("Retomar", use_container_width=True) and typed.strip():
        load_session(typed.strip())
        st.query_params["session"] = typed.strip()
    if st.session_state.session_id:
        st.caption(f"Sessão: `{st.session_state.session_id}`")
    st.divider()
    try:
        health = backend().health()
        st.caption(f"API: {health['status']} · modelo {health['chat_model']} · snapshots {health.get('market_snapshots')}")
    except BackendError as exc:
        st.error(str(exc))

st.title("Crypto Intelligence Agent")
st.caption("Dados ao vivo (CoinPaprika), snapshots (SQLite) e documentos locais (Chroma); respostas verificadas antes da entrega.")

if st.session_state.error:
    st.error(st.session_state.error)

if st.session_state.session_id is None:
    st.info("Crie uma conversa nova ou retome uma existente na barra lateral.")
    st.markdown("Exemplos de perguntas:\n" + "\n".join(f"- {e}" for e in EXAMPLES))
    st.stop()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("reply"):
            render_verification(message["reply"])

if prompt := st.chat_input("Pergunte sobre criptoativos..."):
    st.session_state.messages.append({"role": "user", "content": prompt, "reply": None})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Consultando ferramentas e verificando a resposta (pode levar alguns minutos em CPU)..."):
            try:
                reply = backend().ask(st.session_state.session_id, prompt)
            except BackendError as exc:
                st.error(str(exc))
                st.session_state.messages.append({"role": "assistant", "content": f"⚠ {exc}", "reply": None})
                st.stop()
        st.markdown(reply["answer"])
        render_verification(reply)
        st.caption(f"{reply.get('elapsed_s', 0):.0f} s")
    st.session_state.messages.append({"role": "assistant", "content": reply["answer"], "reply": reply})
