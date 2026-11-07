from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
        case_sensitive=False,
        enable_decoding=False,
    )

    telegram_token: str | None = None
    telegram_webhook_base_url: str = ""
    telegram_webhook_secret: str = ""

    postgres_db: str = "ai_workspace"
    postgres_user: str = "ai_workspace"
    postgres_password: str = ""
    database_url: str = ""
    workflow_db_path: str = ""

    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-06-01"
    azure_openai_deployment: str = "gpt-4o"
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    tavily_api_key: str = ""
    cohere_api_key: str = ""
    ollama_base_url: str = ""
    gemini_image_model: str = "gemini-2.5-flash-image"
    gemini_video_model: str = "veo-3.1-generate-preview"

    model_availability_db_path: str = "/app/data/model_availability.db"
    request_budget_seconds: float = 90
    allowed_telegram_user_ids: list[int] = Field(default_factory=list)
    allowed_user_ids: list[int] = Field(default_factory=list)
    allow_public: bool = False
    admin_chat_id: str = ""

    require_human_approval: bool = True
    max_quality_iterations: int = 3
    max_steps: int = 5
    max_research_results: int = 5
    max_generated_content_chars: int = 100_000
    max_media_seconds: int = 60
    tool_timeout_seconds: int = 600
    max_artifact_bytes: int = 50 * 1024 * 1024
    max_user_operations: int = 100
    quota_window_seconds: int = 86_400
    research_cache_ttl_seconds: int = 3600
    research_cache_enabled: bool = True
    agent_allowed_roots: str = ""
    show_model_footer: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    @field_validator("allowed_telegram_user_ids", "allowed_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, value: object) -> list[int]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("Os IDs Telegram devem ser uma lista ou CSV.")
        try:
            return [int(str(item).strip()) for item in value if str(item).strip()]
        except ValueError as error:
            raise ValueError("ALLOWED_USER_IDS contém um ID inválido.") from error

    @property
    def telegram_user_ids(self) -> list[int]:
        return self.allowed_user_ids or self.allowed_telegram_user_ids

    def require_allowed_users(self) -> list[int]:
        ids = self.telegram_user_ids
        if not ids:
            raise RuntimeError(
                "ALLOWED_USER_IDS não está configurado. O bot recusa arrancar sem lista de utilizadores autorizados."
            )
        return ids



def get_settings() -> Settings:
    return Settings()
