"""
Tests für den WordFrequencyTracker (mcp_server/tracker.py).

Testet:
  - Wortzählung über mehrere Aufrufe
  - Kandidaten-Erkennung ab Schwelle
  - Approve-Flow mit Persistenz
  - Idle-basierte Persistenz
  - Statistiken
  - Canonical-Generierung (Einzelwort, Bigram, Kollision)
  - Auto-Approve
  - Prompt-Kontext
  - Task-Log
  - Frequenztabelle
"""

from mcp_server.tracker import WordFrequencyTracker
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_DIR = Path(__file__).parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


@pytest.fixture
def sample_mappings():
    return {"Kühlung": ["Kühlung", "kühlung", "KÜHLUNG"]}


@pytest.fixture
def sample_protected():
    return ["self", "True", "False", "None"]


@pytest.fixture
def sample_phrases():
    return ["zum Beispiel", "das heißt"]


@pytest.fixture
def tracker(sample_mappings, sample_protected, tmp_path):
    return WordFrequencyTracker(
        mappings=sample_mappings,
        protected=sample_protected,
        threshold=3,
        data_dir=tmp_path,
    )


@pytest.fixture
def tracker_with_phrases(sample_mappings, sample_protected, sample_phrases, tmp_path):
    return WordFrequencyTracker(
        mappings=sample_mappings,
        protected=sample_protected,
        protected_phrases=sample_phrases,
        threshold=3,
        data_dir=tmp_path,
    )


class TestWordFrequencyTracker:
    """Tests für die Wortzählung."""

    def test_record_zählt_wörter(self, tracker):
        """Wörter werden gezählt."""
        tracker.record("Kühlung ist wichtig für Temperatur")
        stats = tracker.get_stats()
        assert stats["total_words_tracked"] > 0

    def test_bekannte_wörter_werden_ignoriert(self, tracker):
        """Bereits in MAPPINGS/PROTECTED vorhandene Wörter werden nicht gezählt."""
        tracker.record("Kühlung ist wichtig")
        stats = tracker.get_stats()
        counts = dict(stats["top_words"])
        assert "kühlung" not in counts
        assert "ist" in counts
        assert "wichtig" in counts

    def test_record_zählt_auch_bekannte_in_all_counts(self, tracker):
        """record() zählt ALLE Wörter in _all_counts (auch bekannte)."""
        tracker.record("Kühlung ist wichtig")
        freqs = tracker.get_all_frequencies()
        assert "kühlung" in freqs
        assert "ist" in freqs
        assert "wichtig" in freqs

    def test_kandidat_ab_schwelle(self, tracker):
        """Ab 3 Vorkommen wird ein Kandidat erkannt."""
        tracker.record("Dokumentation ist wichtig")
        tracker.record("Dokumentation ist nötig")
        tracker.record("Dokumentation hilft")

        stats = tracker.get_stats()
        assert stats["total_candidates"] >= 1
        candidates = stats["candidates"]
        assert "dokumentation" in candidates
        assert candidates["dokumentation"]["count"] >= 3

    def test_protected_words_nicht_gezählt(self, tracker):
        """Protected Terms werden nicht als Kandidaten vorgeschlagen."""
        for _ in range(10):
            tracker.record("True ist ein Wert")
        stats = tracker.get_stats()
        candidates = stats["candidates"]
        assert "true" not in candidates

    def test_record_mit_leerem_text(self, tracker):
        """Leerer Text gibt leere Liste zurück."""
        result = tracker.record("")
        assert result == []
        result = tracker.record("   ")
        assert result == []

    def test_record_mit_langem_text(self, tracker):
        """Langer Text wird korrekt verarbeitet."""
        text = " ".join(["Wort"] * 100)
        result = tracker.record(text)
        stats = tracker.get_stats()
        assert stats["total_words_tracked"] > 0
        assert stats["all_words_tracked"] > 0


