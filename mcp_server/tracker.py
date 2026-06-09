"""
Word-Frequency-Tracker für automatisches Mapping-Lernen und Canonical-Generierung.

Zählt Wortvorkommen über alle Tool-Aufrufe hinweg und schlägt
neue Mappings vor, wenn ein Wort eine konfigurierbare Schwelle
erreicht. Persistenz erfolgt idle-basiert (niedrige CPU/GPU-Last).

Unterstützt drei Ebenen der Token-Kompression:
  Ebene 1: *-PRÄFIX → Einzelwort (z.B. *-ANL → Anleitung)
  Ebene 2: *-PRÄFIX → Satzteil (z.B. *-ZB → zum Beispiel)
  Ebene 3: **-PRÄFIX → *-A + *-B (z.B. **-MOFA → *-MOT + *-FAH)
"""

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Standard-Schwellenwert: Ab 5 Vorkommen wird ein Kandidat vorgeschlagen
DEFAULT_THRESHOLD: int = 5

# Idle-Timeout: Sekunden ohne Aktivität bis Persistenz erfolgt
IDLE_TIMEOUT: float = 30.0

# Maximale Länge eines Canonical-Prefix (Buchstaben)
MAX_CANONICAL_PREFIX: int = 5


class WordFrequencyTracker:
    """
    In-Memory-Datenbank zur Wortzählung und Auto-Mapping-Generierung.

    Zählt alle Wörter, die über tool_map_text / tool_extract_entities
    verarbeitet werden. Wörter die eine Schwelle erreichen und weder
    in mappings.json noch in protected_terms.json vorhanden sind,
    werden als Mapping-Kandidaten vorgeschlagen.

    Neu: Alle Wörter (auch bekannte) werden in einem separaten Counter
    gezählt, um die automatische Canonical-Generierung zu ermöglichen.
    """

    def __init__(
        self,
        mappings: dict,
        protected: list,
        protected_phrases: Optional[list[str]] = None,
        threshold: int = DEFAULT_THRESHOLD,
        data_dir: Optional[Path] = None,
    ) -> None:
        self._mappings = mappings
        self._protected = protected
        self._protected_phrases = protected_phrases or []
        self._threshold = threshold
        self._data_dir = data_dir

        # In-Memory-Datenbank für unbekannte Wörter (bestehendes System)
        self._counts: dict[str, int] = {}
        self._contexts: dict[str, list[str]] = {}
        # Wort → {"count": N, "example": "..."}
        self._candidates: dict[str, dict] = {}

        # NEU: All-Words-Counter (auch bekannte Wörter)
        self._all_counts: dict[str, int] = {}
        self._all_contexts: dict[str, list[str]] = {}

        # NEU: Bigramm-Counter für zusammengesetzte Canonicals
        self._bigram_counts: dict[str, int] = {}
        self._bigram_contexts: dict[str, list[str]] = {}

        # Canonical-Pool: Canonical → Wort (für Kollisionserkennung)
        self._canonical_pool: dict[str, str] = {}

        # Protected-Set für schnellen Lookup
        self._protected_lower: set[str] = {t.lower() for t in protected}
        self._protected_phrases_lower: list[str] = [p.lower() for p in self._protected_phrases]

        # Mappings-Set für schnellen Lookup
        self._known_lower: set[str] = set()
        self._rebuild_known()

        # Idle-Persistenz
        self._dirty = False
        self._last_activity = time.time()
        self._lock = threading.RLock()
        self._shutdown_flag = False
        # Speicher‑Limit für das Tracking
        self._MAX_TRACKED_WORDS: int = 10_000
        self._MAX_BIGRAMS: int = 5_000
        self._MAX_ALL_WORDS: int = 20_000
        self._persist_timer: Optional[threading.Timer] = None
        self._auto_approve_timer: Optional[threading.Timer] = None

    def _rebuild_known(self) -> None:
        """Aktualisiere die Menge der bereits bekannten Wörter."""
        self._known_lower.clear()
        for canonical, variants in self._mappings.items():
            if isinstance(variants, list):
                for v in variants:
                    self._known_lower.add(v.lower())
            else:
                self._known_lower.add(str(variants).lower())
        self._known_lower.update(self._protected_lower)

    def record(self, text: str) -> list[str]:
        """
        Zählt alle Wörter im Text. Gibt neue Kandidaten-Status zurück.

        Zählt sowohl unbekannte Wörter (für Kandidaten-Vorschlag) als auch
        ALLE Wörter (für automatische Canonical-Generierung).

        Args:
            text: Der zu analysierende Text.

        Returns:
            Liste von Wörtern die gerade die Schwelle erreicht haben.
        """
        if not text or not text.strip():
            return []

        words = re.findall(r"\b[a-zA-ZäöüÄÖÜß]{2,}\b", text)
        new_candidates = []

        with self._lock:
            # 1. Unbekannte Wörter zählen (bestehendes System)
            for word in words:
                low = word.lower()

                if low in self._known_lower or low in self._protected_lower:
                    continue

                if low not in self._counts and len(
                        self._counts) >= self._MAX_TRACKED_WORDS:
                    continue
                self._counts[low] = self._counts.get(low, 0) + 1

                if low not in self._contexts:
                    self._contexts[low] = []
                if len(self._contexts[low]) < 3:
                    self._contexts[low].append(text[:100])

                if (
                    self._counts[low] >= self._threshold
                    and low not in self._candidates
                ):
                    self._candidates[low] = {
                        "count": self._counts[low],
                        "example": self._contexts[low][0] if self._contexts[low] else "",
                        "canonical": word.capitalize(),
                    }
                    new_candidates.append(low)
                    logger.info(
                        "Neuer Mapping-Kandidat: '%s' (%d Vorkommen)",
                        word, self._counts[low],
                    )

            # 2. ALLE Wörter zählen (für Auto-Canonical) – mit Speicher-Limit
            for word in words:
                low = word.lower()
                if low not in self._all_counts and len(self._all_counts) >= self._MAX_ALL_WORDS:
                    continue
                self._all_counts[low] = self._all_counts.get(low, 0) + 1

                if low not in self._all_contexts:
                    self._all_contexts[low] = []
                if len(self._all_contexts[low]) < 3:
                    self._all_contexts[low].append(text[:100])

            # 3. Bigramme zählen
            self._track_bigrams(words)

            self._dirty = True
            self._last_activity = time.time()
            self._schedule_persist()
            self._schedule_auto_approve()

        return new_candidates

    def _track_bigrams(self, words: list[str]) -> None:
        """Zählt Bigramme (2er-Wortkombinationen) für zusammengesetzte Canonicals.

        Args:
            words: Liste von Wörtern aus dem aktuellen Text.
        """
        if len(words) < 2:
            return

        for i in range(len(words) - 1):
            w1 = words[i].lower()
            w2 = words[i + 1].lower()
            bigram = f"{w1} {w2}"

            if len(self._bigram_counts) >= self._MAX_BIGRAMS:
                return

            self._bigram_counts[bigram] = self._bigram_counts.get(bigram, 0) + 1

            if bigram not in self._bigram_contexts:
                self._bigram_contexts[bigram] = []
            if len(self._bigram_contexts[bigram]) < 3:
                example = f"{words[i]} {words[i+1]}"
                self._bigram_contexts[bigram].append(example)

    def _generate_canonical(self, word: str) -> Optional[str]:
        """Generiert einen eindeutigen Canonical im Format *-PRÄFIX.

        Bei Kollision wird ein Index angehängt (*-PRÄFIX1, *-PRÄFIX2, ...).

        Args:
            word: Das Wort (lowercase) für das ein Canonical generiert werden soll.

        Returns:
            Canonical-String (z.B. '*-ANL') oder None wenn keine Ersparnis.
        """
        # Canonical nur für Wörter mit 4+ Buchstaben
        if len(word) < 4:
            return None

        # Prefix: Ersten Buchstaben groß, restliche Prefix-Buchstaben ebenfalls groß
        prefix_len = min(MAX_CANONICAL_PREFIX, len(word))
        raw_prefix = word[:prefix_len].upper()
        canonical = f"*-{raw_prefix}"

        # Ersparnis-Prüfung: (Wortlänge - Canonical-Länge) × 1 > 0
        if len(word) <= len(canonical):
            return None

        # Kollisionsprüfung
        if canonical not in self._canonical_pool:
            return canonical

        # Kollision: Index dranhängen
        index = 1
        while True:
            colliding = f"*-{raw_prefix}{index}"
            if colliding not in self._canonical_pool:
                return colliding
            index += 1
            # Sicherheitsgrenze – verhindert Endlosschleife
            if index > 999:
                logger.warning("Canonical-Kollision für '%s' nach 999 Versuchen", word)
                return None

    def _generate_bigram_canonical(self, bigram: str) -> Optional[str]:
        """Generiert einen zusammengesetzten Canonical im Format **-PRÄFIX.

        Args:
            bigram: Das Bigram (z.B. 'auto fahren').

        Returns:
            Zusammengesetzter Canonical (z.B. '**-AUFA') oder None.
        """
        words = bigram.split()
        if len(words) != 2:
            return None

        w1, w2 = words

        # Prüfe ob beide Wörter eigene Canonicals haben
        c1 = None
        c2 = None
        for can, mapped_word in self._canonical_pool.items():
            if mapped_word == w1:
                c1 = can
            if mapped_word == w2:
                c2 = can

        if c1 is None or c2 is None:
            return None

        # Bigram-Canonical: **- + erste 2 Buchstaben von Wort1 + erste 2 von Wort2
        prefix = (w1[:2] + w2[:2]).upper()
        canonical = f"**-{prefix}"

        if len(bigram) <= len(canonical):
            return None

        # Kollisionsprüfung
        index = 0
        base = canonical
        while canonical in self._canonical_pool:
            index += 1
            if index > 99:
                return None
            canonical = f"{base}{index}"

        return canonical

    def _auto_approve(self) -> int:
        """Prüft automatisch, ob Wörter mit ≥ threshold für ein Canonical lohnen.

        Returns:
            Anzahl neu erstellter Canonicals.
        """
        created = 0

        with self._lock:
            # 1. Einzelwörter prüfen (ab threshold)
            for word, count in sorted(
                self._all_counts.items(), key=lambda x: -x[1]
            ):
                if count < self._threshold:
                    continue

                # Prüfe ob bereits als Mapping oder Canonical existiert
                if word in self._known_lower:
                    continue

                # Prüfe ob in Protected
                if word in self._protected_lower:
                    continue

                # Prüfe ob bereits ein Canonical existiert
                already_mapped = False
                for mapped_word in self._canonical_pool.values():
                    if mapped_word == word:
                        already_mapped = True
                        break
                if already_mapped:
                    continue

                # Canonical generieren
                canonical = self._generate_canonical(word)
                if canonical is None:
                    continue

                # In Mappings speichern
                self._mappings[canonical] = [word]
                self._canonical_pool[canonical] = word
                self._known_lower.add(word)
                created += 1

                logger.info(
                    "Auto-Canonical: '%s' → '%s' (%d Vorkommen)",
                    canonical, word, count,
                )

            # 2. Bigramme prüfen (ab threshold)
            for bigram, count in sorted(
                self._bigram_counts.items(), key=lambda x: -x[1]
            ):
                if count < self._threshold:
                    continue

                # Prüfe ob bereits als Mapping existiert
                if bigram in self._known_lower:
                    continue

                canonical = self._generate_bigram_canonical(bigram)
                if canonical is None:
                    continue

                self._mappings[canonical] = [bigram]
                self._canonical_pool[canonical] = bigram
                created += 1

                logger.info(
                    "Auto-Bigram-Canonical: '%s' → '%s' (%d Vorkommen)",
                    canonical, bigram, count,
                )

        return created

    def _schedule_auto_approve(self) -> None:
        """Startet Auto-Approve in einem separaten Thread (nach 2s Verzögerung)."""
        if self._shutdown_flag:
            return
        if self._auto_approve_timer is not None:
            self._auto_approve_timer.cancel()
        self._auto_approve_timer = threading.Timer(2.0, self._auto_approve_wrapper)
        self._auto_approve_timer.daemon = True
        self._auto_approve_timer.start()

    def _auto_approve_wrapper(self) -> None:
        """Wrapper für _auto_approve mit Logging."""
        if self._shutdown_flag:
            return
        try:
            created = self._auto_approve()
            if created > 0:
                logger.info("Auto-Approve: %d neue Canonicals erstellt", created)
                self._persist_now()
        except Exception as exc:
            logger.error("Fehler bei Auto-Approve: %s", exc)

    def get_candidate_words(self) -> dict[str, dict]:
        """Gibt alle Kandidaten zurück (Wörter die die Schwelle erreicht haben)."""
        with self._lock:
            return dict(self._candidates)

    def approve_candidate(self, word: str, canonical: str) -> bool:
        """
        Bestätigt einen Kandidaten als neues Mapping.

        Args:
            word: Das zu bestätigende Wort (lowercase).
            canonical: Der kanonische Name (z.B. "Kühlung").

        Returns:
            True wenn erfolgreich, False wenn Wort kein Kandidat ist.
        """
        with self._lock:
            if word not in self._candidates:
                return False

            if canonical not in self._mappings:
                self._mappings[canonical] = [word]
            elif isinstance(self._mappings[canonical], list):
                if word not in self._mappings[canonical]:
                    self._mappings[canonical].append(word)
            elif isinstance(self._mappings[canonical], str):
                self._mappings[canonical] = [self._mappings[canonical]]
                if word not in self._mappings[canonical]:
                    self._mappings[canonical].append(word)

            self._rebuild_known()

            del self._candidates[word]

            self._dirty = True
            self._persist_now()

            logger.info("Mapping bestätigt: '%s' → '%s'", word, canonical)
            return True

    def get_prompt_context(self, category: str = "") -> str:
        """
        Gibt alle Canonical-Mappings als kompakte Zeichenkette zurück.

        Format: *-ANL→Anleitung|*-MOD→Modell|**-MOFA→*-MOT+*-FAH

        Args:
            category: Optionaler Filter (zeigt nur Mappings die diesen String enthalten).

        Returns:
            Kompakte Canonical-Zeichenkette für den LLM-Prompt.
        """
        with self._lock:
            parts: list[str] = []
            for canonical, variants in sorted(self._mappings.items()):
                if not canonical.startswith(("*-", "**-")):
                    continue

                if isinstance(variants, list) and len(variants) > 0:
                    word = str(variants[0])
                else:
                    word = str(variants)

                if category and category.lower() not in word.lower() and category.lower() not in canonical.lower():
                    continue

                parts.append(f"{canonical}→{word}")

            if not parts:
                return ""

            return "|".join(parts)

    def get_llm_doku(self) -> str:
        """
        Gibt eine vollständige LLM-Dokumentation zurück.

        Enthält alle Task-Canonicals, Wort-Mappings und Bigram-Mappings
        als kompakte Zeichenkette.

        Returns:
            Vollständige Doku als String.
        """
        with self._lock:
            lines: list[str] = []
            lines.append("# LLM-Doku – Semantik MCP")
            lines.append("")
            lines.append("## Aktive Canonicals")
            lines.append(self.get_prompt_context())
            lines.append("")
            lines.append("## Letzte Task-Historie (max 20)")
            lines.extend(self._get_task_history_for_doku())
            lines.append("")
            lines.append("## Mapping-Statistik")
            total = sum(1 for k in self._mappings if k.startswith(("*-", "**-")))
            lines.append(f"Aktive Canonicals: {total}")
            words_tracked = len(self._all_counts)
            lines.append(f"Wörter im Tracking: {words_tracked}")
            return "\n".join(lines)

    def _get_task_history_for_doku(self) -> list[str]:
        """Gibt die letzten Aufgaben als Doku-Zeilen zurück."""
        if not hasattr(self, '_task_history'):
            self._task_history: list[dict] = []
        lines: list[str] = []
        for task in self._task_history[-20:]:
            tool = task.get("tool", "?")
            desc = task.get("description", "")[:80]
            ts = task.get("timestamp", "")
            lines.append(f"- {ts}: {tool} – {desc}")
        return lines

    def add_task_log(self, tool_name: str, description: str) -> None:
        """Fügt einen Task zum Verlauf hinzu."""
        if not hasattr(self, '_task_history'):
            self._task_history: list[dict] = []
        self._task_history.append({
            "tool": tool_name,
            "description": description[:200],
            "timestamp": time.strftime("%d.%m. %H:%M"),
        })
        # Max 100 Tasks im Speicher
        if len(self._task_history) > 100:
            self._task_history = self._task_history[-100:]

    def get_all_frequencies(self, min_count: int = 1) -> dict[str, int]:
        """
        Gibt alle Wortfrequenzen zurück (für Analyse).

        Args:
            min_count: Minimale Anzahl für die Rückgabe.

        Returns:
            Dict {wort: anzahl} sortiert nach Häufigkeit.
        """
        with self._lock:
            return {
                word: count
                for word, count in sorted(
                    self._all_counts.items(), key=lambda x: -x[1]
                )
                if count >= min_count
            }

    def get_stats(self) -> dict:
        """Statistiken über die Lern-Datenbank."""
        with self._lock:
            canonical_count = sum(
                1 for k in self._mappings if k.startswith(("*-", "**-"))
            )
            return {
                "total_words_tracked": len(self._counts),
                "total_candidates": len(self._candidates),
                "threshold": self._threshold,
                "top_words": sorted(
                    self._counts.items(), key=lambda x: x[1], reverse=True
                )[:20],
                "candidates": dict(self._candidates),
                "canonicals_active": canonical_count,
                "all_words_tracked": len(self._all_counts),
                "bigrams_tracked": len(self._bigram_counts),
            }

    def _schedule_persist(self) -> None:
        """Plane Persistenz für wenn idle (niedrige CPU-Last)."""
        if self._persist_timer is not None:
            self._persist_timer.cancel()

        self._persist_timer = threading.Timer(
            IDLE_TIMEOUT, self._persist_if_idle)
        self._persist_timer.daemon = True
        self._persist_timer.start()

    def _persist_if_idle(self) -> None:
        """Persistiere nur wenn genug Zeit seit letzter Aktivität vergangen."""
        with self._lock:
            if not self._dirty:
                return

            idle_time = time.time() - self._last_activity
            if idle_time < IDLE_TIMEOUT:
                self._schedule_persist()
                return

            self._persist_now()

    def _persist_now(self) -> None:
        """Schreibe die aktuellen Mappings in die JSON-Datei."""
        if self._data_dir is None:
            return

        try:
            mappings_path = self._data_dir / "mappings.json"
            with open(mappings_path, "w", encoding="utf-8") as fh:
                json.dump(self._mappings, fh, ensure_ascii=False, indent=2)
            self._dirty = False
            logger.info("Mappings persistent gespeichert: %s", mappings_path)
        except OSError as exc:
            logger.error("Fehler beim Persistieren der Mappings: %s", exc)

    def shutdown(self) -> None:
        """Sofortige Persistenz beim Herunterfahren."""
        self._shutdown_flag = True
        if self._persist_timer is not None:
            self._persist_timer.cancel()
        if self._auto_approve_timer is not None:
            self._auto_approve_timer.cancel()

        with self._lock:
            if self._dirty:
                self._persist_now()
