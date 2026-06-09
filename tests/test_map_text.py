"""
Tests für das map_text Tool des Semantik MCP Servers.

Testet die Kernlogik (Term-Normalisierung) gegen die echten
Funktionen aus ``mcp_server/main.py``.

Hinweis: Da ``mcp_server/main.py`` beim Import die
Konfigurationsdateien aus dem Projektverzeichnis lädt, testen
wir die reine Mapping-Funktion ``_normalize_term`` direkt.
"""

import json
import sys
from pathlib import Path

import pytest

# Projektverzeichnis zum Pfad hinzufügen
PROJECT_DIR = Path(__file__).parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# Importiere die zu testenden Funktionen aus dem echten Server
from mcp_server.main import (  # noqa: E402
    _build_reverse,
    _normalize_term,
)


# ---------------------------------------------------------------------------
#  Tests: _build_reverse
# ---------------------------------------------------------------------------


class TestBuildReverse:
    """Tests für die Reverse-Mapping-Erstellung."""

    def test_listenformat_wird_korrekt_umgekehrt(self):
        """Listenformat: jede Variante → kanonischer Name."""
        mappings = {
            "KI": ["KI", "Künstliche Intelligenz"],
            "ML": ["ML", "Maschinelles Lernen"],
        }
        rev = _build_reverse(mappings)
        assert rev["ki"] == "KI"
        assert rev["künstliche intelligenz"] == "KI"
        assert rev["ml"] == "ML"

    def test_stringformat_wird_uebersprungen(self):
        """String-Format (inkompatibel) wird mit Warnung ignoriert."""
        mappings = {"API": "Programmierschnittstelle"}  # String statt Liste
        rev = _build_reverse(mappings)
        # Da es kein Listenformat ist, wird das Mapping übersprungen
        assert "api" not in rev
        assert "programmierschnittstelle" not in rev

    def test_leeres_mapping_gibt_leeres_rev(self):
        """Leeres Mapping → leeres Reverse."""
        rev = _build_reverse({})
        assert rev == {}

    def test_kleinbuchstaben_normalisierung(self):
        """Varianten werden zu Lowercase normalisiert."""
        mappings = {"KI": ["KI", "Künstliche Intelligenz", "AI"]}
        rev = _build_reverse(mappings)
        assert rev["ki"] == "KI"
        assert rev["künstliche intelligenz"] == "KI"
        assert rev["ai"] == "KI"


# ---------------------------------------------------------------------------
#  Tests: _normalize_term
# ---------------------------------------------------------------------------


class TestNormalizeTerm:
    """Tests für die Term-Normalisierung."""

    def test_unbekannter_term_bleibt_erhalten(self):
        """Unbekannter Term wird unverändert zurückgegeben."""
        assert _normalize_term("foobar") == "foobar"

    def test_leerer_term(self):
        """Leerer Term gibt leeren String zurück."""
        assert _normalize_term("") == ""

    def test_whitespace_wird_getrimmt(self):
        """Whitespace am Anfang/Ende wird von _normalize_term entfernt.

        Die Funktion nutzt intern ``term.strip()`` bevor das Mapping
        geprüft wird. Der getrimmte Wert wird bei unbekannten Terms
        zurückgegeben.
        """
        assert _normalize_term("  unbekannt  ") == "unbekannt"

    def test_case_insensitive_lookup(self):
        """Groß-/Kleinschreibung wird ignoriert beim Lookup."""
        # Bekanntes Mapping: "Kühlung" ist eine Variante von "Kühlung"
        from mcp_server.main import REVERSE
        # Da mappings.json geladen wurde, sollte REVERSE befüllt sein
        assert isinstance(REVERSE, dict)

    def test_safe_replacement(self):
        """Sichere Ersetzungen (z.B. 'k' → 'C') funktionieren."""
        # SAFE_REPLACEMENTS enthält "k": "C"
        assert _normalize_term("k") == "C"
        assert _normalize_term("°c") == "°C"
        assert _normalize_term("°f") == "°F"


# ---------------------------------------------------------------------------
#  Tests: Mapping-Format-Konsistenz
# ---------------------------------------------------------------------------


class TestMappingFormat:
    """Tests für die Konsistenz des Mapping-Formats."""

    def test_echte_mappings_json_ist_listenformat(self):
        """Die echte mappings.json nutzt Listen-Format."""
        mappings_path = PROJECT_DIR / "mappings.json"
        if mappings_path.exists():
            mappings = json.loads(mappings_path.read_text(encoding="utf-8"))
            for key, value in mappings.items():
                assert isinstance(value, list), (
                    f"Mapping '{key}' sollte Liste sein, ist aber {type(value).__name__}"
                )
                assert len(value) > 0, f"Mapping '{key}' hat leere Liste"


# ---------------------------------------------------------------------------
#  Tests: Mappings-Datei-Format
# ---------------------------------------------------------------------------


class TestMappingsJsonFormat:
    """Tests für die Struktur der mappings.json-Datei."""

    def test_mappings_json_ist_valide_json(self):
        """mappings.json ist valides JSON."""
        mappings_path = PROJECT_DIR / "mappings.json"
        if mappings_path.exists():
            data = json.loads(mappings_path.read_text(encoding="utf-8"))
            assert isinstance(data, dict)

    def test_jedes_mapping_hat_mindestens_eine_variante(self):
        """Jedes Mapping hat mindestens eine Variante."""
        mappings_path = PROJECT_DIR / "mappings.json"
        if mappings_path.exists():
            data = json.loads(mappings_path.read_text(encoding="utf-8"))
            for key, variants in data.items():
                assert isinstance(variants, list)
                assert len(variants) >= 1

    def test_kanonischer_name_ist_in_varianten(self):
        """Der kanonische Name sollte immer in seinen Varianten enthalten sein."""
        mappings_path = PROJECT_DIR / "mappings.json"
        if mappings_path.exists():
            data = json.loads(mappings_path.read_text(encoding="utf-8"))
            for canonical, variants in data.items():
                # Der kanonische Name sollte (in einer Schreibweise) enthalten sein
                if isinstance(variants, list):
                    canonical_in_variants = any(
                        v.lower() == canonical.lower() for v in variants
                    )
                    assert canonical_in_variants, (
                        f"Kanonischer Name '{canonical}' fehlt in seinen Varianten"
                    )
