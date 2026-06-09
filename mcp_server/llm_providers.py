"""
LLM-Provider-Abstraktion für den Semantik MCP Server.

Unterstützt drei Provider:
  - Ollama (lokal, Standard auf Port 11434)
  - LM Studio (lokal, OpenAI-kompatibel auf Port 1234)
  - OpenRouter (Cloud, OpenAI-kompatibel, API-Key erforderlich)

Konfiguration über config.json im Projektverzeichnis.
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from requests import Response
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Secret Management
# ---------------------------------------------------------------------------


class SecretManager:
    """Handles encryption and decryption of sensitive keys using Fernet."""

    def __init__(self, key_file: Path = Path("secret.key")):
        self.key_file = key_file
        self._fernet: Optional[Fernet] = None

    def _load_or_generate_key(self) -> bytes:
        """Loads the encryption key from environment variable, file or generates a new one."""
        # Priorität 1: Umgebungsvariable (Sicherster Weg)
        env_key = os.environ.get("SEMANTIK_MASTER_KEY")
        if env_key:
            try:
                return env_key.encode()
            except Exception as exc:
                logger.warning(
                    "SEMANTIK_MASTER_KEY Umgebungsvariable ungültig.", exc_info=exc)

        # Priorität 2: Lokale Datei
        if self.key_file.exists():
            try:
                return self.key_file.read_bytes()
            except OSError:
                logger.error(
                    "Konnte Schlüsseldatei lesen. Generiere neuen Schlüssel.")

        # Fallback: Generiere neuen Schlüssel
        key = Fernet.generate_key()
        try:
            self.key_file.write_bytes(key)
        except OSError as exc:
            logger.error("Konnte Schlüsseldatei speichern: %s", exc)
        return key

    def __get_fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(self._load_or_generate_key())
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        """Encrypts a string and returns it as a base64 encoded string."""
        if not plaintext:
            return ""
        return self.__get_fernet().encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypts a base64 encoded string."""
        if not ciphertext:
            return ""
        try:
            return self.__get_fernet().decrypt(ciphertext.encode()).decode()
        except Exception as exc:
            logger.error("Entschlüsselung fehlgeschlagen: %s", exc)
            return ""


# Global instance for the application
secrets = SecretManager()

