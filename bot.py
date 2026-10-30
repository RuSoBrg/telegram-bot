import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
import uvicorn
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from domain.artifacts import ArtifactVersionStore
from domain.events import iter_app_events
from domain.limits import load_runtime_limits
from domain.models import ArtifactRecord, WorkflowRun, WorkflowStatus
from domain.observability import log_event, timed_event
from domain.persistence import WorkflowStore
from domain.quotas import QuotaExceededError, UserQuotaStore
from graph import grafo
from tools.file_tools import store as object_store

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
workflow_store = WorkflowStore()
artifact_store = ArtifactVersionStore()
runtime_limits = load_runtime_limits()
quota_store = UserQuotaStore(
    max_operations=runtime_limits.max_user_operations,
    window_seconds=runtime_limits.quota_window_seconds,
)


def _workflow_status(approval_required: bool, latest_state: dict) -> WorkflowStatus:
    if approval_required:
        return WorkflowStatus.APPROVAL_REQUIRED
    if latest_state.get("status") == "failed":
        return WorkflowStatus.FAILED
    return WorkflowStatus.APPROVED


def _collect_graph_events(events):
    response = None
    artifacts = []
    approval_required = False
    latest_state = {}
    for event in events:
        latest_state = event.metadata.get("state", latest_state)
        if event.type == "status" and event.content == "approval_required":
            approval_required = True
        elif event.type == "message":
            response = event.content
        elif event.type == "artifact":
            artifacts.append(event.metadata)
    return response, artifacts, approval_required, latest_state


def _update_run(request_id, approval_required, latest_state, artifacts):
    run = workflow_store.get(request_id)
    if not run:
        return
    run.status = _workflow_status(approval_required, latest_state)
    run.artifacts = [
        ArtifactRecord(
            filename=item["filename"],
            path=item["uri"],
            mime_type=item.get("mime_type", "application/octet-stream"),
            size_bytes=item.get("size_bytes", 0),
        )
        for item in artifacts
    ]
    for item in run.artifacts:
        try:
            artifact_store.register(item.filename, item.path, item.mime_type)
        except FileNotFoundError:
            logger.warning("Artefacto não é um caminho local: %s", item.path)
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


async def _send_workflow_message(context, chat_id: int, response: str | None, approval_required: bool, errors):
    if response:
        for index in range(0, len(response), 4000):
            await context.bot.send_message(chat_id=chat_id, text=response[index : index + 4000])
    elif approval_required:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✅ O pacote passou na revisão automática.\n"
                "Responde com /aprovar para receber os ficheiros ou /rejeitar para pedir alterações."
            ),
        )
    else:
        detail = "\n".join(str(error) for error in errors)
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ O workflow terminou sem resposta." + (f"\nErro: {detail}" if detail else ""),
        )


async def _send_artifacts(context, chat_id: int, artifacts: list[dict]) -> None:
    reader = getattr(object_store, "get_bytes", None)
    if reader is None and artifacts:
        raise RuntimeError("O ObjectStore configurado não suporta leitura de artefactos.")
    for artifact in artifacts:
        filename = artifact.get("filename", "")
        key = artifact.get("key") or filename
        file_bytes = await asyncio.to_thread(reader, key)
        await context.bot.send_document(
            chat_id=chat_id,
            document=file_bytes,
            filename=filename,
            caption=f"📎 {filename}",
        )


async def _send_graph_events(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    eventos,
    request_id: str,
) -> bool:
    resposta_final = None
    artefactos: list[dict] = []
    approval_required = False
    latest_state: dict = {}
    events = await asyncio.to_thread(lambda: list(iter_app_events(eventos)))
    resposta_final, artefactos, approval_required, latest_state = _collect_graph_events(events)

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        raise ValueError("Não foi possível determinar o chat do Telegram.")

    _update_run(request_id, approval_required, latest_state, artefactos)

    await _send_workflow_message(
        context,
        chat_id,
        resposta_final,
        approval_required,
        latest_state.get("errors", []),
    )

    if approval_required:
        return True

    await _send_artifacts(context, chat_id, artefactos)
    return False


_background_tasks: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _stream_graph(input_value, chat_id: str):
    return grafo.stream(
        input_value,
        config={"configurable": {"thread_id": chat_id}},
        stream_mode="values",
    )


