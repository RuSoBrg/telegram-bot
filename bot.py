import os
import logging
import json
from pathlib import Path
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
from graph import grafo

# Configuração de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente (.env)
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /start que cumprimenta o utilizador."""
    nome = update.effective_user.first_name if update.effective_user else "utilizador"
    await update.message.reply_text(
        f"Olá, {nome}! Sou o teu assistente inteligente.\n\n"
        "Envia-me uma mensagem e irei coordenar a equipa de agentes para te responder."
    )


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
        # Executar o grafo com o histórico da conversa associado ao thread_id do chat
        eventos = grafo.stream(
            {"messages": [HumanMessage(content=texto)]},
            config={"configurable": {"thread_id": str(update.effective_chat.id)}},
            stream_mode="values",
        )

        # Extrair o conteúdo da última AIMessage
        resposta_final = None
        artefactos: list[dict] = []
        for event in eventos:
            if "messages" in event and event["messages"]:
                ultima_msg = event["messages"][-1]
                if isinstance(ultima_msg, AIMessage) and ultima_msg.content:
                    resposta_final = ultima_msg.content
                elif isinstance(ultima_msg, ToolMessage):
                    artefacto = _artifact_from_tool_message(ultima_msg)
                    if artefacto:
                        artefactos.append(artefacto)

        if resposta_final:
            # Dividir em partes se exceder o limite do Telegram (4096 caracteres)
            if len(resposta_final) > 4096:
                for i in range(0, len(resposta_final), 4000):
                    await update.message.reply_text(resposta_final[i : i + 4000])
            else:
                await update.message.reply_text(resposta_final)
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
                    await update.message.reply_document(
                        document=ficheiro_telegram,
                        caption=caption,
                    )

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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot do Telegram iniciado e a escutar pedidos...")
    application.run_polling()


if __name__ == "__main__":
    main()
