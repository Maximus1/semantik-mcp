"""
Tests für llm_providers.py des Semantik MCP Servers.

Testet:
  - Konfiguration laden
  - Provider-Factory
  - API-Key-Resolution aus Environment
  - Health-Check
"""

from mcp_server.llm_providers import (
    LLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    load_config,
    get_provider,
)
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_DIR = Path(__file__).parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


# ---------------------------------------------------------------------------
#  Tests: load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """Tests für die Konfiguration."""

    def test_lädt_gültige_config(self, sample_config: Path):
        """Gültige config.json wird korrekt geladen."""
        config = load_config(sample_config)
        assert "llm" in config
        assert config["llm"]["default_provider"] == "ollama"
        assert "ollama" in config["llm"]
        assert "lmstudio" in config["llm"]
        assert "openrouter" in config["llm"]

    def test_fehlende_datei_gibt_leeres_dict(self, tmp_path: Path):
        """Fehlende config → leeres Dict (kein Crash)."""
        nonexistent = tmp_path / "nonexistent.json"
        config = load_config(nonexistent)
        assert config == {}

    def test_ungültiges_json_gibt_leeres_dict(self, tmp_path: Path):
        """Ungültiges JSON → leeres Dict."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{broken json", encoding="utf-8")
        config = load_config(bad_file)
        assert config == {}


# ---------------------------------------------------------------------------
#  Tests: Provider-Factory
# ---------------------------------------------------------------------------


class TestGetProvider:
    """Tests für die Provider-Factory-Funktion."""

    def test_ollama_provider(self, sample_config: Path):
        """Ollama-Provider wird korrekt instanziiert."""
        provider = get_provider(name="ollama", config_path=sample_config)
        assert isinstance(provider, OllamaProvider)
        assert provider.base_url == "http://localhost:11434"
        assert provider.default_model == "llama3.2"

    def test_lmstudio_provider(self, sample_config: Path):
        """LMStudio-Provider wird korrekt instanziiert."""
        provider = get_provider(name="lmstudio", config_path=sample_config)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.base_url == "http://localhost:1234/v1"
        assert provider.api_key == "lm-studio"

    def test_openrouter_provider(self, sample_config: Path):
        """OpenRouter-Provider wird korrekt instanziiert."""
        provider = get_provider(name="openrouter", config_path=sample_config)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert "openrouter.ai" in provider.base_url

    def test_default_provider_aus_config(self, sample_config: Path):
        """Ohne expliziten Namen → Default aus config.json."""
        provider = get_provider(config_path=sample_config)
        assert isinstance(provider, OllamaProvider)

    def test_unknown_provider_raises(self, sample_config: Path):
        """Unbekannter Name → ValueError."""
        with pytest.raises(ValueError, match="Unbekannter LLM-Provider"):
            get_provider(name="gpt-ultra-mega-9000", config_path=sample_config)


# ---------------------------------------------------------------------------
#  Tests: API-Key-Resolution
# ---------------------------------------------------------------------------


class TestApiKeyResolution:
    """Tests für die API-Key-Auflösung."""

    def test_api_key_direkt_aus_config(self, sample_config: Path):
        """API-Key wird direkt aus Config gelesen."""
        provider = get_provider(name="lmstudio", config_path=sample_config)
        assert provider.api_key == "lm-studio"

    def test_api_key_env_variable(self, tmp_path: Path):
        """API-Key wird aus Environment-Variable gelesen."""
        config = {
            "llm": {
                "default_provider": "openrouter",
                "openrouter": {
                    "base_url": "https://openrouter.ai/api/v1",
                    "default_model": "openai/gpt-4o-mini",
                    "api_key_env": "TEST_OPENROUTER_KEY",
                },
            }
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        with patch.dict(os.environ, {"TEST_OPENROUTER_KEY": "sk-test-123"}):
            provider = get_provider(config_path=config_path)
            assert provider.api_key == "sk-test-123"

    def test_fehlende_env_variable_gibt_none(self, tmp_path: Path):
        """Fehlende Env-Variable → api_key=None."""
        config = {
            "llm": {
                "default_provider": "openrouter",
                "openrouter": {
                    "base_url": "https://openrouter.ai/api/v1",
                    "default_model": "openai/gpt-4o-mini",
                    "api_key_env": "NONEXISTENT_KEY",
                },
            }
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        # Sicherstellen, dass die Variable nicht existiert
        env = os.environ.copy()
        env.pop("NONEXISTENT_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            provider = get_provider(config_path=config_path)
            assert provider.api_key is None


# ---------------------------------------------------------------------------
#  Tests: Health-Check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests für den Health-Check."""

    def test_health_check_erfolgreich(self):
        """Erfolgreiche Anfrage → True."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            default_model="llama3.2",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "ok"}
        mock_response.raise_for_status = MagicMock()

        with patch("mcp_server.llm_providers.requests.post", return_value=mock_response):
            assert provider.health_check() is True

    def test_health_check_fehler(self):
        """Fehlerhafte Anfrage → False."""
        import requests
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            default_model="llama3.2",
        )

        with patch(
            "mcp_server.llm_providers.requests.post",
            side_effect=requests.ConnectionError("nope"),
        ):
            assert provider.health_check() is False