# ---------------------------------------------------------------------------
#  Ausnahmen
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Basis-Ausnahme für alle LLM-Provider-Fehler."""


class LLMConnectionError(LLMError):
    """Verbindung zum Provider nicht möglich."""


class LLMTimeoutError(LLMError):
    """Anfrage hat das Timeout überschritten."""

# ---------------------------------------------------------------------------
#  Abstrakte Basis-Klasse
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Abstrakte Basis für LLM-Provider."""

    def __init__(
        self,
        base_url: str,
        default_model: str,
        api_key: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.api_key = api_key

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        generation_options: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generiere eine Textantwort für den gegebenen Prompt.

        Args:
            prompt: Der Eingabe-Text.
            system: System-Prompt (optional).
            model: Modellname (optional, überschreibt Default).
            generation_options: Optionen wie ``max_tokens`` und ``temperature``.
        """

    def health_check(self) -> bool:
        """Prüft, ob der Provider erreichbar ist."""
        try:
            self.generate(
                "Antworte mit 'ok'.",
                generation_options={
                    "max_tokens": 5})
            return True
        except LLMError:
            return False


# ---------------------------------------------------------------------------
#  Ollama-Provider (eigenes API-Format)
# ---------------------------------------------------------------------------

class OllamaProvider(LLMProvider):
    """
    Provider für Ollama (lokal).

    API-Referenz: POST http://localhost:11434/api/generate
    """

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        generation_options: Optional[Dict[str, Any]] = None,
    ) -> str:
        opts = generation_options or {}
        url = f"{self.base_url}/api/generate"
        payload: Dict[str, Any] = {
            "model": model or self.default_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": opts.get("temperature", 0.3),
                "num_predict": opts.get("max_tokens", 500),
            },
        }
        if system:
            payload["system"] = system

        try:
            # Explicit type hint for Pylance/static analysis
            resp = requests.post(
                url, json=payload, timeout=30)  # type: Response
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "")
        except requests.ConnectionError as exc:
            raise LLMConnectionError(
                f"Ollama nicht erreichbar unter {self.base_url}. "
                f"Ist Ollama gestartet (Standard-Port 11434)? "
                f"Details: {exc}"
            ) from exc
        except requests.Timeout as exc:
            raise LLMTimeoutError(
                "Ollama-Anfrage nach 30 Sekunden Timeout abgebrochen."
            ) from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unbekannt"
            body = exc.response.text[:300] if exc.response is not None else ""
            raise LLMError(f"Ollama HTTP-Fehler {status}: {body}") from exc
        except (KeyError, json.JSONDecodeError) as exc:
            raise LLMError(
                f"Unerwartete Antwort von Ollama: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
#  OpenAI-kompatible Provider (LM Studio, OpenRouter, …)
# ---------------------------------------------------------------------------

class OpenAICompatibleProvider(LLMProvider):
    """
    Provider für OpenAI-kompatible APIs.

    Funktioniert mit:
      - LM Studio (http://localhost:1234/v1)
      - OpenRouter (https://openrouter.ai/api/v1)
      - Jeder anderen OpenAI-kompatiblen API
    """

    def __init__(
        self,
        base_url: str,
        default_model: str,
        api_key: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(base_url, default_model, api_key)
        self.extra_headers = extra_headers or {}

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        generation_options: Optional[Dict[str, Any]] = None,
    ) -> str:
        opts = generation_options or {}
        url = f"{self.base_url}/chat/completions"

        messages: list[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "max_tokens": opts.get("max_tokens", 500),
            "temperature": opts.get("temperature", 0.3),
        }

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)

        try:
            resp: Response = requests.post(
                url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except requests.ConnectionError as exc:
            raise LLMConnectionError(
                f"LLM-Provider nicht erreichbar unter {self.base_url}. "
                f"Ist der Server gestartet? Details: {exc}"
            ) from exc
        except requests.Timeout as exc:
            raise LLMTimeoutError(
                "LLM-Anfrage nach 30 Sekunden Timeout abgebrochen."
            ) from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unbekannt"
            body = exc.response.text[:300] if exc.response is not None else ""
            raise LLMError(f"LLM HTTP-Fehler {status}: {body}") from exc
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise LLMError(
                f"Unerwartete Antwortstruktur vom LLM-Provider: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
#  Konfiguration laden
# ---------------------------------------------------------------------------

def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Lädt die config.json aus dem Projektverzeichnis.

    Args:
        config_path: Alternativer Pfad. Standard: <dieses_modul>/config.json
    """
    if config_path is None:
        config_path = Path(__file__).parent / "config.json"

    if not config_path.exists():
        logger.warning(
            "Config-Datei nicht gefunden: %s – verwende Defaults.",
            config_path)
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        logger.error("Ungültiges JSON in %s: %s", config_path, exc)
        return {}


def get_provider(
    name: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> LLMProvider:
    """
    Factory-Funktion: Gibt einen LLMProvider zurück.

    Args:
        name: Provider-Name ("ollama", "lmstudio", "openrouter").
              Wenn None, wird der Default aus config.json verwendet.
        config_path: Alternativer Pfad zur config.json.

    Returns:
        Instanz eines LLMProvider.

    Raises:
        ValueError: Bei unbekanntem Provider-Namen.
    """
    config = load_config(config_path)
    llm_config = config.get("llm", {})

    provider_name = name or llm_config.get("default_provider", "ollama")
    provider_cfg = llm_config.get(provider_name, {})

    base_url: str = provider_cfg.get("base_url", "http://localhost:11434")
    default_model: str = provider_cfg.get("default_model", "llama3.2")

    # API-Key: Priorität auf verschlüsseltem Wert in Config
    api_key_raw: Optional[str] = provider_cfg.get("api_key")
    api_key: Optional[str] = None

    if api_key_raw:
        # Versuche zu entschlüsseln. Wenn die Entschlüsselung fehlschlägt (z. B. weil der Schlüssel nicht verschlüsselt ist),
        # verwende den rohen Wert als Fallback.
        decrypted = secrets.decrypt(api_key_raw)
        api_key = decrypted if decrypted else api_key_raw

    api_key_env: Optional[str] = provider_cfg.get("api_key_env")
    if api_key_env and not api_key:
        api_key = os.environ.get(api_key_env)

    if provider_name == "ollama":
        return OllamaProvider(
            base_url=base_url,
            default_model=default_model,
            api_key=api_key,
        )

    if provider_name in ("lmstudio", "openrouter"):
        extra_headers: Dict[str, str] = {}
        if provider_name == "openrouter":
            extra_headers["HTTP-Referer"] = "https://github.com/semantik-mcp"
            extra_headers["X-Title"] = "Semantik MCP Server"
        return OpenAICompatibleProvider(
            base_url=base_url,
            default_model=default_model,
            api_key=api_key,
            extra_headers=extra_headers,
        )

    raise ValueError(
        f"Unbekannter LLM-Provider: '{provider_name}'. "
        f"Verfügbar: ollama, lmstudio, openrouter"
    )
