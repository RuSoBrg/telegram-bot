import base64
import json
import logging
import os
import re
import time
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

from docx import Document
from langchain_core.tools import tool
from pptx import Presentation
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
GENERATED_DIR = BASE_DIR / "generated_files"
MAX_CONTENT_LENGTH = 100_000


def _output_path(filename: str, extension: str, prefix: str) -> Path:
    requested_name = Path(filename).stem if filename.strip() else prefix
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", requested_name).strip(".-_")
    if not safe_name:
        safe_name = prefix
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    return GENERATED_DIR / f"{safe_name}-{uuid4().hex[:8]}{extension}"


def _validate_content(content: str) -> str:
    if not content or not content.strip():
        raise ValueError("O conteúdo do ficheiro não pode estar vazio.")
    if len(content) > MAX_CONTENT_LENGTH:
        raise ValueError("O conteúdo excede o limite de 100000 caracteres.")
    return content.strip()


def _artifact(path: Path, mime_type: str) -> str:
    return json.dumps(
        {
            "tipo": "ficheiro_gerado",
            "filename": path.name,
            "path": str(path),
            "mime_type": mime_type,
        },
        ensure_ascii=False,
    )


def _tool_error(error: Exception) -> str:
    logger.exception("Falha ao gerar ficheiro")
    return json.dumps({"tipo": "erro_ficheiro", "erro": str(error)}, ensure_ascii=False)


def _require_env(*names: str) -> dict[str, str]:
    values = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"Configuração em falta: {', '.join(missing)}.")
    return values


def _google_genai_client():
    from google import genai

    api_key = _require_env("GOOGLE_API_KEY")["GOOGLE_API_KEY"]
    return genai.Client(api_key=api_key)


def _inline_bytes(response: Any) -> bytes:
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None) if inline_data else None
            if data:
                return data
    raise RuntimeError("O modelo não devolveu dados multimédia.")


def _lines(content: str) -> list[str]:
    return [line.strip() for line in content.splitlines() if line.strip()]


