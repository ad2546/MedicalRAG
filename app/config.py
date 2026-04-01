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

    # OCI Generative AI — native SDK auth (reads ~/.oci/config)
    oci_compartment_id: str = ""
    oci_region: str = "us-ashburn-1"
    oci_model_gen: str = "meta.llama-3.3-70b-instruct"   # pipeline model
    oci_model_chat: str = "meta.llama-3.3-70b-instruct"  # chat model
    oci_config_profile: str = "DEFAULT"
    oci_use_instance_principal: bool = False

    # Okuha Cloud
    okuha_api_key: str = ""
    okuha_service_name: str = "medical-rag"

    # Embedding
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_batch_size: int = 32
    embedding_dim: int = 384

    # Retrieval
    top_k_docs: int = 5
    reflection_confidence_threshold: float = 0.5
    max_reflection_rounds: int = 2

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    # Auth
    auth_secret_key: str = "change-me-in-production"
    auth_token_exp_minutes: int = 60 * 24
    auth_cookie_name: str = "access_token"
    auth_pbkdf2_iterations: int = 210000
    default_user_request_limit: int = 5


settings = Settings()
