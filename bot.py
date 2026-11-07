import asyncio
import hmac
import logging
import sqlite3
import weakref
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
import uvicorn
import litellm
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from telegram import Update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from domain.db import ArtifactStore, init_db
from domain.events import iter_app_events
from domain.models import ArtifactRecord, WorkflowRun, WorkflowStatus
from domain.observability import log_event, timed_event
from domain.db import WorkflowStore
from workflows.main_workflow import GRAPH_RECURSION_LIMIT
from workflows.main_workflow import build_async_workflow
from psycopg import Error as PsycopgError
from routing.availability import AvailabilityStore
from routing.alerts import configure_alert_sender
from routing.catalog import load_catalog
from routing.router import ModelRouter
from routing.errors import ErrorKind, classify_error
from routing.alerts import notify_disabled
from app.service import WorkflowService
from app.settings import get_settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = get_settings().telegram_token
workflow_store = WorkflowStore()
artifact_store = ArtifactStore()
grafo: Any | None = None
workflow_service = WorkflowService()
_availability_cleanup_task: asyncio.Task | None = None
_chat_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _allowed_user_ids() -> set[int]:
    return set(get_settings().telegram_user_ids)


def _is_authorized(update: Update) -> bool:
    allowed = _allowed_user_ids()
    if not allowed:
        public = get_settings().allow_public
        if not public:
            logger.warning("ALLOWED_USER_IDS está vazio e ALLOW_PUBLIC=false; acesso recusado.")
        return public
    user = update.effective_user
    return bool(user and user.id in allowed)


async def _reject_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id if user else "desconhecido"
    logger.warning("Tentativa de acesso não autorizada: user_id=%s", user_id)
    if update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Não tens autorização para usar este bot.",
        )


def _lock_for_chat(chat_id: str) -> asyncio.Lock:
    return _chat_locks.setdefault(chat_id, asyncio.Lock())


def _format_availability(value: Any) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%d/%m %H:%M")


def _state_text() -> str:
    store = AvailabilityStore()
    status_by_model = {
        (item["provider"], item["model"]): item
        for item in store.snapshot()
    }
    lines = ["📊 Estado dos modelos"]
    for agent, entries in load_catalog().items():
        lines.append(f"\n{agent}:")
        for entry in entries:
            item = status_by_model.get((entry.provider, entry.model))
            if item and not store.is_available(entry.provider, entry.model):
                lines.append(
                    f"• {entry.model_id} — ⏳ volta a "
                    f"{_format_availability(item['unavailable_until'])} "
                    f"({item.get('reason') or 'indisponível'})"
                )
            else:
                lines.append(f"• {entry.model_id} — ✅ disponível")
    return "\n".join(lines)


async def estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update, context)
        return
    if update.effective_chat:
        state_text = await asyncio.to_thread(_state_text)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=state_text,
        )


async def _cleanup_availability_periodically() -> None:
    while True:
        await asyncio.sleep(300)
        try:
            cleared = await asyncio.to_thread(
                AvailabilityStore().clear_expired
            )
        except (OSError, sqlite3.Error, PsycopgError) as error:
            logger.warning("Não foi possível limpar cooldowns expirados: %s", error)
            continue
        if cleared:
            logger.info("Foram removidos %d cooldowns expirados.", cleared)


async def _validate_catalog_models() -> None:
    store = AvailabilityStore()
    seen: set[tuple[str, str]] = set()
    for entries in load_catalog().values():
        for entry in entries:
            key = (entry.provider, entry.model)
            if key in seen:
                continue
            seen.add(key)
            try:
                request = ModelRouter._kwargs(
                    entry,
                    [{"role": "user", "content": "ping"}],
                    None,
                )
                await litellm.acompletion(
                    **request,
                    max_tokens=1,
                    timeout=min(5, entry.timeout_s),
                )
            except Exception as error:
                classified = classify_error(error)
                if classified.kind in {ErrorKind.AUTH, ErrorKind.MODEL_NOT_FOUND}:
                    until = datetime.now(timezone.utc) + timedelta(days=365)
                    store.mark_unavailable(
                        entry.provider,
                        entry.model,
                        until,
                        classified.kind.value,
                        str(error),
                    )
                    await notify_disabled(
                        classified.disable or "model",
                        entry.provider,
                        entry.model,
                        classified.kind.value,
                    )
                logger.warning(
                    "Validação inicial falhou para %s: %s", entry.model_id, error
                )


