"""Runtime configuration. All paths derive from GENIE_HOME (default ~/.dataset-genie)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GENIE_", extra="ignore")

    # Read from GENIE_HOME (documented name); GENIE_GENIE_HOME also accepted for prefix consistency.
    genie_home: Path = Field(
        default_factory=lambda: Path.home() / ".dataset-genie",
        validation_alias=AliasChoices("GENIE_HOME", "GENIE_GENIE_HOME"),
    )
    port: int = 8765
    default_budget_cap: float = 15.0
    default_stop_at_pct: int = 90
    default_concurrency: int = 8
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    app_referer: str = "http://localhost:8765"
    app_title: str = "Dataset Genie"
    catalogue_ttl_hours: int = 24

    @property
    def db_path(self) -> Path:
        return self.genie_home / "genie.db"

    @property
    def exports_dir(self) -> Path:
        return self.genie_home / "exports"

    def ensure_dirs(self) -> None:
        self.genie_home.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


def reset_settings_cache() -> None:
    """Tests call this after changing GENIE_HOME."""
    get_settings.cache_clear()
