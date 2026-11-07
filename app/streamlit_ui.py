import streamlit as st
import asyncio
from uuid import uuid4
from langgraph.types import Command
from app.service import run_job
from domain.models import WorkflowRun, WorkflowStatus
from domain.db import WorkflowStore

workflow_store = WorkflowStore()

st.set_page_config(page_title="Workspace AI - Startup", page_icon="🚀", layout="wide")

st.title("🚀 Empresa de Agentes AI (Workspace Pessoal & Profissional)")
st.markdown("O teu sistema centralizado com **Supervisor (Google Gemini)** e **Especialistas (Google Gemini)**.")

# Inicializar o histórico de chat no Streamlit
if "messages" not in st.session_state:
    st.session_state.messages = []

# Cada sessão do browser tem uma memória LangGraph própria.
if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"streamlit-{uuid4()}"
if "request_id" not in st.session_state:
    st.session_state.request_id = str(uuid4())
if "approval_required" not in st.session_state:
    st.session_state.approval_required = False

# Mostrar mensagens anteriores na UI
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Caixa de input do utilizador
if prompt := st.chat_input("O que precisas de resolver hoje? (Ex: Ideias para TikTok, código Flutter, rotinas...)"):
    st.session_state.request_id = str(uuid4())
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"), st.spinner("A equipa está a analisar o teu pedido..."):
        try:
            result = asyncio.run(
                run_job(
                    prompt,
                    st.session_state.thread_id,
                    user_id=st.session_state.thread_id,
                )
            )
            st.session_state.approval_required = result.needs_approval
            ultimo_estado = result.state
            if result.text:
                resposta_final = result.text
                st.markdown(resposta_final)
                st.session_state.messages.append({"role": "assistant", "content": resposta_final})
            else:
                st.warning(result.errors[0] if result.errors else "O pedido não gerou uma resposta direta.")
            if st.session_state.get("approval_required", False):
                run_status = WorkflowStatus.APPROVAL_REQUIRED
            elif ultimo_estado.get("status") == "failed":
                run_status = WorkflowStatus.FAILED
            else:
                run_status = WorkflowStatus.APPROVED
            run = WorkflowRun(
                request_id=st.session_state.request_id,
                workflow="auto",
                user_id=st.session_state.thread_id,
                objective=prompt,
                status=run_status,
            )
            if ultimo_estado.get("quality_report"):
                run.quality_report = ultimo_estado["quality_report"]
            if ultimo_estado.get("research_sources"):
                run.sources = ultimo_estado["research_sources"]
            run.iteration = ultimo_estado.get("iteration", 0)
            if ultimo_estado.get("plan"):
                run.metadata["plan"] = ultimo_estado["plan"]
            workflow_store.save(run)
        except Exception as error:
            import logging
            logger = logging.getLogger("streamlit_app")
            short_id = st.session_state.request_id[:8]
            logger.exception("Falha no workflow %s: %s", st.session_state.request_id, error)
            erro_msg = f"⚠️ Ocorreu um erro ao processar o teu pedido (ref: {short_id}). Por favor, tenta novamente."
            st.error(erro_msg)
            st.session_state.messages.append({"role": "assistant", "content": erro_msg})

if st.session_state.approval_required:
    st.info("O output passou na revisão automática e aguarda a tua aprovação.")
    col_approve, col_reject = st.columns(2)
    if col_approve.button("Aprovar e concluir"):
        result = asyncio.run(
            run_job(
                Command(resume="approved"),
                st.session_state.thread_id,
                user_id=st.session_state.thread_id,
            )
        )
        st.session_state.approval_required = result.needs_approval
        if result.text:
            st.session_state.messages.append({"role": "assistant", "content": result.text})
        if result.artifacts:
            art_text = "Ficheiros gerados:\n" + "\n".join(f"- 📎 {a.get('filename')}" for a in result.artifacts)
            st.session_state.messages.append({"role": "assistant", "content": art_text})
        st.rerun()
    if col_reject.button("Rejeitar"):
        result = asyncio.run(
            run_job(
                Command(resume="rejected"),
                st.session_state.thread_id,
                user_id=st.session_state.thread_id,
            )
        )
        st.session_state.approval_required = result.needs_approval
        if result.text:
            st.session_state.messages.append({"role": "assistant", "content": result.text})
        st.warning("Output rejeitado. Indica as alterações pretendidas.")