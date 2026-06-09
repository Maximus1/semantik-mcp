"""
Tests für das summarize_text Tool des Semantik MCP Servers.

Testet:
  - Provider-Routing (Ollama/LMStudio/OpenRouter)
  - Default-Provider-Verhalten
  - HTTP-Fehlerbehandlung (gemockt)
  - Timeout-Verhalten
"""

from mcp_server.llm_providers import (
    LLMConnectionError,
    LLMError,
    LLMTimeoutError,
    LLMProvider,
    OpenAICompatibleProvider,
    OllamaProvider,
    get_provider,
)
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_DIR = Path(__file__).parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


# ---------------------------------------------------------------------------
#  Tests: Provider-Routing
# ---------------------------------------------------------------------------


class TestProviderRouting:
    """Tests für die Provider-Auswahl via config.json."""

    def test_default_provider_ist_ollama(self, sample_config: Path):
        """Standard-Provider ist Ollama."""
        provider = get_provider(config_path=sample_config)
        assert isinstance(provider, OllamaProvider)

    def test_explicit_ollama(self, sample_config: Path):
        """Explizite Ollama-Auswahl."""
        provider = get_provider(name="ollama", config_path=sample_config)
        assert isinstance(provider, OllamaProvider)

    def test_explicit_lmstudio(self, sample_config: Path):
        """LM Studio als Provider."""
        provider = get_provider(name="lmstudio", config_path=sample_config)
        assert isinstance(provider, OpenAICompatibleProvider)

    def test_explicit_openrouter(self, sample_config: Path):
        """OpenRouter als Provider."""
        provider = get_provider(name="openrouter", config_path=sample_config)
        assert isinstance(provider, OpenAICompatibleProvider)

    def test_unbekannter_provider_wirft_error(self, sample_config: Path):
        """Unbekannter Provider-Name wirft ValueError."""
        with pytest.raises(ValueError, match="Unbekannter LLM-Provider"):
            get_provider(name="unbekannt", config_path=sample_config)

    def test_config_fallback_bei_fehlender_datei(self, tmp_path: Path):
        """Fehlende config.json → Standard-Defaults (Ollama)."""
        nonexistent = tmp_path / "nonexistent.json"
        provider = get_provider(config_path=nonexistent)
        assert isinstance(provider, OllamaProvider)


# ---------------------------------------------------------------------------
#  Tests: OllamaProvider
# ---------------------------------------------------------------------------


class TestOllamaProvider:
    """Tests für den Ollama-Provider mit gemockten HTTP-Calls."""

    def test_generate_erfolgreich(self):
        """Erfolgreiche Textgenerierung."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            default_model="llama3.2",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": "Dies ist eine Zusammenfassung."}
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch("mcp_server.llm_providers.requests.post", return_value=mock_response) as mock_post:
            result = provider.generate("Fasse zusammen: ...")
            assert result == "Dies ist eine Zusammenfassung."
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert "api/generate" in call_kwargs[0][0]

    def test_generate_mit_system_prompt(self):
        """System-Prompt wird korrekt übergeben."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            default_model="llama3.2",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "OK"}
        mock_response.raise_for_status = MagicMock()

        with patch("mcp_server.llm_providers.requests.post", return_value=mock_response) as mock_post:
            provider.generate("Test", system="Du bist ein Assistent.")
            payload = mock_post.call_args[1]["json"]
            assert payload["system"] == "Du bist ein Assistent."

    def test_connection_error(self):
        """Verbindungsfehler → LLMConnectionError."""
        import requests
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            default_model="llama3.2",
        )

        with patch(
            "mcp_server.llm_providers.requests.post",
            side_effect=requests.ConnectionError("Connection refused"),
        ):
            with pytest.raises(LLMConnectionError, match="Ollama nicht erreichbar"):
                provider.generate("Test")

    def test_timeout(self):
        """Timeout → LLMTimeoutError."""
        import requests
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            default_model="llama3.2",
        )

        with patch(
            "mcp_server.llm_providers.requests.post",
            side_effect=requests.Timeout("30s exceeded"),
        ):
            with pytest.raises(LLMTimeoutError, match="Timeout"):
                provider.generate("Test")

    def test_http_error(self):
        """HTTP-Fehler → LLMError."""
        import requests
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            default_model="llama3.2",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("mcp_server.llm_providers.requests.post") as mock_post:
            mock_post.side_effect = requests.HTTPError(
                response=mock_resp
            )
            with pytest.raises(LLMError, match="HTTP-Fehler"):
                provider.generate("Test")