def _workflow_status(
    approval_required: bool,
    latest_state: Mapping[str, Any],
) -> WorkflowStatus:
    if latest_state.get("status") == "failed":
        return WorkflowStatus.FAILED
    if latest_state.get("status") == "degraded":
        return WorkflowStatus.DEGRADED
    if approval_required:
        return WorkflowStatus.APPROVAL_REQUIRED
    if latest_state.get("status") == "approved" or latest_state.get("approval_decision") == "approved":
        return WorkflowStatus.APPROVED
    return WorkflowStatus.COMPLETED


def _collect_graph_events(
    events: list[Any],
) -> tuple[str | None, list[dict[str, Any]], bool, dict[str, Any]]:
    response: str | None = None
    artifacts: list[dict[str, Any]] = []
    approval_required = False
    latest_state: dict[str, Any] = {}
    for event in events:
        latest_state = event.metadata.get("state", latest_state)
        if event.type == "status" and event.content == "approval_required":
            approval_required = True
        elif event.type == "message":
            response = event.content
        elif event.type == "artifact":
            artifacts.append(event.metadata)
    return response, artifacts, approval_required, latest_state


def _update_run(
    request_id: str,
    approval_required: bool,
    latest_state: Mapping[str, Any],
    artifacts: list[dict[str, Any]],
) -> None:
    run = workflow_store.get(request_id)
    if not run:
        return
    run.status = _workflow_status(approval_required, latest_state)
    run.artifacts = [
        ArtifactRecord(
            artifact_id=item.get("artifact_id", ""),
            filename=item["filename"],
            path=item["uri"],
            mime_type=item.get("mime_type", "application/octet-stream"),
            size_bytes=item.get("size_bytes", 0),
        )
        for item in artifacts
    ]
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


async def _send_workflow_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    response: str | None,
    approval_required: bool,
    errors: list[Any],
    latest_state: Mapping[str, Any],
    request_id: str = "",
) -> None:
    failed = latest_state.get("status") == "failed" or bool(errors)
    if approval_required and not failed:
        callback_ap = f"ap:{request_id}" if request_id else "workflow:approved"
        callback_rj = f"rj:{request_id}" if request_id else "workflow:rejected"
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✅ O pacote está pronto para decisão humana.\n"
                "Escolhe uma ação:"
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Aprovar", callback_data=callback_ap),
                        InlineKeyboardButton("Rejeitar", callback_data=callback_rj),
                    ],
                ]
            ),
        )
    else:
        text = _best_available_text(
            latest_state,
            errors[0] if errors else None,
            response=response,
        )
        if latest_state.get("status") == "degraded":
            text = f"⚠️ Resposta de último recurso\n\n{text}"
        text = _with_model_footer(text, latest_state)
        for index in range(0, len(text), 4000):
            await context.bot.send_message(chat_id=chat_id, text=text[index : index + 4000])


def _with_model_footer(text: str, latest_state: Mapping[str, Any]) -> str:
    if not get_settings().show_model_footer:
        return text
    if "— " in text[-120:]:
        return text
    agent = latest_state.get("agent_used") or latest_state.get("next_agent")
    model = latest_state.get("model_used")
    if not agent or not model:
        return text
    return f"{text.rstrip()}\n\n— {agent} · {model}"


def _best_available_text(
    latest_state: Mapping[str, Any],
    error: Any = None,
    *,
    response: str | None = None,
) -> str:
    """Choose a useful final response, including when a workflow failed."""
    candidates = [response, latest_state.get("response"), latest_state.get("final_response")]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate

    for message in reversed(latest_state.get("messages") or []):
        if isinstance(message, AIMessage):
            content = message.content
        elif isinstance(message, Mapping):
            if message.get("role") not in {"assistant", "ai"}:
                continue
            content = message.get("content")
        else:
            continue
        if isinstance(content, str) and content.strip():
            return content

    request = latest_state.get("objective") or latest_state.get("request")
    if not request:
        for message in latest_state.get("messages") or []:
            if isinstance(message, HumanMessage) and isinstance(message.content, str):
                request = message.content
                break
    request = request or "o teu pedido"
    if error is not None:
        logger.warning("Resposta de último recurso após erro de workflow: %s", error)
    return (
        f"⚠️ Não consegui concluir «{request}». "
        "O serviço encontrou uma dificuldade temporária. "
        "Podes tentar novamente com o mesmo pedido."
    )


