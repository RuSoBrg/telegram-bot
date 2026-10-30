import json
from pathlib import Path

from telegram import InputFile, Update
from telegram.ext import ContextTypes


def artifact_from_tool_message(message) -> dict | None:
    try:
        artifact = json.loads(str(message.content))
    except (TypeError, json.JSONDecodeError):
        return None
    path = Path(artifact.get("path", "")).resolve()
    generated_dir = (Path(__file__).resolve().parent.parent / "generated_files").resolve()
    if (
        artifact.get("tipo") != "ficheiro_gerado"
        or generated_dir not in path.parents
        or not path.is_file()
    ):
        return None
    artifact["path"] = str(path)
    return artifact


async def publish_artifact(update: Update, artifact: dict) -> None:
    path = Path(artifact["path"])
    with path.open("rb") as file:
        telegram_file = InputFile(file, filename=artifact["filename"])
        caption = f"📎 {artifact['filename']}"
        mime_type = artifact.get("mime_type", "")
        if mime_type.startswith("image/"):
            await update.message.reply_photo(photo=telegram_file, caption=caption)
        elif mime_type.startswith("video/"):
            await update.message.reply_video(video=telegram_file, caption=caption)
        elif mime_type.startswith("audio/"):
            await update.message.reply_audio(audio=telegram_file, caption=caption)
        else:
            await update.message.reply_document(document=telegram_file, caption=caption)


async def publish_artifacts(update: Update, artifacts: list[dict]) -> None:
    for artifact in artifacts:
        await publish_artifact(update, artifact)
