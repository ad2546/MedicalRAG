from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" silently drops any .env keys not declared here
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:password@localhost:5432/medicalrag"
    database_url_sync: str = "postgresql://postgres:password@localhost:5432/medicalrag"

    # LLM Provider — "groq" (default) or "oci"
    llm_provider: str = "groq"

    # Groq (default LLM provider — fast, free tier, OpenAI-compatible)
    groq_api_key: str = ""
    groq_model_gen: str = "llama-3.3-70b-versatile"   # pipeline / diagnosis model
    groq_model_chat: str = "llama-3.3-70b-versatile"  # interactive chat model

    # OCI Generative AI — optional fallback (set LLM_PROVIDER=oci to use)
    oci_compartment_id: str = ""
    oci_region: str = "us-ashburn-1"
    oci_model_gen: str = "meta.llama-3.3-70b-instruct"
    oci_model_chat: str = "meta.llama-3.3-70b-instruct"
    oci_config_profile: str = "DEFAULT"
    oci_use_instance_principal: bool = False

    # Okahu Cloud (monocle-apptrace)
    okahu_api_key: str = ""
    okahu_service_name: str = "medicalchatbot_ni9wbg"

    # Embedding
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_batch_size: int = 32
    embedding_dim: int = 384

    # Retrieval
    top_k_docs: int = 5
    reflection_confidence_threshold: float = 0.5
    max_reflection_rounds: int = 1

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    # Workflow endpoint (external Bearer token auth for Okahu Cloud / n8n)
    workflow_api_key: str = ""

    # Auth
    auth_secret_key: str = "change-me-in-production"
    auth_token_exp_minutes: int = 60 * 24
    auth_cookie_name: str = "access_token"
    auth_pbkdf2_iterations: int = 210000
    default_user_request_limit: int = 5
    # Hard cap across all users — resets at UTC midnight
    global_daily_request_limit: int = 200

    @model_validator(mode="after")
    def fix_database_urls(self) -> "Settings":
        """Normalise DB URLs — Railway provides postgres:// which asyncpg rejects."""
        url = self.database_url
        if url.startswith("postgres://"):
            self.database_url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            self.database_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

        sync_url = self.database_url_sync
        if sync_url.startswith("postgres://"):
            self.database_url_sync = sync_url.replace("postgres://", "postgresql://", 1)
        return self


settings = Settings()
