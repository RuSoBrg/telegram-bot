import streamlit as st
import logging
from uuid import uuid4
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from graph import grafo
from domain.models import WorkflowRun, WorkflowStatus
from domain.persistence import WorkflowStore
from domain.events import iter_app_events

logger = logging.getLogger(__name__)
load_dotenv()
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

config = {"configurable": {"thread_id": st.session_state.thread_id}}

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
            eventos = grafo.stream(
                {
                    "messages": [HumanMessage(content=prompt)],
                    "request_id": st.session_state.request_id,
                    "user_id": st.session_state.thread_id,
                },
                config=config,
                stream_mode="values",
            )
            respostas_agentes = []
            ultimo_estado = {}
            for event in iter_app_events(eventos):
                ultimo_estado = event.metadata.get("state", ultimo_estado)
                if event.type == "status" and event.content == "approval_required":
                    st.session_state.approval_required = True
                elif event.type == "message":
                    respostas_agentes.append(event.content)
                elif event.type == "error":
                    st.error(event.content)

            if respostas_agentes:
                resposta_final = respostas_agentes[-1]
                st.markdown(resposta_final)
                st.session_state.messages.append({"role": "assistant", "content": resposta_final})
            else:
                st.warning("O sistema processou o pedido mas não gerou uma resposta direta.")
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
            logger.exception("Falha ao executar o grafo para a sessão %s", st.session_state.thread_id)
            erro_msg = (
                "❌ O workflow terminou com erro.\n"
                f"Erro: {type(error).__name__}: {error}"
            )
            st.error(erro_msg)
            st.session_state.messages.append({"role": "assistant", "content": erro_msg})

if st.session_state.approval_required:
    st.info("O output passou na revisão automática e aguarda a tua aprovação.")
    col_approve, col_reject = st.columns(2)
    if col_approve.button("Aprovar e concluir"):
        list(
            iter_app_events(
                grafo.stream(
                    Command(resume="approved"),
                    config=config,
                    stream_mode="values",
                )
            )
        )
        st.session_state.approval_required = False
        st.rerun()
    if col_reject.button("Rejeitar"):
        list(
            iter_app_events(
                grafo.stream(
                    Command(resume="rejected"),
                    config=config,
                    stream_mode="values",
                )
            )
        )
        st.session_state.approval_required = False
        st.warning("Output rejeitado. Indica as alterações pretendidas.")