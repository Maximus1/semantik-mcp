"""
Gemeinsame Test-Fixtures für alle Semantik MCP Server Tests.

Bietet temp-basierte Konfigurationsdateien, damit Tests keine
globalen Daten verschmutzen.
"""

import json
import sys
import os
from pathlib import Path
from typing import Any, Dict

import pytest

# Projektverzeichnis zum Python-Path hinzufügen
PROJECT_DIR = Path(__file__).parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


# ---------------------------------------------------------------------------
#  Fixtures: Temporäre Konfigurationsdateien
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Erstellt ein temporäres Verzeichnis mit leeren Konfigurationsdateien."""
    return tmp_path


@pytest.fixture
def sample_mappings(tmp_path: Path) -> Path:
    """Erstellt eine mappings.json mit Beispieldaten im tmp-Verzeichnis.

    Format (konsistent mit der echten mappings.json): kanonischer Name →
    Liste von Varianten.
    """
    mappings = {
        "KI": ["KI", "Künstliche Intelligenz", "künstliche Intelligenz"],
        "ML": ["ML", "Maschinelles Lernen", "maschinelles Lernen"],
        "API": ["API", "Programmierschnittstelle", "Schnittstelle"],
        "Deployment": ["Deployment", "Bereitstellung", "deployment"],
    }
    path = tmp_path / "mappings.json"
    path.write_text(
        json.dumps(
            mappings,
            ensure_ascii=False,
            indent=2),
        encoding="utf-8")
    return path


@pytest.fixture
def sample_protected_terms(tmp_path: Path) -> Path:
    """Erstellt eine protected_terms.json mit Beispiel-Terms."""
    data = {
        "terms": [
            "__init__",
            "__name__",
            "self",
            "cls",
            "True",
            "False",
            "None",
            "print",
            "len",
            "range",
        ]
    }
    path = tmp_path / "protected_terms.json"
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2),
        encoding="utf-8")
    return path


@pytest.fixture
def sample_config(tmp_path: Path) -> Path:
    """Erstellt eine config.json mit Beispiel-Konfiguration."""
    config = {
        "llm": {
            "default_provider": "ollama",
            "ollama": {
                "base_url": "http://localhost:11434",
                "default_model": "llama3.2",
            },
            "lmstudio": {
                "base_url": "http://localhost:1234/v1",
                "default_model": "local-model",
                "api_key": "lm-studio",
            },
            "openrouter": {
                "base_url": "https://openrouter.ai/api/v1",
                "default_model": "openai/gpt-4o-mini",
                "api_key_env": "OPENROUTER_API_KEY",
            },
        }
    }
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            config,
            ensure_ascii=False,
            indent=2),
        encoding="utf-8")
    return path


@pytest.fixture
def sample_python_code() -> str:
    """Beispiel-Python-Code zum Testen der Code-Optimierung."""
    return '''\
"""Dies ist ein Modul-Docstring."""


class MeineKlasse:
    """Eine Beispielklasse."""

    def __init__(self, name):
        """Initialisiert die Klasse."""
        self.name = name

    def gruessen(self, sprache="de"):
        """Gibt eine Begrüßung zurück."""
        if sprache == "de":
            return f"Hallo, {self.name}!"  # Deutsche Begrüßung
        else:
            return f"Hello, {self.name}!"


def berechne_summe(zahlen):
    """Berechnet die Summe einer Liste."""
    # Summiere alle Zahlen
    result = 0
    for z in zahlen:
        result += z
    return result


# Hauptprogramm
if __name__ == "__main__":
    k = MeineKlasse("Welt")
    print(k.gruessen())
'''


@pytest.fixture
def sample_python_code_expected_clean() -> str:
    """Erwarteter Output nach Code-Optimierung (Docstrings/Kommentare entfernt)."""
    return '''\



class MeineKlasse:

    def __init__(self, name):
        self.name = name

    def gruessen(self, sprache="de"):
        if sprache == "de":
            return f"Hallo, {self.name}!"
        else:
            return f"Hello, {self.name}!"


def berechne_summe(zahlen):
    result = 0
    for z in zahlen:
        result += z
    return result



if __name__ == "__main__":
    k = MeineKlasse("Welt")
    print(k.gruessen())
'''
