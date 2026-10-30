import os
import logging
import json
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langgraph.types import Command
from graph import grafo
from domain.models import ArtifactRecord, WorkflowRun, WorkflowStatus
from domain.persistence import WorkflowStore
from domain.observability import log_event, timed_event
from domain.artifacts import ArtifactVersionStore
from domain.quotas import UserQuotaStore, QuotaExceededError
from domain.limits import load_runtime_limits
from domain.locks import WorkflowLockStore
from domain.locks import TaskQueue
from domain.object_store import create_object_store
from agents.publisher import artifact_from_tool_message, publish_artifacts

# Configuração de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente (.env)
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
workflow_store = WorkflowStore(os.getenv("WORKFLOW_DB_PATH", "workspace.db"))
artifact_store = ArtifactVersionStore(os.getenv("ARTIFACT_DB_PATH", "workspace.db"))
object_store = create_object_store()
task_queue = TaskQueue()
lock_store = WorkflowLockStore(
    os.getenv("WORKFLOW_DB_PATH", "workspace.db"),
    redis_url=os.getenv("REDIS_URL", ""),
    timeout_seconds=int(
        os.getenv(
            "REDIS_LOCK_TIMEOUT_SECONDS",
            str(load_runtime_limits().tool_timeout_seconds),
        )
    ),
)
quota_store = UserQuotaStore(
    os.getenv("WORKFLOW_DB_PATH", "workspace.db"),
    max_operations=load_runtime_limits().max_user_operations,
    window_seconds=load_runtime_limits().quota_window_seconds,
)


async def _send_graph_events(update: Update, eventos, request_id: str) -> bool:
    resposta_final = None
    artefactos: list[dict] = []
    approval_required = False
    latest_state: dict = {}
    for event in eventos:
        latest_state = event
        if "__interrupt__" in event:
            approval_required = True
        if "messages" not in event or not event["messages"]:
            continue
        ultima_msg = event["messages"][-1]
        if isinstance(ultima_msg, AIMessage) and ultima_msg.content:
            resposta_final = ultima_msg.content
        elif isinstance(ultima_msg, ToolMessage):
            artefacto = artifact_from_tool_message(ultima_msg)
            if artefacto:
                artefactos.append(artefacto)

    run = workflow_store.get(request_id)
    if run:
        run.status = (
            WorkflowStatus.APPROVAL_REQUIRED
            if approval_required
            else (
                WorkflowStatus.FAILED
                if latest_state.get("status") == "failed"
                else WorkflowStatus.APPROVED
            )
        )
        run.artifacts = [
            ArtifactRecord(
                filename=item["filename"],
                path=item["path"],
                mime_type=item.get("mime_type", "application/octet-stream"),
                size_bytes=Path(item["path"]).stat().st_size,
            )
            for item in artefactos
        ]
        for item in run.artifacts:
            artifact_store.register(
                item.filename,
                item.path,
                item.mime_type,
            )
            object_store.put(
                item.path,
                f"{request_id}/{item.filename}",
            )
        if latest_state.get("quality_report"):
            run.quality_report = latest_state["quality_report"]
        if latest_state.get("research_sources"):
            run.sources = latest_state["research_sources"]
        if latest_state.get("iteration") is not None:
            run.iteration = latest_state["iteration"]
        if latest_state.get("errors"):
            run.errors = latest_state["errors"]
        if latest_state.get("plan"):
            run.metadata["plan"] = latest_state["plan"]
        workflow_store.save(run)

    if resposta_final:
        if len(resposta_final) > 4096:
            for index in range(0, len(resposta_final), 4000):
                await update.message.reply_text(resposta_final[index : index + 4000])
        else:
            await update.message.reply_text(resposta_final)
    elif approval_required:
        await update.message.reply_text(
            "✅ O pacote passou na revisão automática.\n"
            "Responde com /aprovar para receber os ficheiros ou /rejeitar para pedir alterações."
        )
    else:
        errors = latest_state.get("errors", [])
        detail = "\n".join(str(error) for error in errors)
        await update.message.reply_text(
            "❌ O workflow terminou sem resposta."
            + (f"\nErro: {detail}" if detail else "")
        )

    if approval_required:
        return True

    await publish_artifacts(update, artefactos)
    return approval_required


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /start que cumprimenta o utilizador."""
    nome = update.effective_user.first_name if update.effective_user else "utilizador"
    await update.message.reply_text(
        f"Olá, {nome}! Sou o teu assistente inteligente.\n\n"
        "Envia-me uma mensagem e irei coordenar a equipa de agentes para te responder."
    )


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retoma o último workflow do chat depois de aprovação humana."""
    if not update.message or not update.effective_chat:
        return
    thread_id = str(update.effective_chat.id)
    try:
        await _send_graph_events(
            update,
            grafo.stream(
                Command(resume="approved"),
                config={"configurable": {"thread_id": thread_id}},
                stream_mode="values",
            ),
            context.user_data.get("request_id", ""),
        )
    except Exception:
        logger.exception("Falha ao aprovar workflow do chat %s", thread_id)
        await update.message.reply_text("❌ Não foi possível aprovar o workflow.")