async def _send_artifacts(context, chat_id: int, artifacts: list[dict]) -> None:
    from pathlib import Path
    for artifact in artifacts:
        filename = artifact.get("filename", "")
        file_bytes = None
        artifact_id = artifact.get("artifact_id")
        if artifact_id:
            try:
                file_bytes = await asyncio.to_thread(artifact_store.get_bytes, artifact_id)
            except Exception as e:
                logger.warning("Falha ao ler bytes do artefacto %s: %s", artifact_id, e)
        if not file_bytes:
            uri = artifact.get("uri") or artifact.get("path")
            if uri and not uri.startswith("db://"):
                path = Path(uri).resolve()
                if path.is_file() and path.stat().st_size <= 50 * 1024 * 1024:
                    file_bytes = await asyncio.to_thread(path.read_bytes)
        if not file_bytes:
            logger.warning("Artefacto %s não encontrado para envio.", filename)
            continue
        await context.bot.send_document(
            chat_id=chat_id,
            document=file_bytes,
            filename=filename,
            caption=f"📎 {filename}",
        )


async def _send_graph_events(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    eventos: AsyncIterator[Any],
    request_id: str,
) -> bool:
    resposta_final = None
    artefactos: list[dict] = []
    approval_required = False
    latest_state: dict = {}
    result = await workflow_service.collect(eventos)
    resposta_final = result.text
    artefactos = result.artifacts
    approval_required = result.needs_approval
    latest_state = result.state

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        raise ValueError("Não foi possível determinar o chat do Telegram.")

    await asyncio.to_thread(
        _update_run, request_id, approval_required, latest_state, artefactos
    )

    await _send_workflow_message(
        context,
        chat_id,
        resposta_final,
        approval_required,
        result.errors,
        latest_state,
        request_id,
    )

    if approval_required:
        return True

    await _send_artifacts(context, chat_id, artefactos)
    return False


_background_tasks: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _iter_graph_events(eventos: AsyncIterator[Any]) -> AsyncIterator[Any]:
    async for event in eventos:
        for app_event in iter_app_events([event]):
            yield app_event


_concurrency_semaphore = asyncio.Semaphore(2)
_active_chat_threads: dict[str, str] = {}


async def _keep_typing(bot: Any, chat_id: int, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass


def _dispatch_workflow_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    graph_input: Any,
    chat_id: str,
    request_id: str,
) -> asyncio.Task:
    coro = _run_graph_events(update, context, graph_input, chat_id, request_id)
    if hasattr(context, "application") and hasattr(context.application, "create_task"):
        task = context.application.create_task(coro)
    else:
        task = asyncio.create_task(coro)
    _track_task(task)
    return task


async def _stream_graph(input_value: Any, thread_id: str) -> Any:
    if grafo is None:
        raise RuntimeError("O grafo ainda não foi inicializado.")
    return grafo.astream(
        input_value,
        config={
            "configurable": {"thread_id": thread_id},
            "recursion_limit": GRAPH_RECURSION_LIMIT,
        },
        stream_mode="values",
    )


async def _run_graph_events(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    graph_input: Any,
    chat_id: str,
    request_id: str,
) -> None:
    chat_numeric_id = update.effective_chat.id if update.effective_chat else None
    stop_typing = asyncio.Event()
    typing_task = None
    if chat_numeric_id:
        typing_task = asyncio.create_task(_keep_typing(context.bot, chat_numeric_id, stop_typing))

    lock = _lock_for_chat(chat_id)
    try:
        async with _concurrency_semaphore:
            async with lock:
                timeout = float(get_settings().request_budget_seconds or 90.0)
                try:
                    eventos = await _stream_graph(graph_input, chat_id)
                    with timed_event(
                        "workflow_execution",
                        request_id=request_id,
                        workflow="auto",
                        agent="orchestrator",
                    ):
                        await asyncio.wait_for(
                            _send_graph_events(update, context, eventos, request_id),
                            timeout=timeout,
                        )
                    log_event("workflow_finished", request_id=request_id)
                except asyncio.TimeoutError:
                    logger.exception("Timeout ao executar workflow %s", request_id)
                    short_id = request_id[:8]
                    if update.effective_chat:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=f"⚠️ O pedido excedeu o tempo limite (ref: {short_id}). Por favor, tenta novamente.",
                        )
                except Exception as error:
                    logger.exception("Erro ao executar workflow %s: %s", request_id, error)
                    short_id = request_id[:8]
                    if update.effective_chat:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=f"⚠️ Ocorreu um erro ao processar o teu pedido (ref: {short_id}).",
                        )
    finally:
        stop_typing.set()
        if typing_task:
            typing_task.cancel()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update, context)
        return
    nome = update.effective_user.first_name if update.effective_user else "utilizador"
    await update.message.reply_text(
        f"Olá, {nome}! Sou o teu assistente inteligente.\n\n"
        "Envia-me uma mensagem e irei coordenar a equipa de agentes para te responder."
    )