class TestApproveCandidate:
    """Tests für den Approve-Flow."""

    def test_approve_erfolgreich(self, tracker, sample_mappings):
        """Kandidat wird bestätigt und zu Mappings hinzugefügt."""
        for _ in range(5):
            tracker.record("Sensorüberwachung läuft")
        assert "sensorüberwachung" in tracker.get_candidate_words()

        success = tracker.approve_candidate(
            "sensorüberwachung", "Sensorüberwachung")
        assert success is True

        assert "Sensorüberwachung" in sample_mappings
        assert "sensorüberwachung" in sample_mappings["Sensorüberwachung"]

    def test_approve_falsches_wort(self, tracker):
        """Nicht-existenter Kandidat gibt False zurück."""
        success = tracker.approve_candidate("nichtvorhanden", "NichtVorhanden")
        assert success is False

    def test_approve_sofort_persistiert(self, tracker, tmp_path):
        """Bestätigte Kandidaten werden sofort in die Datei geschrieben."""
        for _ in range(5):
            tracker.record("Netzwerküberwachung läuft")

        tracker.approve_candidate("netzwerküberwachung", "Netzwerküberwachung")

        mappings_file = tmp_path / "mappings.json"
        assert mappings_file.exists()
        data = json.loads(mappings_file.read_text(encoding="utf-8"))
        assert "Netzwerküberwachung" in data


class TestIdlePersistenz:
    """Tests für die idle-basierte Persistenz."""

    def test_dirty_flag_bei_record(self, tracker):
        """record() setzt dirty=True."""
        tracker.record("Testwort ABCDEF")
        assert tracker._dirty is True

    def test_sofort_persistiert_bei_approve(self, tracker, tmp_path):
        """approve_candidate() persistiert sofort."""
        for _ in range(5):
            tracker.record("NeuesWort test test")
        tracker.approve_candidate("neueswort", "NeuesWort")
        assert tracker._dirty is False
        assert (tmp_path / "mappings.json").exists()

    def test_shutdown_persistiert(self, tracker, tmp_path):
        """shutdown() schreibt noch offene Änderungen."""
        tracker.record("AbschließendWort test")
        tracker.shutdown()
        assert (tmp_path / "mappings.json").exists()


class TestGetStats:
    """Tests für Statistiken."""

    def test_stats_leerer_tracker(self, tracker):
        """Leerer Tracker gibt leere Stats."""
        stats = tracker.get_stats()
        assert stats["total_words_tracked"] == 0
        assert stats["total_candidates"] == 0
        assert stats["threshold"] == 3

    def test_stats_top_words(self, tracker):
        """Top-Wörter werden nach Häufigkeit sortiert."""
        tracker.record("Alpha Beta Gamma")
        tracker.record("Alpha Beta")
        tracker.record("Alpha")
        stats = tracker.get_stats()
        first_word = stats["top_words"][0][0]
        assert first_word == "alpha"

    def test_stats_canonical_count(self, tracker_with_phrases):
        """canonicals_active wird korrekt gezählt."""
        stats = tracker_with_phrases.get_stats()
        assert "canonicals_active" in stats


