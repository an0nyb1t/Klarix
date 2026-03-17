from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    app_name: str = "GitChat"
    debug: bool = False
    data_dir: str = "./data"

    # Database — uses aiosqlite driver for async support
    database_url: str = "sqlite+aiosqlite:///./data/gitchat.db"

    # GitHub
    github_token: str = ""

    # LLM defaults — user can override per-session via the settings UI
    llm_provider: str = "anthropic"
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-4-20250514"
    llm_base_url: str = ""

    # Rate limit — set to your plan's TPM limit; 0 = no tracking (local models)
    llm_rate_limit_tpm: int = 0

    # Embeddings
    embedding_model: str = "all-MiniLM-L6-v2"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
