"""
Tests für SSRF-Schutz in tool_configure (mcp_server/main.py).

Testet:
  - Lokale URLs sind erlaubt (localhost, 127.0.0.1)
  - cloud-URLs sind erlaubt (openrouter.ai)
  - Unbekannte Hosts werden abgelehnt
  - Ungültige Schemas werden abgelehnt
  - Ungültige Zeichen werden abgelehnt
  - Zu lange URLs werden abgelehnt
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Projektverzeichnis zum Pfad hinzufügen
PROJECT_DIR = Path(__file__).parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from mcp_server.main import tool_configure  # noqa: E402


# ---------------------------------------------------------------------------
#  Tests: Erlaubte URLs
# ---------------------------------------------------------------------------


class TestSsrfAllowedUrls:
    """Tests für erlaubte URLs."""

    def test_valid_local_url_localhost(self):
        """Erlaubte localhost-URL."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="llama3.2",
            api_key="",
            base_url="http://localhost:11434",
        ))
        assert result["status"] == "ok"
        assert result["base_url"] == "http://localhost:11434"

    def test_valid_local_url_127(self):
        """Erlaubte 127.0.0.1-URL."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="llama3.2",
            api_key="",
            base_url="http://127.0.0.1:11434",
        ))
        assert result["status"] == "ok"

    def test_valid_openrouter_url(self):
        """Erlaubte OpenRouter-URL."""
        result = json.loads(tool_configure(
            provider="openrouter",
            model="openai/gpt-4o-mini",
            api_key="sk-test",
            base_url="https://openrouter.ai/api/v1",
        ))
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
#  Tests: Abgelehnte URLs (SSRF-Schutz)
# ---------------------------------------------------------------------------


class TestSsrfBlockedUrls:
    """Tests für abgelehnte URLs (SSRF-Schutz)."""

    def test_invalid_scheme(self):
        """Nicht erlaubtes Schema (ftp://) wird abgelehnt."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="llama3.2",
            api_key="",
            base_url="ftp://malicious.com",
        ))
        assert result["status"] == "error"
        assert "URL muss mit http:// oder https:// beginnen" in result["message"]

    def test_unknown_host(self):
        """Unbekannter/externer Host wird abgelehnt."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="llama3.2",
            api_key="",
            base_url="http://malicious.com/api",
        ))
        assert result["status"] == "error"
        assert "nicht erlaubten Host" in result["message"]

    def test_internal_ip_blocked(self):
        """Interne IPs (10.x.x.x) werden abgelehnt."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="llama3.2",
            api_key="",
            base_url="http://10.0.0.1:11434",
        ))
        assert result["status"] == "error"
        assert "nicht erlaubten Host" in result["message"]

    def test_localhost_subdomain_blocked(self):
        """localhost mit Subdomain (nicht exakt 'localhost') wird abgelehnt."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="llama3.2",
            api_key="",
            base_url="http://evil.localhost:11434",
        ))
        # evil.localhost ist nicht in der Whitelist
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
#  Tests: URL-Validierung
# ---------------------------------------------------------------------------


class TestUrlValidation:
    """Tests für URL-Validierung."""

    def test_url_zu_lang(self):
        """Übergroße URL wird abgelehnt."""
        long_url = "http://localhost:11434/" + "a" * 300
        result = json.loads(tool_configure(
            provider="ollama",
            model="llama3.2",
            api_key="",
            base_url=long_url,
        ))
        assert result["status"] == "error"

    def test_url_mit_ungueltigen_zeichen(self):
        """URL mit ungültigen Zeichen wird abgelehnt."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="llama3.2",
            api_key="",
            base_url="http://localhost:11434/<script>",
        ))
        # <> sind nicht im erlaubten Zeichensatz
        assert result["status"] == "error"

    def test_leere_base_url_erlaubt(self):
        """Leere base_url nutzt den Provider-Default."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="llama3.2",
            api_key="",
            base_url="",
        ))
        assert result["status"] == "ok"
        assert result["base_url"] == "http://localhost:11434"


# ---------------------------------------------------------------------------
#  Tests: Provider-Validierung
# ---------------------------------------------------------------------------


class TestProviderValidation:
    """Tests für Provider-Validierung."""

    def test_invalid_provider(self):
        """Unbekannter Provider wird abgelehnt."""
        result = json.loads(tool_configure(
            provider="evil-provider",
            model="gpt-4",
            api_key="",
            base_url="",
        ))
        assert result["status"] == "error"
        assert "Ungültiger Provider" in result["message"]

    def test_valid_provider_ollama(self):
        """Ollama ist erlaubt."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="llama3.2",
            api_key="",
            base_url="",
        ))
        assert result["status"] == "ok"
        assert result["provider"] == "ollama"

    def test_valid_provider_lmstudio(self):
        """LM Studio ist erlaubt."""
        result = json.loads(tool_configure(
            provider="lmstudio",
            model="local-model",
            api_key="",
            base_url="",
        ))
        assert result["status"] == "ok"

    def test_valid_provider_openrouter(self):
        """OpenRouter ist erlaubt."""
        result = json.loads(tool_configure(
            provider="openrouter",
            model="openai/gpt-4o-mini",
            api_key="sk-test",
            base_url="",
        ))
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
#  Tests: Modell- und API-Key-Validierung
# ---------------------------------------------------------------------------


class TestModelAndKeyValidation:
    """Tests für Modell- und API-Key-Validierung."""

    def test_empty_model_rejected(self):
        """Leeres Modell wird abgelehnt."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="",
            api_key="",
            base_url="",
        ))
        assert result["status"] == "error"
        assert "Modellname" in result["message"]

    def test_too_long_model_rejected(self):
        """Modellname > 100 Zeichen wird abgelehnt."""
        result = json.loads(tool_configure(
            provider="ollama",
            model="x" * 200,
            api_key="",
            base_url="",
        ))
        assert result["status"] == "error"

    def test_too_long_api_key_rejected(self):
        """API-Key > 1024 Zeichen wird abgelehnt."""
        result = json.loads(tool_configure(
            provider="openrouter",
            model="openai/gpt-4o-mini",
            api_key="x" * 2000,
            base_url="",
        ))
        assert result["status"] == "error"
        assert "API-Key" in result["message"]