async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retoma o workflow com rejeição e pede uma nova instrução ao agente."""
    if not update.message or not update.effective_chat:
        return
    thread_id = str(update.effective_chat.id)
    request_id = context.user_data.get("request_id", "")
    try:
        await update.message.reply_text(
            "O pacote foi rejeitado. Indica na próxima mensagem o que queres alterar."
        )
        await _send_graph_events(
            update,
            grafo.stream(
                Command(resume="rejected"),
                config={"configurable": {"thread_id": thread_id}},
                stream_mode="values",
            ),
            request_id,
        )
    except Exception:
        logger.exception("Falha ao rejeitar workflow do chat %s", thread_id)
        await update.message.reply_text("❌ Não foi possível rejeitar o workflow.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processa mensagens de texto e consulta o grafo LangGraph."""
    if not update.message or not update.message.text:
        return

    texto = update.message.text

    # Mostrar o estado 'A escrever...' no Telegram
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    try:
        quota_store.consume(
            str(update.effective_user.id if update.effective_user else update.effective_chat.id),
            "workflow",
        )
        request_id = str(uuid4())
        context.user_data["request_id"] = request_id
        workflow_store.save(
            WorkflowRun(
                request_id=request_id,
                workflow="auto",
                user_id=str(update.effective_user.id if update.effective_user else update.effective_chat.id),
                objective=texto,
                status=WorkflowStatus.PRODUCING,
            )
        )
        if os.getenv("ASYNC_WORKFLOWS", "false").lower() == "true":
            task_queue.enqueue(
                "workflows",
                json.dumps(
                    {
                        "request_id": request_id,
                        "thread_id": str(update.effective_chat.id),
                        "user_id": str(
                            update.effective_user.id
                            if update.effective_user
                            else update.effective_chat.id
                        ),
                        "text": texto,
                        "workflow": "auto",
                    },
                    ensure_ascii=False,
                ),
            )
            await update.message.reply_text(
                f"✅ Pedido colocado em fila.\nID: {request_id}\n"
                "O worker irá processá-lo e o estado ficará disponível para retoma."
            )
            return
        log_event(
            "workflow_started",
            request_id=request_id,
            workflow="auto",
            user_id=str(update.effective_chat.id),
        )
        eventos = grafo.stream(
            {
                "messages": [HumanMessage(content=texto)],
                "request_id": request_id,
                "user_id": str(update.effective_chat.id),
            },
            config={"configurable": {"thread_id": str(update.effective_chat.id)}},
            stream_mode="values",
        )
        with lock_store.acquire(
            f"chat:{update.effective_chat.id}",
            request_id,
        ):
            with timed_event(
                "workflow_execution",
                request_id=request_id,
                workflow="auto",
                agent="orchestrator",
            ):
                await _send_graph_events(update, eventos, request_id)
        log_event("workflow_finished", request_id=request_id)

    except QuotaExceededError as error:
        logger.warning("Quota excedida no chat %s: %s", update.effective_chat.id, error)
        await update.message.reply_text("⏳ A quota diária foi atingida. Tenta novamente mais tarde.")
    except Exception as error:
        logger.exception("Erro ao processar mensagem do chat %s", update.effective_chat.id)
        await update.message.reply_text(
            "❌ O workflow terminou com erro.\n"
            f"Erro: {type(error).__name__}: {error}"
        )


def main() -> None:
    """Ponto de entrada para iniciar o bot do Telegram."""
    if not TELEGRAM_TOKEN:
        logger.error("A variável de ambiente TELEGRAM_TOKEN não está definida.")
        raise ValueError("TELEGRAM_TOKEN não configurado. Adiciona-o ao ficheiro .env.")

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("aprovar", approve))
    application.add_handler(CommandHandler("rejeitar", reject))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot do Telegram iniciado e a escutar pedidos...")
    application.run_polling()


if __name__ == "__main__":
    main()
