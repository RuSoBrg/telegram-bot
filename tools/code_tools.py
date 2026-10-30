import os
from pathlib import Path
from langchain_core.tools import tool

BASE_DIR = Path(__file__).resolve().parent.parent
_configured_roots = os.environ.get("AGENT_ALLOWED_ROOTS", "")
ALLOWED_ROOTS = tuple(
    Path(root).expanduser().resolve()
    for root in _configured_roots.split(os.pathsep)
    if root.strip()
) or (BASE_DIR,)
BLOCKED_NAMES = {
    ".env",
    "key.properties",
    "google-services.json",
    "service-account.json",
}
BLOCKED_SUFFIXES = (".pem", ".p12", ".pfx", ".jks", ".keystore")

def _resolve_allowed_path(path_value: str, *, must_be_directory: bool = False) -> Path:
    candidate = Path(path_value).expanduser()
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    resolved = candidate.resolve()
    if not any(resolved == root or root in resolved.parents for root in ALLOWED_ROOTS):
        raise PermissionError("O caminho está fora das pastas autorizadas.")
    if resolved.name in BLOCKED_NAMES or resolved.suffix.lower() in BLOCKED_SUFFIXES:
        raise PermissionError("O acesso a este tipo de ficheiro está bloqueado.")
    if must_be_directory and not resolved.is_dir():
        raise NotADirectoryError("A pasta indicada não existe.")
    return resolved

@tool
def ler_ficheiro_codigo(caminho_ficheiro: str) -> str:
    """Lê o conteúdo completo de um ficheiro de código no disco (ex: Flutter/Dart, C#, YAML, JSON).
    Deves passar o caminho relativo ou absoluto do ficheiro."""
    try:
        caminho = _resolve_allowed_path(caminho_ficheiro)
        if not caminho.is_file():
            return f"Erro: O ficheiro '{caminho_ficheiro}' não foi encontrado no disco."
        
        # Limite de segurança para evitar ler ficheiros gigantescos ou binários
        tamanho = caminho.stat().st_size
        if tamanho > 500_000: # ~500KB
            return f"Erro: O ficheiro é demasiado grande ({tamanho} bytes) para ser lido de uma só vez."

        with caminho.open("r", encoding="utf-8", errors="ignore") as f:
            conteudo = f.read()
            
        return f"--- Conteúdo de {caminho_ficheiro} ---\n{conteudo}"
    except PermissionError:
        return "Erro: o acesso a este caminho não é permitido."
    except OSError:
        return "Erro: não foi possível ler o ficheiro indicado."

@tool
def listar_ficheiros_projeto(caminho_pasta: str = ".") -> str:
    """Lista todos os ficheiros de código (.dart, .yaml, .json, .gradle) dentro de uma pasta do projeto.
    Útil para explorar a estrutura do projeto antes de ler um ficheiro específico."""
    try:
        caminho_raiz = _resolve_allowed_path(caminho_pasta, must_be_directory=True)
        if not caminho_raiz.exists():
            return f"Erro: A pasta '{caminho_pasta}' não existe."
        
        ficheiros = []
        extensoes_permitidas = ('.dart', '.yaml', '.json', '.gradle', '.xml', '.properties')
        
        for root, dirs, files in os.walk(caminho_raiz):
            # Ignorar pastas de build e cache do Flutter/Git
            dirs[:] = [d for d in dirs if d not in ['.dart_tool', '.git', 'build', '.idea', 'android/.gradle']]
            for file in files:
                if file.endswith(extensoes_permitidas):
                    ficheiro = Path(root) / file
                    if ficheiro.name in BLOCKED_NAMES or ficheiro.suffix.lower() in BLOCKED_SUFFIXES:
                        continue
                    rel_path = os.path.relpath(ficheiro, caminho_raiz)
                    ficheiros.append(rel_path)
                    
        if not ficheiros:
            return f"Nenhum ficheiro de código encontrado na pasta '{caminho_pasta}'."
            
        return "Ficheiros encontrados no projeto:\n" + "\n".join(ficheiros[:50]) # Limita a 50 ficheiros
    except (PermissionError, NotADirectoryError):
        return "Erro: o acesso a esta pasta não é permitido."
    except OSError:
        return "Erro: não foi possível listar a pasta indicada."