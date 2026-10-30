from telegram import Update

async def publish_artifact(update: Update, artifact: dict) -> None:
    await update.message.reply_text(
        f"📎 {artifact['filename']}\n{artifact['uri']}"
    )


async def publish_artifacts(update: Update, artifacts: list[dict]) -> None:
    for artifact in artifacts:
        await publish_artifact(update, artifact)
