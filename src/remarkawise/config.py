"""Configuration management for Remarkawise."""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # reMarkable Cloud settings
    remarkable_device_token: Optional[str] = Field(
        default=None,
        description="Device token for reMarkable Cloud API authentication",
    )

    # Readwise settings
    readwise_access_token: Optional[str] = Field(
        default=None,
        description="Access token for Readwise API",
    )

    # Sync settings
    sync_interval_minutes: int = Field(
        default=60,
        description="Interval between automatic syncs in minutes",
    )
    sync_folders: Optional[str] = Field(
        default=None,
        description="Comma-separated list of folders to sync (empty = all)",
    )

    # Storage paths
    data_dir: Path = Field(
        default=Path.home() / ".remarkawise",
        description="Directory for storing application data",
    )

    @property
    def sync_state_path(self) -> Path:
        """Path to the sync state database."""
        return self.data_dir / "sync_state.db"

    @property
    def cache_dir(self) -> Path:
        """Path to the cache directory for downloaded files."""
        return self.data_dir / "cache"

    def ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


# Global settings instance
settings = Settings()