@tool
def gerar_documento_word(
    conteudo: str,
    titulo: str = "",
    nome_ficheiro: str = "documento",
) -> str:
    """Gera um ficheiro Word .docx a partir de texto estruturado e devolve os dados para o Telegram.
    Usa esta ferramenta quando o utilizador pedir um documento Word, DOCX, relatório ou plano
    para descarregar. O conteúdo deve incluir todo o texto final."""
    try:
        conteudo = _validate_content(conteudo)
        path = _output_path(nome_ficheiro, ".docx", "documento")
        document = Document()
        if titulo.strip():
            document.add_heading(titulo.strip(), level=0)
        for line in _lines(conteudo):
            if line.startswith("### "):
                document.add_heading(line[4:], level=2)
            elif line.startswith("## "):
                document.add_heading(line[3:], level=1)
            elif line.startswith("# "):
                document.add_heading(line[2:], level=0)
            elif line.startswith(("- ", "* ")):
                document.add_paragraph(line[2:], style="List Bullet")
            else:
                document.add_paragraph(line)
        document.save(path)
        return _artifact(
            path,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except (OSError, ValueError) as error:
        return _tool_error(error)


@tool
def gerar_apresentacao_powerpoint(
    conteudo: str,
    titulo: str = "",
    nome_ficheiro: str = "apresentacao",
) -> str:
    """Gera uma apresentação PowerPoint .pptx e devolve os dados para o Telegram.
    Usa esta ferramenta quando o utilizador pedir slides, PowerPoint ou PPTX.
    Separe os slides com linhas vazias; cada bloco torna-se um slide."""
    try:
        conteudo = _validate_content(conteudo)
        path = _output_path(nome_ficheiro, ".pptx", "apresentacao")
        presentation = Presentation()
        title = titulo.strip() or _lines(conteudo)[0][:120]
        title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
        title_slide.shapes.title.text = title
        title_slide.placeholders[1].text = "Gerado pelo assistente"

        blocks = [block.strip() for block in re.split(r"\n\s*\n", conteudo) if block.strip()]
        for block in blocks:
            lines = _lines(block)
            slide = presentation.slides.add_slide(presentation.slide_layouts[1])
            slide.shapes.title.text = lines[0][:120]
            body = slide.placeholders[1].text_frame
            body.clear()
            for index, line in enumerate(lines[1:] or lines):
                paragraph = body.paragraphs[0] if index == 0 else body.add_paragraph()
                paragraph.text = line.removeprefix("- ").removeprefix("* ")
                paragraph.level = 0
        presentation.save(path)
        return _artifact(
            path,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    except (OSError, ValueError) as error:
        return _tool_error(error)


@tool
def gerar_documento_pdf(
    conteudo: str,
    titulo: str = "",
    nome_ficheiro: str = "documento",
) -> str:
    """Gera um ficheiro PDF e devolve os dados para o Telegram.
    Usa esta ferramenta quando o utilizador pedir um PDF, relatório ou documento PDF."""
    try:
        conteudo = _validate_content(conteudo)
        path = _output_path(nome_ficheiro, ".pdf", "documento")
        styles = getSampleStyleSheet()
        story: list[Any] = []
        if titulo.strip():
            story.append(Paragraph(escape(titulo.strip()), styles["Title"]))
            story.append(Spacer(1, 8 * mm))
        for line in _lines(conteudo):
            if line.startswith("#"):
                story.append(Paragraph(escape(line.lstrip("# ").strip()), styles["Heading2"]))
            else:
                story.append(Paragraph(escape(line), styles["BodyText"]))
            story.append(Spacer(1, 3 * mm))
        SimpleDocTemplate(str(path), pagesize=A4).build(story)
        return _artifact(path, "application/pdf")
    except (OSError, ValueError) as error:
        return _tool_error(error)


@tool
def gerar_fotografia(
    prompt: str,
    nome_ficheiro: str = "imagem",
    modelo: str = "",
) -> str:
    """Gera uma imagem PNG a partir de uma descrição usando Google GenAI e prepara-a para envio no Telegram.
    Usa esta ferramenta para fotografias, ilustrações, thumbnails, posters ou imagens de marketing."""
    try:
        prompt = _validate_content(prompt)
        response = _google_genai_client().models.generate_content(
            model=modelo or os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
            contents=prompt,
            config={"response_modalities": ["TEXT", "IMAGE"]},
        )
        path = _output_path(nome_ficheiro, ".png", "imagem")
        path.write_bytes(_inline_bytes(response))
        return _artifact(path, "image/png")
    except (OSError, RuntimeError, ValueError) as error:
        return _tool_error(error)


@tool
def gerar_video(
    prompt: str,
    nome_ficheiro: str = "video",
    modelo: str = "",
    duracao_segundos: int = 8,
) -> str:
    """Gera um vídeo MP4 a partir de uma descrição usando Google Veo e prepara-o para envio no Telegram.
    Usa esta ferramenta para vídeos, reels, anúncios e clips. A geração pode demorar alguns minutos."""
    try:
        prompt = _validate_content(prompt)
        if not 1 <= duracao_segundos <= 8:
            raise ValueError("A duração do vídeo deve estar entre 1 e 8 segundos.")
        client = _google_genai_client()
        operation = client.models.generate_videos(
            model=modelo or os.getenv("GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview"),
            prompt=prompt,
            config={"duration_seconds": duracao_segundos, "number_of_videos": 1},
        )
        deadline = time.monotonic() + 600
        while not operation.done:
            if time.monotonic() >= deadline:
                raise RuntimeError("A geração do vídeo excedeu o tempo limite de 10 minutos.")
            time.sleep(10)
            operation = client.operations.get(operation)
        if getattr(operation, "error", None):
            raise RuntimeError(str(operation.error))
        generated = getattr(getattr(operation, "response", None), "generated_videos", None) or []
        if not generated:
            raise RuntimeError("O modelo não devolveu um vídeo.")
        video = getattr(generated[0], "video", None)
        video_bytes = getattr(video, "video_bytes", None) if video else None
        if not video_bytes:
            raise RuntimeError("A resposta do modelo não contém os bytes do vídeo.")
        path = _output_path(nome_ficheiro, ".mp4", "video")
        path.write_bytes(video_bytes)
        return _artifact(path, "video/mp4")
    except (OSError, RuntimeError, ValueError) as error:
        return _tool_error(error)


@tool
def gerar_audio(
    texto: str,
    nome_ficheiro: str = "audio",
    idioma: str = "pt-PT",
) -> str:
    """Gera áudio MP3 falado a partir de texto usando Google Cloud Text-to-Speech.
    Usa esta ferramenta para narrações, voz-off, anúncios e leitura de conteúdos.
    Requer GOOGLE_CLOUD_PROJECT e credenciais ADC configuradas."""
    try:
        texto = _validate_content(texto)
        _require_env("GOOGLE_CLOUD_PROJECT")
        from google.cloud import texttospeech

        client = texttospeech.TextToSpeechClient()
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=texto),
            voice=texttospeech.VoiceSelectionParams(
                language_code=idioma,
                name="pt-PT-Wavenet-A" if idioma == "pt-PT" else "",
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            ),
        )
        path = _output_path(nome_ficheiro, ".mp3", "audio")
        path.write_bytes(response.audio_content)
        return _artifact(path, "audio/mpeg")
    except (OSError, RuntimeError, ValueError) as error:
        return _tool_error(error)


@tool
def gerar_musica(
    prompt: str,
    nome_ficheiro: str = "musica",
    duracao_segundos: int = 30,
) -> str:
    """Gera música instrumental com Vertex AI Lyria e prepara o ficheiro WAV para envio.
    Requer GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION e credenciais ADC com acesso ao Lyria."""
    try:
        prompt = _validate_content(prompt)
        if not 1 <= duracao_segundos <= 60:
            raise ValueError("A duração da música deve estar entre 1 e 60 segundos.")
        config = _require_env("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default()
        session = AuthorizedSession(credentials)
        location = config["GOOGLE_CLOUD_LOCATION"]
        url = (
            f"https://{location}-aiplatform.googleapis.com/v1/"
            f"projects/{config['GOOGLE_CLOUD_PROJECT']}/locations/{location}/"
            f"publishers/google/models/{os.getenv('VERTEX_MUSIC_MODEL', 'lyria-002')}:predict"
        )
        response = session.post(
            url,
            json={
                "instances": [{"prompt": prompt}],
                "parameters": {"duration_seconds": duracao_segundos},
            },
            timeout=120,
        )
        response.raise_for_status()
        predictions = response.json().get("predictions", [])
        encoded_audio = predictions[0].get("audioContent") if predictions else None
        if not encoded_audio:
            raise RuntimeError("O modelo Lyria não devolveu áudio.")
        path = _output_path(nome_ficheiro, ".wav", "musica")
        path.write_bytes(base64.b64decode(encoded_audio))
        return _artifact(path, "audio/wav")
    except (OSError, RuntimeError, ValueError, IndexError) as error:
        return _tool_error(error)
