from pathlib import Path

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

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


settings = Settings()