async def _run_graph_events(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    graph_input,
    chat_id: str,
    request_id: str,
) -> None:
    try:
        eventos = _stream_graph(graph_input, chat_id)
        with timed_event(
            "workflow_execution",
            request_id=request_id,
            workflow="auto",
            agent="orchestrator",
        ):
            await _send_graph_events(update, context, eventos, request_id)
        log_event("workflow_finished", request_id=request_id)
    except Exception:
        logger.exception("Erro ao executar workflow %s", request_id)
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ O workflow terminou com erro. Tenta novamente mais tarde.",
            )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    nome = update.effective_user.first_name if update.effective_user else "utilizador"
    await update.message.reply_text(
        f"Olá, {nome}! Sou o teu assistente inteligente.\n\n"
        "Envia-me uma mensagem e irei coordenar a equipa de agentes para te responder."
    )


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    thread_id = str(update.effective_chat.id)
    await update.message.reply_text("✅ A aprovação foi recebida. A processar...")
    task = asyncio.create_task(
        _run_graph_events(
            update,
            context,
            Command(resume="approved"),
            thread_id,
            context.user_data.get("request_id", ""),
        )
    )
    _track_task(task)


async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    thread_id = str(update.effective_chat.id)
    await update.message.reply_text("O pacote foi rejeitado. A processar a alteração...")
    task = asyncio.create_task(
        _run_graph_events(
            update,
            context,
            Command(resume="rejected"),
            thread_id,
            context.user_data.get("request_id", ""),
        )
    )
    _track_task(task)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_chat:
        return

    texto = update.message.text
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )
    try:
        quota_store.consume(
            str(update.effective_user.id if update.effective_user else update.effective_chat.id),
            "workflow",
        )
    except QuotaExceededError as error:
        logger.warning("Quota excedida no chat %s: %s", update.effective_chat.id, error)
        await update.message.reply_text("⏳ A quota diária foi atingida. Tenta novamente mais tarde.")
        return

    request_id = str(uuid4())
    chat_id = str(update.effective_chat.id)
    context.user_data["request_id"] = request_id
    workflow_store.save(
        WorkflowRun(
            request_id=request_id,
            workflow="auto",
            user_id=str(update.effective_user.id if update.effective_user else chat_id),
            objective=texto,
            status=WorkflowStatus.PRODUCING,
        )
    )
    log_event("workflow_started", request_id=request_id, workflow="auto", user_id=chat_id)
    await update.message.reply_text("⏳ A processar o teu pedido...")
    task = asyncio.create_task(
        _run_graph_events(
            update,
            context,
            {
                "messages": [HumanMessage(content=texto)],
                "request_id": request_id,
                "user_id": chat_id,
            },
            chat_id,
            request_id,
        )
    )
    _track_task(task)


def _build_application() -> Application:
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN não configurado. Adiciona-o ao ficheiro .env.")
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("aprovar", approve))
    application.add_handler(CommandHandler("rejeitar", reject))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return application


telegram_application = _build_application() if TELEGRAM_TOKEN else None
WEBHOOK_PATH = os.getenv("TELEGRAM_WEBHOOK_PATH", "/telegram/webhook")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")


@asynccontextmanager
async def _lifespan(_: FastAPI):
    if telegram_application is None:
        raise RuntimeError("TELEGRAM_TOKEN não configurado.")
    await telegram_application.initialize()
    await telegram_application.start()
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").rstrip("/")
    if webhook_url:
        await telegram_application.bot.set_webhook(
            f"{webhook_url}{WEBHOOK_PATH}",
            secret_token=WEBHOOK_SECRET or None,
        )
    try:
        yield
    finally:
        await telegram_application.stop()
        await telegram_application.shutdown()


api = FastAPI(lifespan=_lifespan)


@api.post(
    WEBHOOK_PATH,
    responses={
        401: {"description": "Invalid webhook secret"},
        503: {"description": "Telegram application unavailable"},
    },
)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
):
    if WEBHOOK_SECRET and not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "", WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    if telegram_application is None:
        raise HTTPException(status_code=503, detail="Telegram application unavailable")
    payload = await request.json()
    await telegram_application.process_update(Update.de_json(payload, telegram_application.bot))
    return {"ok": True}


def main() -> None:
    logger.info("Bot do Telegram iniciado em %s", WEBHOOK_PATH)
    uvicorn.run(api, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
