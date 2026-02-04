"""Tests for configuration management."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from remarkawise.config import Settings


class TestSettingsDefaults:
    """Tests for default Settings values."""

    def test_default_sync_interval(self) -> None:
        """Test default sync interval is 60 minutes."""
        # Use _env_file=None to avoid loading .env
        settings = Settings(_env_file=None)
        assert settings.sync_interval_minutes == 60

    def test_default_data_dir(self) -> None:
        """Test default data directory is ~/.remarkawise."""
        settings = Settings(_env_file=None)
        assert settings.data_dir == Path.home() / ".remarkawise"

    def test_default_readwise_token_is_none(self) -> None:
        """Test readwise token is None by default (without .env file)."""
        # Clear env vars and don't load .env file
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)
            assert settings.readwise_access_token is None

    def test_default_anthropic_key_is_none(self) -> None:
        """Test anthropic key is None by default (without .env file)."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)
            assert settings.anthropic_api_key is None

    def test_default_cache_path_is_none(self) -> None:
        """Test remarkable cache path is None by default."""
        settings = Settings(_env_file=None)
        assert settings.remarkable_local_cache_path is None

    def test_default_sync_folders_is_none(self) -> None:
        """Test sync folders is None by default."""
        settings = Settings(_env_file=None)
        assert settings.sync_folders is None


class TestSettingsProperties:
    """Tests for Settings computed properties."""

    def test_sync_state_path(self) -> None:
        """Test sync_state_path property."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(data_dir=Path(tmpdir))
            assert settings.sync_state_path == Path(tmpdir) / "sync_state.db"

    def test_cache_dir(self) -> None:
        """Test cache_dir property."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(data_dir=Path(tmpdir))
            assert settings.cache_dir == Path(tmpdir) / "cache"


class TestSettingsEnsureDirectories:
    """Tests for ensure_directories method."""

    def test_creates_data_dir(self) -> None:
        """Test that ensure_directories creates data directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = Path(tmpdir) / "new_data_dir"
            settings = Settings(data_dir=data_path)

            assert not data_path.exists()
            settings.ensure_directories()
            assert data_path.exists()
            assert data_path.is_dir()

    def test_creates_cache_dir(self) -> None:
        """Test that ensure_directories creates cache directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = Path(tmpdir) / "data"
            settings = Settings(data_dir=data_path)

            settings.ensure_directories()

            cache_path = data_path / "cache"
            assert cache_path.exists()
            assert cache_path.is_dir()

    def test_idempotent(self) -> None:
        """Test that ensure_directories can be called multiple times."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(data_dir=Path(tmpdir))

            # Call multiple times should not raise
            settings.ensure_directories()
            settings.ensure_directories()

            assert settings.data_dir.exists()


class TestSettingsFromEnvironment:
    """Tests for loading Settings from environment variables."""

    def test_loads_readwise_token(self) -> None:
        """Test loading readwise token from environment."""
        with patch.dict(os.environ, {"READWISE_ACCESS_TOKEN": "test-token-123"}):
            settings = Settings()
            assert settings.readwise_access_token == "test-token-123"

    def test_loads_anthropic_key(self) -> None:
        """Test loading anthropic key from environment."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            settings = Settings()
            assert settings.anthropic_api_key == "sk-ant-test"

    def test_loads_sync_interval(self) -> None:
        """Test loading sync interval from environment."""
        with patch.dict(os.environ, {"SYNC_INTERVAL_MINUTES": "30"}):
            settings = Settings()
            assert settings.sync_interval_minutes == 30

    def test_loads_data_dir(self) -> None:
        """Test loading data directory from environment."""
        with patch.dict(os.environ, {"DATA_DIR": "/custom/data/path"}):
            settings = Settings()
            assert settings.data_dir == Path("/custom/data/path")

    def test_loads_remarkable_cache_path(self) -> None:
        """Test loading remarkable cache path from environment."""
        with patch.dict(os.environ, {"REMARKABLE_LOCAL_CACHE_PATH": "/custom/cache"}):
            settings = Settings()
            assert settings.remarkable_local_cache_path == Path("/custom/cache")

    def test_case_insensitive_env_vars(self) -> None:
        """Test that environment variables are case insensitive."""
        with patch.dict(os.environ, {"readwise_access_token": "lowercase-token"}):
            settings = Settings()
            assert settings.readwise_access_token == "lowercase-token"


class TestSettingsExtraIgnore:
    """Tests for extra='ignore' configuration."""

    def test_ignores_unknown_env_vars(self) -> None:
        """Test that unknown environment variables are ignored."""
        with patch.dict(os.environ, {"UNKNOWN_SETTING": "value", "ANOTHER_UNKNOWN": "123"}):
            # Should not raise validation error
            settings = Settings()
            assert not hasattr(settings, "unknown_setting")


class TestSettingsValidation:
    """Tests for Settings validation."""

    def test_sync_interval_must_be_int(self) -> None:
        """Test that sync interval accepts integer values."""
        settings = Settings(sync_interval_minutes=120)
        assert settings.sync_interval_minutes == 120

    def test_data_dir_accepts_string(self) -> None:
        """Test that data_dir accepts string and converts to Path."""
        settings = Settings(data_dir="/tmp/test")
        assert isinstance(settings.data_dir, Path)
        assert settings.data_dir == Path("/tmp/test")

    def test_cache_path_accepts_string(self) -> None:
        """Test that remarkable cache path accepts string."""
        settings = Settings(remarkable_local_cache_path="/custom/path")
        assert isinstance(settings.remarkable_local_cache_path, Path)


class TestSettingsFromEnvFile:
    """Tests for loading Settings from .env file."""

    def test_loads_from_env_file(self) -> None:
        """Test loading settings from .env file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "READWISE_ACCESS_TOKEN=file-token\n"
                "SYNC_INTERVAL_MINUTES=45\n"
            )

            # Change to temp directory to find .env
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                settings = Settings(_env_file=env_file)
                assert settings.readwise_access_token == "file-token"
                assert settings.sync_interval_minutes == 45
            finally:
                os.chdir(original_cwd)


class TestGlobalSettings:
    """Tests for global settings instance."""

    def test_global_settings_exists(self) -> None:
        """Test that global settings instance exists."""
        from remarkawise.config import settings

        assert settings is not None
        assert isinstance(settings, Settings)