async def _send_admin_alert(text: str, _: str) -> None:
    admin_chat_id = get_settings().admin_chat_id.strip()
    if not admin_chat_id or telegram_application is None:
        return
    await telegram_application.bot.send_message(chat_id=int(admin_chat_id), text=text)


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update, context)
        return
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    thread_id = _active_chat_threads.get(chat_id) or chat_id
    await update.message.reply_text("✅ A aprovação foi recebida. A processar...")
    _dispatch_workflow_task(
        update,
        context,
        Command(resume="approved"),
        thread_id,
        (context.user_data or {}).get("request_id", ""),
    )


async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update, context)
        return
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    thread_id = _active_chat_threads.get(chat_id) or chat_id
    await update.message.reply_text("O pacote foi rejeitado. A processar a alteração...")
    _dispatch_workflow_task(
        update,
        context,
        Command(resume="rejected"),
        thread_id,
        (context.user_data or {}).get("request_id", ""),
    )


async def approval_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Resume the durable LangGraph thread from an inline approval action."""
    query = update.callback_query
    if query is None or query.message is None:
        return
    if not _is_authorized(update):
        await query.answer("Não tens autorização para esta ação.", show_alert=True)
        return

    data = query.data or ""
    if ":" not in data:
        await query.answer("Ação de aprovação inválida.", show_alert=True)
        return

    prefix, param = data.split(":", 1)
    if prefix in {"ap", "rj"}:
        action = "approved" if prefix == "ap" else "rejected"
        request_id = param
    elif prefix == "workflow":
        action = param
        request_id = (context.user_data or {}).get("request_id", "")
    else:
        await query.answer("Ação não reconhecida.", show_alert=True)
        return

    if action not in {"approved", "rejected"}:
        await query.answer("Ação de aprovação inválida.", show_alert=True)
        return

    run = await asyncio.to_thread(workflow_store.get, request_id) if request_id else None
    if not run:
        await query.answer("Pedido não encontrado ou já expirado.", show_alert=True)
        await query.edit_message_text("Pedido não encontrado ou já expirado.")
        return

    user = update.effective_user
    if user and str(user.id) != str(run.user_id):
        await query.answer("Apenas o autor do pedido pode aprovar ou rejeitar.", show_alert=True)
        return

    if run.status != WorkflowStatus.APPROVAL_REQUIRED:
        await query.answer("Esta ação já foi processada anteriormente.", show_alert=True)
        await query.edit_message_text("ℹ️ Esta decisão já tinha sido registada.")
        return

    decision = action
    run.status = WorkflowStatus.APPROVED if decision == "approved" else WorkflowStatus.REJECTED
    await asyncio.to_thread(workflow_store.save, run)
    await query.answer()

    thread_id = (run.metadata or {}).get("thread_id") or f"{query.message.chat.id}:{request_id}"
    config = {"configurable": {"thread_id": thread_id}}
    if grafo is None:
        await query.edit_message_text("O workflow ainda não está disponível.")
        return

    await grafo.aupdate_state(
        config,
        {"approval_decision": decision, "approval_required": False},
    )
    await query.edit_message_text(
        "✅ Aprovado. A processar..." if decision == "approved"
        else "❌ Rejeitado. A processar..."
    )

    if decision == "rejected":
        _active_chat_threads[str(query.message.chat.id)] = thread_id

    _dispatch_workflow_task(
        update,
        context,
        Command(resume=decision),
        thread_id,
        request_id,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update, context)
        return
    if not update.message or not update.message.text or not update.effective_chat:
        return

    texto = update.message.text
    chat_id_str = str(update.effective_chat.id)
    request_id = str(uuid4())
    active_thread = _active_chat_threads.pop(chat_id_str, None)
    thread_id = active_thread or f"{chat_id_str}:{request_id}"

    user_id_str = str(update.effective_user.id if update.effective_user else chat_id_str)
    run = WorkflowRun(
        request_id=request_id,
        workflow="auto",
        user_id=user_id_str,
        objective=texto,
        status=WorkflowStatus.PRODUCING,
        metadata={"thread_id": thread_id},
    )
    await asyncio.to_thread(workflow_store.save, run)
    log_event("workflow_started", request_id=request_id, workflow="auto", user_id=chat_id_str)
    await update.message.reply_text("⏳ A processar o teu pedido...")

    _dispatch_workflow_task(
        update,
        context,
        {
            "messages": [HumanMessage(content=texto)],
            "request_id": request_id,
            "user_id": chat_id_str,
        },
        thread_id,
        request_id,
    )


def _build_application() -> Application:
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN não configurado. Adiciona-o ao ficheiro .env.")
    allowed_ids = get_settings().require_allowed_users()
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    user_filter = filters.User(user_id=allowed_ids)
    application.add_handler(CommandHandler("start", start, filters=user_filter))
    application.add_handler(CommandHandler("estado", estado, filters=user_filter))
    application.add_handler(CommandHandler("modelos", estado, filters=user_filter))
    application.add_handler(CommandHandler("aprovar", approve, filters=user_filter))
    application.add_handler(CommandHandler("rejeitar", reject, filters=user_filter))
    application.add_handler(CallbackQueryHandler(approval_callback, pattern=r"^(ap|rj|workflow):"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & user_filter, handle_message))
    application.add_handler(MessageHandler(~user_filter, _reject_unauthorized))
    return application


def _get_telegram_application() -> Application | None:
    if not TELEGRAM_TOKEN:
        return None
    if not get_settings().telegram_user_ids:
        return None
    return _build_application()


telegram_application = _get_telegram_application()
WEBHOOK_PATH = "/telegram/webhook"
WEBHOOK_SECRET = get_settings().telegram_webhook_secret


@asynccontextmanager
async def _lifespan(_: FastAPI):
    global grafo, _availability_cleanup_task, telegram_application
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN não configurado.")
    get_settings().require_allowed_users()
    if telegram_application is None:
        telegram_application = _build_application()
    settings = get_settings()
    base_url = settings.telegram_webhook_base_url.strip().rstrip("/")
    if base_url and not settings.telegram_webhook_secret.strip():
        raise RuntimeError(
            "TELEGRAM_WEBHOOK_SECRET é obrigatório quando o webhook está configurado."
        )
    await init_db()
    grafo = await build_async_workflow()
    configure_alert_sender(_send_admin_alert if settings.admin_chat_id.strip() else None)
    _availability_cleanup_task = asyncio.create_task(
        _cleanup_availability_periodically(),
        name="availability-cleanup",
    )
    try:
        await _validate_catalog_models()
    except Exception:
        logger.exception("Validação do catálogo falhou; o arranque prossegue.")
    await telegram_application.initialize()
    await telegram_application.start()
    polling_started = False
    if base_url:
        webhook_url = f"{base_url}{WEBHOOK_PATH}"
        logger.info("A configurar Webhook do Telegram em: %s", webhook_url)
        await telegram_application.bot.set_webhook(
            url=webhook_url,
            secret_token=WEBHOOK_SECRET or None,
            drop_pending_updates=True,
        )
        logger.info("Webhook configurado com sucesso!")
    else:
        logger.info(
            "TELEGRAM_WEBHOOK_BASE_URL não configurado; a iniciar polling do Telegram."
        )
        await telegram_application.bot.delete_webhook(drop_pending_updates=True)
        if telegram_application.updater is None:
            raise RuntimeError("Updater do Telegram não está disponível para polling.")
        await telegram_application.updater.start_polling(drop_pending_updates=True)
        polling_started = True
    try:
        yield
    finally:
        if _availability_cleanup_task is not None:
            _availability_cleanup_task.cancel()
            await asyncio.gather(_availability_cleanup_task, return_exceptions=True)
            _availability_cleanup_task = None
        configure_alert_sender(None)
        if polling_started and telegram_application.updater is not None:
            await telegram_application.updater.stop()
        await telegram_application.stop()
        await telegram_application.shutdown()
        grafo = None


api = FastAPI(lifespan=_lifespan)


@api.get("/")
@api.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "telegram-bot"}


@api.get("/webhook-info")
async def get_webhook_info() -> dict[str, Any]:
    if telegram_application is None:
        return {"error": "Telegram application not configured"}
    info = await telegram_application.bot.get_webhook_info()
    return {
        "url": info.url,
        "has_custom_certificate": info.has_custom_certificate,
        "pending_update_count": info.pending_update_count,
        "last_error_date": str(info.last_error_date) if info.last_error_date else None,
        "last_error_message": info.last_error_message,
        "max_connections": info.max_connections,
        "ip_address": info.ip_address,
    }


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
) -> dict[str, bool]:
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
    settings = get_settings()
    host = settings.host
    port = settings.port
    logger.info("Bot do Telegram iniciado em http://%s:%d%s", host, port, WEBHOOK_PATH)
    uvicorn.run(api, host=host, port=port)


if __name__ == "__main__":
    main()
