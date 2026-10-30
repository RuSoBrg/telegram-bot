import os
import logging
import json
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv

from telegram import InputFile, Update
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
from domain.observability import log_event

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


def _artifact_from_tool_message(message: ToolMessage) -> dict | None:
    try:
        artifact = json.loads(str(message.content))
    except (TypeError, json.JSONDecodeError):
        return None
    path = Path(artifact.get("path", "")).resolve()
    generated_dir = (Path(__file__).resolve().parent / "generated_files").resolve()
    if (
        artifact.get("tipo") != "ficheiro_gerado"
        or generated_dir not in path.parents
        or not path.is_file()
    ):
        return None
    artifact["path"] = str(path)
    return artifact


async def _send_graph_events(update: Update, eventos, request_id: str) -> bool:
    resposta_final = None
    artefactos: list[dict] = []
    approval_required = False
    for event in eventos:
        if "__interrupt__" in event:
            approval_required = True
        if "messages" not in event or not event["messages"]:
            continue
        ultima_msg = event["messages"][-1]
        if isinstance(ultima_msg, AIMessage) and ultima_msg.content:
            resposta_final = ultima_msg.content
        elif isinstance(ultima_msg, ToolMessage):
            artefacto = _artifact_from_tool_message(ultima_msg)
            if artefacto:
                artefactos.append(artefacto)

    run = workflow_store.get(request_id)
    if run:
        run.status = (
            WorkflowStatus.APPROVAL_REQUIRED
            if approval_required
            else WorkflowStatus.APPROVED
        )
        run.artifacts = [
            ArtifactRecord(
                filename=item["filename"],
                path=item["path"],
                mime_type=item.get("mime_type", "application/octet-stream"),
            )
            for item in artefactos
        ]
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
        await update.message.reply_text(
            "⚠️ O sistema processou o pedido, mas não gerou uma resposta direta."
        )

    for artefacto in artefactos:
        caminho = Path(artefacto["path"])
        with caminho.open("rb") as ficheiro:
            ficheiro_telegram = InputFile(ficheiro, filename=artefacto["filename"])
            caption = f"📎 {artefacto['filename']}"
            mime_type = artefacto.get("mime_type", "")
            if mime_type.startswith("image/"):
                await update.message.reply_photo(photo=ficheiro_telegram, caption=caption)
            elif mime_type.startswith("video/"):
                await update.message.reply_video(video=ficheiro_telegram, caption=caption)
            elif mime_type.startswith("audio/"):
                await update.message.reply_audio(audio=ficheiro_telegram, caption=caption)
            else:
                await update.message.reply_document(document=ficheiro_telegram, caption=caption)
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
        await _send_graph_events(update, eventos, request_id)
        log_event("workflow_finished", request_id=request_id)

    except Exception:
        logger.exception("Erro ao processar mensagem do chat %s", update.effective_chat.id)
        await update.message.reply_text(
            "❌ Ocorreu um erro ao processar o teu pedido. Tenta novamente mais tarde."
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