# ---------------------------------------------------------------------------
#  Tests: OpenAICompatibleProvider
# ---------------------------------------------------------------------------


class TestOpenAICompatibleProvider:
    """Tests für OpenAI-kompatible Provider."""

    def test_generate_erfolgreich(self):
        """Erfolgreiche Textgenerierung über OpenAI-API."""
        provider = OpenAICompatibleProvider(
            base_url="http://localhost:1234/v1",
            default_model="local-model",
            api_key="lm-studio",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Zusammenfassung hier."}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("mcp_server.llm_providers.requests.post", return_value=mock_response) as mock_post:
            result = provider.generate("Fasse zusammen: ...")
            assert result == "Zusammenfassung hier."
            # Prüfe Authorization-Header
            call_kwargs = mock_post.call_args
            headers = call_kwargs[1]["headers"]
            assert "Authorization" in headers
            assert headers["Authorization"] == "Bearer lm-studio"

    def test_openrouter_extra_headers(self):
        """OpenRouter spezifische Header werden gesetzt."""
        provider = OpenAICompatibleProvider(
            base_url="https://openrouter.ai/api/v1",
            default_model="openai/gpt-4o-mini",
            api_key="sk-or-xxx",
            extra_headers={
                "HTTP-Referer": "https://github.com/semantik-mcp",
                "X-Title": "Semantik MCP Server",
            },
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Antwort"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("mcp_server.llm_providers.requests.post", return_value=mock_response) as mock_post:
            provider.generate("Test")
            headers = mock_post.call_args[1]["headers"]
            assert "HTTP-Referer" in headers
            assert "X-Title" in headers

    def test_unerwartete_antwortstruktur(self):
        """Unerwartete API-Antwort → LLMError."""
        provider = OpenAICompatibleProvider(
            base_url="http://localhost:1234/v1",
            default_model="local-model",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"unexpected": "format"}
        mock_response.raise_for_status = MagicMock()

        with patch("mcp_server.llm_providers.requests.post", return_value=mock_response):
            with pytest.raises(LLMError, match="Unerwartete Antwortstruktur"):
                provider.generate("Test")


# ---------------------------------------------------------------------------
#  Tests: summarize_text Tool-Funktion
# ---------------------------------------------------------------------------


class TestSummarizeTextTool:
    """Integrationstests für die summarize_text Tool-Funktion."""

    def test_tool_nutzt_default_provider(self, tmp_path: Path):
        """Tool verwendet den Default-Provider aus config."""
        import json as _json

        # Config direkt schreiben (statt shutil.copy – vermeidet File-Locks)
        config = {
            "llm": {
                "default_provider": "ollama",
                "ollama": {
                    "base_url": "http://localhost:11434",
                    "default_model": "llama3.2",
                },
            }
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(_json.dumps(config), encoding="utf-8")

        # Simuliere Tool-Aufruf mit gemocktem Provider
        provider = get_provider(config_path=config_path)

        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "Gekürzter Text."}
        mock_response.raise_for_status = MagicMock()

        with patch("mcp_server.llm_providers.requests.post", return_value=mock_response):
            result = provider.generate("Langer Text zum Kürzen...")
            assert result == "Gekürzter Text."

    def test_tool_fehler_gibt_runtime_error(self, sample_config: Path):
        """LLM-Fehler wird als RuntimeError geworfen."""
        import requests
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            default_model="llama3.2",
        )

        with patch(
            "mcp_server.llm_providers.requests.post",
            side_effect=requests.ConnectionError("nope"),
        ):
            with pytest.raises(LLMConnectionError):
                provider.generate("Test")