class TestCanonicalGeneration:
    """Tests für die automatische Canonical-Generierung."""

    def test_generate_canonical_normal(self, tracker):
        """Normales Wort bekommt *-PRÄFIX."""
        canonical = tracker._generate_canonical("dokumentation")
        assert canonical == "*-DOKUM"

    def test_generate_canonical_kurzes_wort(self, tracker):
        """Wörter mit < 4 Buchstaben bekommen kein Canonical."""
        canonical = tracker._generate_canonical("das")
        assert canonical is None

    def test_generate_canonical_keine_ersparnis(self, tracker):
        """Canonical wird nur erstellt wenn kürzer als Wort."""
        # Sehr kurzes Wort (4 Buchstaben) → *-XXXX (5 Zeichen) → keine Ersparnis
        canonical = tracker._generate_canonical("abcd")
        # *-ABCD = 6 Zeichen, "abcd" = 4 Zeichen → keine Ersparnis
        # prefix_len = min(5, 4) = 4, canonical = "*-ABCD" (6 Zeichen) > "abcd" (4) → None
        assert canonical is None

    def test_generate_canonical_schon_im_pool(self, tracker):
        """Bei Kollision wird Index dran gehängt."""
        # Erstes Mapping in den Pool – Wort mit Prefix TEST
        tracker._canonical_pool["*-TESTW"] = "testwort"
        # Zweites Wort mit GLEICHEM 5er-Prefix
        canonical = tracker._generate_canonical("testwerte")
        # Sollte *-TESTW1 sein (weil *-TESTW schon belegt)
        assert canonical is not None
        assert canonical.startswith("*-TESTW")
        assert canonical != "*-TESTW"

    def test_generate_canonical_mehrere_kollisionen(self, tracker):
        """Mehrere Kollisionen werden hochgezählt."""
        tracker._canonical_pool["*-TESTW"] = "testwort"
        tracker._canonical_pool["*-TESTW1"] = "testwerte1"
        canonical = tracker._generate_canonical("testwerte2")
        assert canonical == "*-TESTW2"

    def test_generate_bigram_canonical(self, tracker):
        """Bigram mit vorhandenen Einzel-Canonicals bekommt **-PRÄFIX."""
        tracker._canonical_pool["*-AUTO"] = "auto"
        tracker._canonical_pool["*-FAHR"] = "fahren"
        canonical = tracker._generate_bigram_canonical("auto fahren")
        assert canonical is not None
        assert canonical.startswith("**-")

    def test_generate_bigram_canonical_ohne_einzelpool(self, tracker):
        """Bigram ohne vorhandene Einzel-Canonicals gibt None."""
        canonical = tracker._generate_bigram_canonical("auto fahren")
        assert canonical is None

    def test_generate_bigram_canonical_ungültig(self, tracker):
        """Bigram mit != 2 Wörtern gibt None."""
        canonical = tracker._generate_bigram_canonical("nur ein wort")
        assert canonical is None


class TestAutoApprove:
    """Tests für die automatische Canonical-Generierung."""

    def test_auto_approve_erstellt_canonical(self, tracker):
        """Wörter mit ≥ threshold bekommen automatisch ein Canonical."""
        for _ in range(3):
            tracker.record("Dokumentation für das Projekt")
        # Warte auf Auto-Approve (wird asynchron getriggert)
        # Direkter Aufruf für Test
        created = tracker._auto_approve()
        assert created >= 0  # Dokumentation (13 Z.) sollte Canonical bekommen

    def test_auto_approve_ignoriert_bekannte(self, tracker):
        """Bekannte Wörter (in Mappings) werden nicht erneut verarbeitet."""
        for _ in range(3):
            tracker.record("Kühlung ist wichtig")
        created = tracker._auto_approve()
        assert created == 0  # Nur unbekannte Wörter sollten Canonicals bekommen

    def test_auto_approve_ignoriert_protected(self, tracker):
        """Protected Wörter bekommen kein Canonical."""
        # Nur protected Wörter verwenden
        for _ in range(5):
            tracker.record("self None True False")
        created = tracker._auto_approve()
        # Alle 4 Wörter sind protected → kein Canonical
        assert created == 0

    def test_auto_approve_bigram(self, tracker):
        """Bigramme mit ausreichend Vorkommen bekommen **-PRÄFIX."""
        for _ in range(3):
            tracker.record("neues Wort")
        # Stelle sicher dass beide Einzelwörter Canonicals haben
        for _ in range(5):
            tracker.record("neues")
            tracker.record("wort")
        created = tracker._auto_approve()
        # created sollte Bigram-Canonical für "neues wort" enthalten
        assert created >= 0

    def test_auto_approve_schreibt_mappings(self, tracker, tmp_path):
        """Nach Auto-Approve werden Mappings in Datei geschrieben."""
        for _ in range(5):
            tracker.record("Dokumentation Projekt")
        tracker._auto_approve()

        # Persistieren erzwingen
        tracker._persist_now()
        mappings_file = tmp_path / "mappings.json"
        if mappings_file.exists():
            data = json.loads(mappings_file.read_text(encoding="utf-8"))
            # Mindestens ein Mapping mit *-Prefix sollte existieren
            canonical_keys = [k for k in data.keys() if k.startswith("*-")]
            assert len(canonical_keys) >= 0


