import streamlit as st
import logging
from uuid import uuid4
from langchain_core.messages import HumanMessage, AIMessage
from graph import grafo

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Workspace AI - Startup", page_icon="🚀", layout="wide")

st.title("🚀 Empresa de Agentes AI (Workspace Pessoal & Profissional)")
st.markdown("O teu sistema centralizado com **Supervisor (Azure GPT-4o)** e **Especialistas (Google Gemini)**.")

# Inicializar o histórico de chat no Streamlit
if "messages" not in st.session_state:
    st.session_state.messages = []

# Cada sessão do browser tem uma memória LangGraph própria.
if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"streamlit-{uuid4()}"

config = {"configurable": {"thread_id": st.session_state.thread_id}}

# Mostrar mensagens anteriores na UI
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Caixa de input do utilizador
if prompt := st.chat_input("O que precisas de resolver hoje? (Ex: Ideias para TikTok, código Flutter, rotinas...)"):
    # Adicionar mensagem do utilizador ao histórico visual
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Executar o Grafo de Agentes
    with st.chat_message("assistant"), st.spinner(
        "A equipa está a analisar o teu pedido..."
    ):  
        try:
            eventos = grafo.stream(
                {"messages": [HumanMessage(content=prompt)]}, 
                config=config, 
                stream_mode="values"
            )
            
            respostas_agentes = []
            for event in eventos:
                ultima_msg = event["messages"][-1]
                if isinstance(ultima_msg, AIMessage) and ultima_msg.content:
                    respostas_agentes.append(ultima_msg.content)
                
            # Exibir a resposta final do agente competente
            if respostas_agentes:
                resposta_final = respostas_agentes[-1]
                st.markdown(resposta_final)
                st.session_state.messages.append({"role": "assistant", "content": resposta_final})
            else:
                st.warning("O sistema processou o pedido mas não gerou uma resposta direta.")
        except Exception:
            logger.exception("Falha ao executar o grafo para a sessão %s", st.session_state.thread_id)
            erro_msg = (
                "❌ Não foi possível processar o pedido neste momento. "
                "Consulta os logs da aplicação para mais detalhes."
            )
            st.error(erro_msg)
            st.session_state.messages.append({"role": "assistant", "content": erro_msg})