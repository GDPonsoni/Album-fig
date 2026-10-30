from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://album:album@localhost:5433/album"
    base_url: str = "http://localhost:5173"
    media_dir: Path = BACKEND_DIR / "media"
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"
    session_days: int = 30
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "album@example.com"
    brevo_api_key: str = ""  # com a chave, o e-mail sai pela API; o plano gratuito do Render bloqueia SMTP
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_public_url: str = ""  # endereço público do bucket, sem / no final

    @field_validator("database_url")
    @classmethod
    def use_psycopg(cls, value: str) -> str:
        # Render e Neon entregam postgresql://, que o SQLAlchemy levaria ao driver psycopg2.
        for prefix in ("postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value.removeprefix(prefix)
        return value

    @property
    def r2_enabled(self) -> bool:
        return bool(self.r2_bucket)

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


settings = Settings()