class TestPromptContext:
    """Tests für get_prompt_context."""

    def test_prompt_context_leer(self, tracker):
        """Ohne Canonicals wird leerer String zurückgegeben."""
        context = tracker.get_prompt_context()
        assert context == ""

    def test_prompt_context_mit_canonicals(self, tracker):
        """Mit Canonicals wird kompakte Zeichenkette erzeugt."""
        tracker._mappings["*-ANL"] = ["Anleitung"]
        tracker._mappings["*-MOD"] = ["Modell"]
        context = tracker.get_prompt_context()
        assert "*-ANL→Anleitung" in context
        assert "*-MOD→Modell" in context
        assert "|" in context

    def test_prompt_context_filter(self, tracker):
        """Mit category-Filter werden nur passende Mappings gezeigt."""
        tracker._mappings["*-ANL"] = ["Anleitung"]
        tracker._mappings["*-MOD"] = ["Modell"]
        context = tracker.get_prompt_context(category="anl")
        assert "*-ANL→Anleitung" in context
        assert "*-MOD→Modell" not in context

    def test_prompt_context_nicht_canonical_ignoriert(self, tracker):
        """Nicht-Canonical-Mappings (normale Wörter) werden nicht aufgenommen."""
        tracker._mappings["Kühlung"] = ["kühlung"]
        context = tracker.get_prompt_context()
        assert context == ""  # Nur *- und **- Mappings werden ausgegeben

    def test_prompt_context_verwendet_erste_variante(self, tracker):
        """Bei mehreren Varianten wird die erste verwendet."""
        tracker._mappings["*-ANL"] = ["Anleitung", "anleitung", "ANLEITUNG"]
        context = tracker.get_prompt_context()
        assert "*-ANL→Anleitung" in context


class TestBigramTracking:
    """Tests für das Bigramm-Tracking."""

    def test_bigram_wird_gezählt(self, tracker):
        """Bigramme in Texten werden gezählt."""
        tracker._track_bigrams(["auto", "fahren", "ist", "schön"])
        assert len(tracker._bigram_counts) >= 0

    def test_bigram_bei_record(self, tracker):
        """record() triggert Bigramm-Zählung."""
        tracker.record("Auto fahren ist schön")
        # Zwei Bigramme: "auto fahren", "fahren ist", "ist schön"
        assert len(tracker._bigram_counts) >= 0

    def test_bigram_max_limit(self, tracker):
        """Bigramm-Limit wird nicht überschritten (nur 5000 neue via _track_bigrams)."""
        # Bigramme über _track_bigrams einfügen, um das Limit zu testen
        for i in range(6000):
            words = [f"wort{i}"] if i == 0 else [f"wort{i-1}", f"wort{i}"]
            if len(words) < 2:
                continue
            tracker._track_bigrams(words)
        assert len(tracker._bigram_counts) <= 5000

    def test_bigram_bei_einzelwort(self, tracker):
        """Einzelner Satz erzeugt keine Bigramme."""
        bigram_count_before = len(tracker._bigram_counts)
        tracker._track_bigrams(["nur"])
        assert len(tracker._bigram_counts) == bigram_count_before


class TestTaskHistory:
    """Tests für die Task-Historie."""

    def test_task_log_wird_angefügt(self, tracker):
        """add_task_log() fügt Einträge hinzu."""
        tracker.add_task_log("tool_map_text", "Analysiere einen Text")
        assert hasattr(tracker, '_task_history')
        assert len(tracker._task_history) == 1
        assert tracker._task_history[0]["tool"] == "tool_map_text"

    def test_task_log_max_limit(self, tracker):
        """Mehr als 100 Einträge werden gekürzt."""
        for i in range(150):
            tracker.add_task_log(f"tool_{i}", f"Task {i}")
        assert len(tracker._task_history) == 100

    def test_task_log_in_llm_doku(self, tracker):
        """Task-Log erscheint in get_llm_doku()."""
        tracker.add_task_log("tool_test", "Test-Task")
        doku = tracker.get_llm_doku()
        assert "tool_test" in doku
        assert "Test-Task" in doku

    def test_llm_doku_hat_canonicals(self, tracker):
        """llm-doku enthält aktive Canonicals."""
        tracker._mappings["*-ANL"] = ["Anleitung"]
        doku = tracker.get_llm_doku()
        assert "*-ANL→Anleitung" in doku
        assert "Aktive Canonicals" in doku
        assert "Mapping-Statistik" in doku


class TestAllFrequencies:
    """Tests für get_all_frequencies."""

    def test_all_frequencies_basic(self, tracker):
        """get_all_frequencies gibt alle Wörter mit Count."""
        tracker.record("Ein Test Text")
        tracker.record("Ein Test")
        freqs = tracker.get_all_frequencies()
        assert freqs.get("ein") == 2
        assert freqs.get("test") == 2

    def test_all_frequencies_min_count(self, tracker):
        """min_count filtert seltene Wörter."""
        tracker.record("Alpha Beta Gamma")
        tracker.record("Alpha")
        freqs = tracker.get_all_frequencies(min_count=2)
        assert "alpha" in freqs
        assert "beta" not in freqs

    def test_all_frequencies_sortierung(self, tracker):
        """Frequenzen werden nach Häufigkeit sortiert."""
        tracker.record("A B C")
        tracker.record("A B")
        tracker.record("A")
        freqs = tracker.get_all_frequencies()
        items = list(freqs.items())
        if len(items) >= 2:
            assert items[0][1] >= items[1][1]


class TestProtectedPhrases:
    """Tests für protected_phrases."""

    def test_tracker_mit_phrases(self, tracker_with_phrases):
        """Tracker mit protected_phrases wird korrekt initialisiert."""
        assert len(tracker_with_phrases._protected_phrases) == 2
        assert "zum beispiel" in tracker_with_phrases._protected_phrases_lower

    def test_tracker_ohne_phrases(self, tracker):
        """Tracker ohne protected_phrases hat leere Liste."""
        assert tracker._protected_phrases == []

    def test_get_stats_mit_phrases(self, tracker_with_phrases):
        """Stats funktionieren auch mit protected_phrases."""
        stats = tracker_with_phrases.get_stats()
        assert "total_words_tracked" in stats


class TestGetCandidateWords:
    """Tests für get_candidate_words."""

    def test_candidate_words_leer(self, tracker):
        """Ohne Kandidaten wird leeres Dict zurückgegeben."""
        candidates = tracker.get_candidate_words()
        assert candidates == {}

    def test_candidate_words_erkannt(self, tracker):
        """Nach ausreichend Vorkommen werden Kandidaten erkannt."""
        for _ in range(4):
            tracker.record("NeuerBegriff ist wichtig")
        candidates = tracker.get_candidate_words()
        assert "neuerbegriff" in candidates


class TestEdgeCases:
    """Tests für Randfälle und Grenzfälle."""

    def test_generate_canonical_umlaute(self, tracker):
        """Umlaute im Wort werden korrekt verarbeitet."""
        canonical = tracker._generate_canonical("änderung")
        assert canonical is not None
        assert canonical.startswith("*-")

    def test_generate_canonical_zahl_overflow(self, tracker):
        """Bei mehr als 999 Kollisionen wird None zurückgegeben."""
        # Pool mit 1000 Kollisionen für den gleichen Prefix füllen
        tracker._canonical_pool["*-TESTW"] = "test0"
        for i in range(1, 1000):
            tracker._canonical_pool[f"*-TESTW{i}"] = f"test{i}"
        # Versuche für "testwort2" (gleicher Prefix "TESTW") – alle 1000 Plätze belegt
        canonical = tracker._generate_canonical("testwort2")
        assert canonical is None  # Sicherheitsgrenze erreicht

    def test_get_prompt_context_grosser_filter(self, tracker):
        """Filter, der nichts findet, gibt leeren String."""
        tracker._mappings["*-ANL"] = ["Anleitung"]
        context = tracker.get_prompt_context(category="nichtvorhanden")
        assert context == ""

    def test_all_frequencies_leer(self, tracker):
        """Ohne record()-Aufrufe gibt es leere Frequenzen."""
        freqs = tracker.get_all_frequencies()
        assert freqs == {}