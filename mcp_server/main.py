"""
MCP-Server für semantische Textanalyse mit automatischem Mapping-Lernen.

Stellt 11 Tools als MCP-konforme Server bereit:
  - Terminologie-Normalisierung und Analyse
  - Automatisches Mapping-Lernen (Word-Frequency-Tracker)
  - LLM-Konfiguration (Env-Variablen + config.json)
"""

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Annotated, Any, cast

from pydantic import Field

# Imports der App / Drittanbieter
from mcp_server.llm_providers import secrets
from mcp.server.fastmcp import FastMCP

# Wurzelverzeichnis (ein Verzeichnis über mcp_server/)
ROOT_DIR = Path(__file__).resolve().parent.parent

# Windows-Konsolen-Kodierung auf UTF-8 setzen
if sys.platform == "win32":
    # Type-Ignore: reconfigure ist eine dynamische Eigenschaft von TextIOWrapper
    # auf Windows, für Pylance nicht direkt sichtbar.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Sicherheitskonstanten
# ---------------------------------------------------------------------------

MAX_INPUT_LENGTH: int = 100_000

# ---------------------------------------------------------------------------
#  LLM-Konfiguration (Env-Variablen)
# ---------------------------------------------------------------------------
#  Hinweis: Diese Variablen sind bewusst GROẞBUCHSTABEN (Konvention für
#  Konfig-Konstanten), werden aber zur Laufzeit von _load_llm_config und
#  tool_configure neu zugewiesen. Daher kein ``Final``-Decorator.

LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "")
LLM_BASE_URL: str = os.environ.get("LLM_BASE_URL", "")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "")
LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")
AUTO_LEARN_MODE: str = os.environ.get("AUTO_LEARN_MODE", "approve")
LEARNING_THRESHOLD: int = int(os.environ.get("LEARNING_THRESHOLD", "5"))

# ---------------------------------------------------------------------------
#  Daten laden (mit Fehlerbehandlung)
# ---------------------------------------------------------------------------

# Mappings: kanonischer Name → Liste von Varianten
MAPPINGS: dict[str, list[str]] = {}
PROTECTED: list[str] = []
PROTECTED_PHRASES: list[str] = []

# Laden von mappings.json und protected_terms.json ohne _safe_path
# (definiert später)
try:
    with open(ROOT_DIR / "mappings.json", encoding="utf-8") as _f:
        _loaded: Any = json.load(_f)
    if isinstance(_loaded, dict):
        # Nur gültige Mappings (kanonisch → list[str]) übernehmen
        for _k, _v in _loaded.items():
            _key = str(_k)
            _val = _v
            if isinstance(_val, list):
                MAPPINGS[_key] = [str(x) for x in _val]
except (FileNotFoundError, json.JSONDecodeError, PermissionError) as exc:
    logger.error("mappings.json konnte nicht geladen werden: %s", exc)

try:
    with open(ROOT_DIR / "protected_terms.json", encoding="utf-8") as _f:
        _raw: Any = json.load(_f)
    if isinstance(_raw, dict):
        # _terms_raw ist bereits eine list (durch isinstance vorher sichergestellt)
        PROTECTED = [str(t) for t in cast(list[Any], _raw.get("terms", []))]  # type: ignore[misc]
        PROTECTED_PHRASES = [str(p) for p in cast(list[Any], _raw.get("phrases", []))]  # type: ignore[misc]
    elif isinstance(_raw, list):
        PROTECTED = [str(t) for t in _raw]  # type: ignore[misc]
except (FileNotFoundError, json.JSONDecodeError, PermissionError) as exc:
    logger.error("protected_terms.json konnte nicht geladen werden: %s", exc)

# ---------------------------------------------------------------------------
#  LLM-Konfiguration auch aus config.json lesen (Fallback)
# ---------------------------------------------------------------------------


def _load_llm_config() -> None:
    """Lädt LLM-Konfiguration aus config.json, wenn Env-Variablen leer sind."""
    global LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY  # noqa: PLW0603
    config_path = ROOT_DIR / "config.json"
    if not config_path.exists():
        return
    try:
        # _safe_path wird erst später definiert – hier direkt öffnen
        with open(config_path, encoding="utf-8") as fh:
            _raw_cfg: Any = json.load(fh)
        if not isinstance(_raw_cfg, dict):
            return
        # cast hilft Pylance beim Type-Inferenz für .get() auf dict[str, Any]
        llm: Any = cast(dict[str, Any], _raw_cfg).get("llm", {})
        if not isinstance(llm, dict):
            return
        if not LLM_PROVIDER:
            LLM_PROVIDER = str(cast(dict[str, Any], llm).get("default_provider", ""))  # type: ignore[misc]
        provider_cfg: Any = cast(dict[str, Any], llm).get(LLM_PROVIDER, {})
        if not isinstance(provider_cfg, dict):
            provider_cfg = {}
        if not LLM_BASE_URL:
            LLM_BASE_URL = str(cast(dict[str, Any], provider_cfg).get("base_url", ""))  # type: ignore[misc]
        if not LLM_MODEL:
            LLM_MODEL = str(cast(dict[str, Any], provider_cfg).get("default_model", ""))  # type: ignore[misc]
        if not LLM_API_KEY:
            LLM_API_KEY = str(cast(dict[str, Any], provider_cfg).get("api_key", ""))  # type: ignore[misc]
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("config.json nicht lesbar: %s", exc)


_load_llm_config()

# ---------------------------------------------------------------------------
#  Hilfsfunktionen
# ---------------------------------------------------------------------------


def _validate_input(text: str, max_length: int = MAX_INPUT_LENGTH) -> str:
    """Validiert Eingabe auf Länge und leere Strings."""
    if not text or not text.strip():
        raise ValueError("Eingabe-Text darf nicht leer sein.")
    if len(text) > max_length:
        raise ValueError(
            f"Eingabe-Text zu lang ({len(text)} Zeichen). "
            f"Maximum: {max_length} Zeichen."
        )
    return text


def _safe_path(base: Path, filename: str) -> Path:
    """
    Verhindert Path Traversal, indem sichergestellt wird, dass die
    resultierende Datei innerhalb des Basisverzeichnisses liegt.
    """
    resolved_base = base.resolve()
    target_path = (base / filename).resolve()
    if not target_path.is_relative_to(resolved_base):
        raise PermissionError(f"Unzulässiger Pfadzugriff: {filename}")
    return target_path


def _build_reverse(mapping: dict[str, list[str]]) -> dict[str, str]:
    """Baut Reverse-Mapping: Variante (lower) → Kanonischer Name."""
    rev: dict[str, str] = {}
    for canonical, variants in mapping.items():
        if not isinstance(variants, list):
            logger.warning("Mapping '%s' hat kein Listen-Format.", canonical)
            continue
        for v in variants:
            rev[str(v).lower()] = str(canonical)
    return rev


# Reverse-Mapping: Variante (lower) → kanonischer Name
REVERSE: dict[str, str] = _build_reverse(MAPPINGS)

PROTECTED_LOWER: list[str] = [t.lower() for t in PROTECTED]


def _compile_protected_regex(terms: list[str]) -> "re.Pattern[str]":
    """
    Kompiliert den Schutz-Regex sicher.
    Verhindert ReDoS durch Begrenzung der Pattern-Länge und Nutzung von re.escape.
    """
    if not terms:
        return re.compile(r"($^)", re.IGNORECASE)

    # Begrenze die Anzahl der Begriffe im Regex, um Kompilierungszeit und
    # Backtracking-Risiken zu minimieren.
    limit = 200
    selected_terms = terms[:limit]

    # re.escape verhindert, dass Sonderzeichen als Regex-Metazeichen interpretiert werden.
    # Die Wortgrenzen \b verhindern Teilwort-Matches.
    pattern = r"\b(" + "|".join(re.escape(t) for t in selected_terms) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


PROTECTED_RE = _compile_protected_regex(PROTECTED)

SAFE_REPLACEMENTS: dict[str, str] = {"k": "C", "°c": "°C", "°f": "°F"}


def _normalize_term(term: str) -> str:
    t = term.strip()
    low = t.lower()
    if low in SAFE_REPLACEMENTS:
        return SAFE_REPLACEMENTS[low]
    if low in PROTECTED_LOWER:
        return PROTECTED[PROTECTED_LOWER.index(low)]
    if low in REVERSE:
        return REVERSE[low]
    return t


# ---------------------------------------------------------------------------
#  Word-Frequency-Tracker (automatisches Mapping-Lernen)
# ---------------------------------------------------------------------------

try:
    # Lokaler Import für die Ausführung innerhalb des mcp_server Ordners
    from tracker import WordFrequencyTracker
except ImportError:
    from mcp_server.tracker import WordFrequencyTracker

TRACKER = WordFrequencyTracker(
    mappings=MAPPINGS,
    protected=PROTECTED,
    protected_phrases=PROTECTED_PHRASES,
    threshold=LEARNING_THRESHOLD,
    data_dir=ROOT_DIR,
)

def map_text(text: str) -> str:
    result: str = PROTECTED_RE.sub(lambda m: m.group(0), text)
    words: list[str] = result.split()
    normalized: list[str] = [_normalize_term(w) for w in words]
    return " ".join(normalized)


def summarize_text(text: str, max_sentences: int = 3) -> str:
    # Verwendung eines nicht-gierigen Splits, um potenzielle
    # Performance-Probleme bei extremen Texten zu vermeiden
    sentences: list[str] = [
        s.strip() for s in re.split(r"[.!?]+(?:\s+|$)", text.strip()) if s.strip()
    ]
    if not sentences:
        return ""
    if len(sentences) <= max_sentences:
        return ". ".join(sentences) + "."
    scored: list[tuple[float, str]] = []
    for i, s in enumerate(sentences):
        score: float = sum(1 for w in s.split() if w.lower() in REVERSE) + 0.0
        score += 1.0 / (i + 1)
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    top: list[str] = [s for _, s in scored[:max_sentences]]
    return ". ".join(top) + "."


def compare_versions(text1: str, text2: str) -> dict[str, Any]:
    """Vergleicht zwei Textversionen und liefert Diffs."""
    words1 = text1.split()
    words2 = text2.split()
    norm1: list[str] = [_normalize_term(w) for w in words1]
    norm2: list[str] = [_normalize_term(w) for w in words2]
    added: list[str] = [w for w in norm2 if w not in norm1]
    removed: list[str] = [w for w in norm1 if w not in norm2]
    unchanged: int = sum(1 for w in norm2 if w in norm1)
    return {
        "original_length": len(words1),
        "optimized_length": len(words2),
        "unchanged_words": unchanged,
        "added_words": added,
        "removed_words": removed,
        "text1_normalized": " ".join(norm1),
        "text2_normalized": " ".join(norm2),
    }


def extract_entities(text: str) -> list[str]:
    entities: set[str] = set()
    for term, variants in MAPPINGS.items():
        for v in variants:
            if re.search(r"\b" + re.escape(v) + r"\b", text, re.IGNORECASE):
                entities.add(term)
    return sorted(entities)


def detect_language(text: str) -> str:
    de_indicators = [
        "und",
        "der",
        "die",
        "das",
        "ist",
        "ein",
        "eine",
        "von",
        "mit"]
    en_indicators = ["and", "the", "is", "a", "an", "of", "with", "in", "to"]
    text_lower = text.lower()
    de_count = sum(1 for w in de_indicators if w in text_lower.split())
    en_count = sum(1 for w in en_indicators if w in text_lower.split())
    if de_count > en_count:
        return "de"
    if en_count > de_count:
        return "en"
    return "unknown"


def translate_text(text: str, target_lang: str = "") -> str:
    if not LLM_PROVIDER and not target_lang:
        return json.dumps({
            "error": "Übersetzung erfordert Google Translate.",
            "hinweis": "installiere deep-translator: pip install deep-translator"
        })
    try:
        from deep_translator import GoogleTranslator  # noqa: E402
        if not target_lang:
            lang = detect_language(text)
            target_lang = "en" if lang == "de" else "de"
        return GoogleTranslator(
            source="auto",
            target=target_lang).translate(text)
    except ImportError:
        return json.dumps({"error": "deep-translator nicht installiert."})
    except Exception as exc:
        return json.dumps({"error": f"Übersetzung fehlgeschlagen: {exc}"})


# ---------------------------------------------------------------------------
#  MCP-Server
# ---------------------------------------------------------------------------

mcp = FastMCP("Semantik MCP Server")


# ---------------------------------------------------------------------------
#  MCP-Prompt: Setup
# ---------------------------------------------------------------------------

@mcp.prompt()
def setup() -> str:
    """Setup-Assistent – Konfiguration des LLM-Providers."""
    if LLM_PROVIDER:
        return (
            f"Server ist bereits konfiguriert:\n"
            f"  Provider: {LLM_PROVIDER}\n"
            f"  Modell: {LLM_MODEL}\n"
            f"  URL: {LLM_BASE_URL}\n"
            f"Kein Setup nötig."
        )
    return (
        "Konfiguriere den Semantik MCP Server.\n\n"
        "Bitte rufe das Tool 'tool_configure' auf mit:\n"
        "  provider: ollama | lmstudio | openrouter\n"
        "  model: Modellname (z.B. llama3.2)\n"
        "  api_key: (nur bei OpenRouter erforderlich)\n"
        "  base_url: (optional, z.B. http://localhost:11434)\n\n"
        "Beispiele:\n"
        "  Ollama (lokal):     provider='ollama', model='llama3.2'\n"
        "  LM Studio (lokal):  provider='lmstudio', model='local-model'\n"
        "  OpenRouter (Cloud):  provider='openrouter', model='openai/gpt-4o-mini', api_key='sk-or-xxx'"
    )


# ---------------------------------------------------------------------------
#  MCP-Resource: Config-Status
# ---------------------------------------------------------------------------

@mcp.resource("config://status")
def config_status() -> str:
    """Zeigt den aktuellen Konfigurationsstatus des Servers."""
    status: dict[str, Any] = {
        "configured": bool(LLM_PROVIDER),
        "provider": LLM_PROVIDER or "(nicht gesetzt)",
        "model": LLM_MODEL or "(nicht gesetzt)",
        "base_url": LLM_BASE_URL or "(nicht gesetzt)",
        "api_key_set": bool(LLM_API_KEY),
        "auto_learn_mode": AUTO_LEARN_MODE,
        "learning_threshold": LEARNING_THRESHOLD,
    }
    return json.dumps(status, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
#  Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def tool_map_text(
    text: Annotated[str, Field(
        description="Der Eingabe-Text mit potenziell uneinheitlichen Fachbegriffen."
    )],
) -> str:
    """Normiert Fachbegriffe im Text auf die kanonische Form gemäß dem internen Mapping.

    Erkennt Varianten von Fachbegriffen und ersetzt sie durch den kanonischen Begriff.
    Gleichzeitig werden alle Wörter für das automatische Mapping-Lernen gezählt.
    """
    _validate_input(text)
    result = map_text(text)
    # Words für Tracking zählen
    TRACKER.record(text)
    return result


@mcp.tool()
def tool_summarize_text(
    text: Annotated[str, Field(
        description="Der vollständige Text, der zusammengefasst werden soll."
    )],
    max_sentences: Annotated[int, Field(
        description="Maximale Anzahl von Sätzen in der Zusammenfassung.", ge=1, le=20,
    )] = 3,
) -> str:
    """Fasst einen Text in wenigen Sätzen zusammen.

    Wählt die wichtigsten Sätze basierend auf Term-Häufigkeit und Satzposition.
    """
    _validate_input(text)
    TRACKER.record(text)
    return summarize_text(text, max_sentences)


@mcp.tool()
def tool_compare_versions(
    text1: Annotated[str, Field(description="Der Originaltext vor der Änderung.")],
    text2: Annotated[str, Field(description="Der überarbeitete oder optimierte Text.")],
) -> str:
    """Vergleicht zwei Textversionen und zeigt Added, Removed und Unchanged."""
    _validate_input(text1)
    _validate_input(text2)
    return json.dumps(
        compare_versions(
            text1,
            text2),
        ensure_ascii=False,
        indent=2)


@mcp.tool()
def tool_extract_entities(
    text: Annotated[str, Field(
        description="Der zu analysierende Text, aus dem Fachbegriffe extrahiert werden."
    )],
) -> str:
    """Extrahiert erkannte Fachbegriffe (Entities) aus dem Text."""
    _validate_input(text)
    TRACKER.record(text)
    return json.dumps(extract_entities(text), ensure_ascii=False)


@mcp.tool()
def tool_detect_language(
    text: Annotated[str, Field(
        description="Der zu analysierende Text (einzelner Satz oder Absatz)."
    )],
) -> str:
    """Erkennt die Sprache des Textes (de, en, oder unknown)."""
    return json.dumps({"language": detect_language(text)})


@mcp.tool()
def tool_get_mappings(
    category: Annotated[str, Field(
        description="Optionaler Filter: Zeigt nur Mappings die diesen Substring enthalten."
    )] = "",
) -> str:
    """Gibt alle verfügbaren Terminologiemappings als JSON zurück."""
    if category:
        filtered: dict[str, list[str]] = {
            k: v for k, v in MAPPINGS.items() if category.lower() in k.lower()
        }
        return json.dumps(filtered, ensure_ascii=False, indent=2)
    return json.dumps(MAPPINGS, ensure_ascii=False, indent=2)


@mcp.tool()
def tool_optimize_code(code: Annotated[str, Field(
        description="Der Quellcode-Text, der formatiert werden soll.")], ) -> str:
    """Optimiert Code-Text durch Konsistenz-Verbesserungen (Whitespace-Bereinigung) und
    stellt sicher, dass nur sichere AST‑Knoten verarbeitet werden.

    Der Code wird mit ``ast.parse`` geparst. Nur Knoten aus der Whitelist werden
    akzeptiert; bei einem Verstoß wird der Originalcode unverändert zurückgegeben.
    """
    import ast

    # -----------------------------------------------------------------------
    #  Whitelist erlaubter AST‑Knoten (sicheres Subset)
    # -----------------------------------------------------------------------
    _ALLOWED_NODES: "set[type[ast.AST]]" = {
        ast.Module,
        ast.Expr,
        ast.Assign,
        ast.AugAssign,
        ast.AnnAssign,
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Constant,
        ast.Call,
        ast.Attribute,
        ast.Subscript,
        ast.Slice,
        ast.List,
        ast.Tuple,
        ast.Dict,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.BoolOp,
        ast.If,
        ast.For,
        ast.While,
        ast.Return,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.arguments,
        ast.arg,
        ast.Import,
        ast.ImportFrom,
        ast.Pass,
        ast.Break,
        ast.Continue,
        ast.Raise,
        ast.Try,
        ast.ExceptHandler,
        ast.With,
        ast.AsyncWith,
        ast.Yield,
        ast.YieldFrom,
    }

    def _is_node_allowed(node: ast.AST) -> bool:
        """Rekursiver Check, ob ein Knoten in der Whitelist ist."""
        if type(node) not in _ALLOWED_NODES:
            return False
        for child in ast.iter_child_nodes(node):
            if not _is_node_allowed(child):
                return False
        return True

    # -----------------------------------------------------------------------
    #  1. Syntax‑Validierung (ast.parse) – gibt bei Fehlern den Originalcode zurück
    # -----------------------------------------------------------------------
    tree: ast.AST
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Syntaxfehler → unverändert zurückgeben (wie bisher)
        return code

    # -----------------------------------------------------------------------
    #  2. Whitelist‑Check – bei Verstoß Originalcode zurückgeben
    # -----------------------------------------------------------------------
    if not _is_node_allowed(tree):
        # Logge das Ereignis (ohne Details des schädlichen Codes)
        logger.warning(
            "tool_optimize_code: Verbotener AST‑Knoten entdeckt – Code unverändert.")
        return code

    # -----------------------------------------------------------------------
    #  3. Whitespace‑Optimierung (wie bisher)
    # -----------------------------------------------------------------------
    lines: list[str] = code.split("\n")
    optimized: list[str] = []
    for line in lines:
        stripped: str = line.rstrip()
        optimized.append(stripped if stripped else "")
    while optimized and optimized[-1] == "":
        optimized.pop()
    return "\n".join(optimized)


@mcp.tool()
def tool_translate_text(
    text: Annotated[str, Field(description="Der zu übersetzende Text.")],
    target_lang: Annotated[str, Field(
        description="Zielsprache als Sprachcode (z.B. 'de', 'en'). Standard: automatisch."
    )] = "",
) -> str:
    """Übersetzt Text in die angegebene Zielsprache via Google Translate."""
    _validate_input(text)
    return translate_text(text, target_lang)


# ---------------------------------------------------------------------------
#  Tools: LLM-Konfiguration & Setup
# ---------------------------------------------------------------------------

@mcp.tool()
def tool_configure(
    provider: Annotated[str, Field(
        description="LLM-Provider: 'ollama', 'lmstudio' oder 'openrouter'."
    )],
    model: Annotated[str, Field(
        description="Modellname (z.B. 'llama3.2', 'local-model', 'openai/gpt-4o-mini')."
    )],
    api_key: Annotated[str, Field(
        description="API-Key (nur bei OpenRouter erforderlich, sonst leer)."
    )] = "",
    base_url: Annotated[str, Field(
        description="Basis-URL des Providers (optional, wird automatisch gesetzt)."
    )] = "",
) -> str:
    """Konfiguriert den LLM-Provider. Speichert die Einstellungen in config.json.

    Wird typischerweise einmalig beim Setup aufgerufen.
    """
    # Validierung der Eingabewerte, um unvalidiertes Schreiben in config.json
    # zu verhindern
    allowed_providers = {"ollama", "lmstudio", "openrouter"}
    if provider not in allowed_providers:
        return json.dumps(
            {
                "status": "error",
                "message": f"Ungültiger Provider. Erlaubt sind: {
                    ', '.join(allowed_providers)}"},
            ensure_ascii=False)

    if len(model) > 100 or not model.strip():
        return json.dumps(
            {
                "status": "error",
                "message": "Ungültiger Modellname (leere Zeichenfolge oder zu lang)."},
            ensure_ascii=False)

    if len(api_key) > 1024:
        return json.dumps({"status": "error",
                           "message": "API-Key ist zu lang."},
                          ensure_ascii=False)

    # SSRF‑Schutz: Whitelist für erlaubte Domains/Hosts
    _ALLOWED_HOSTS: set[str] = {
        "localhost", "127.0.0.1", "::1", "openrouter.ai", "api.openrouter.ai"
    }

    if base_url:
        if len(base_url) > 255:
            return json.dumps({"status": "error", "message": "URL ist zu lang."}, ensure_ascii=False)
        if not base_url.startswith(("http://", "https://")):
            return json.dumps({"status": "error", "message": "URL muss mit http:// oder https:// beginnen."},
                              ensure_ascii=False)
        
        # Host‑Whitelist prüfen
        try:
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            host = parsed.hostname or ""
            if host not in _ALLOWED_HOSTS:
                return json.dumps({"status": "error", "message": "URL verweist auf nicht erlaubten Host."},
                                  ensure_ascii=False)
        except Exception:
            return json.dumps({"status": "error", "message": "URL-Format ungültig."},
                              ensure_ascii=False)

        # Nur zulässige Zeichen
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9:/._-]+", base_url):
            return json.dumps({"status": "error", "message": "URL enthält ungültige Zeichen."},
                              ensure_ascii=False)

    global LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY  # noqa: PLW0603

    # Standard-URLs
    default_urls: dict[str, str] = {
        "ollama": "http://localhost:11434",
        "lmstudio": "http://localhost:1234/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    }
    if not base_url:
        base_url = default_urls.get(provider, "")

    # Zuweisung an die (per Konvention Konstante) globalen Variablen –
    # bewusst nicht ``Final`` markiert, weil sie zur Laufzeit geändert werden.
    LLM_PROVIDER = provider  # type: ignore[misc]
    LLM_BASE_URL = base_url  # type: ignore[misc]
    LLM_MODEL = model  # type: ignore[misc]
    LLM_API_KEY = api_key  # type: ignore[misc]

    # In config.json speichern
    config_path = ROOT_DIR / "config.json"
    try:
        config: dict[str, Any] = {}
        if config_path.exists():
            with open(_safe_path(ROOT_DIR, config_path.name), encoding="utf-8") as fh:
                config = json.load(fh)

        config["llm"] = {
            "default_provider": provider,
            provider: {
                "base_url": base_url,
                "default_model": model,
                "api_key": secrets.encrypt(api_key),
            },
        }
        with open(_safe_path(ROOT_DIR, config_path.name), "w", encoding="utf-8") as fh:
            json.dump(config, fh, ensure_ascii=False, indent=2)

        return json.dumps({
            "status": "ok",
            "message": f"Konfiguriert: {provider} / {model}",
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key_set": bool(api_key),
        }, ensure_ascii=False, indent=2)
    except OSError as exc:
        return json.dumps({"status": "error",
                           "message": f"Fehler beim Speichern: {exc}"})


# ---------------------------------------------------------------------------
#  Tools: Automatisches Mapping-Lernen
# ---------------------------------------------------------------------------

@mcp.tool()
def tool_configure_tracker(
    max_tracked_words: Annotated[int, Field(
        description="Maximale Anzahl eindeutiger Wörter im Tracking (100-50000).",
        ge=100, le=50000,
    )] = 0,
    max_all_words: Annotated[int, Field(
        description="Maximale Anzahl Wörter im All-Words-Counter (100-100000).",
        ge=100, le=100000,
    )] = 0,
    max_bigrams: Annotated[int, Field(
        description="Maximale Anzahl Bigramme (100-50000).",
        ge=100, le=50000,
    )] = 0,
    threshold: Annotated[int, Field(
        description="Schwellenwert für Auto-Canonical (2-100).",
        ge=2, le=100,
    )] = 0,
) -> str:
    """Konfiguriert die Limits und Schwellen des Wort-Trackers zur Laufzeit.

    Alle Parameter sind optional – nur angegebene Werte werden geändert.
    Änderungen wirken sich sofort auf neue Aufrufe aus.
    """
    changes: list[str] = []
    if max_tracked_words > 0:
        old = TRACKER._MAX_TRACKED_WORDS
        TRACKER._MAX_TRACKED_WORDS = max_tracked_words
        changes.append(f"max_tracked_words: {old} → {max_tracked_words}")
    if max_all_words > 0:
        old = TRACKER._MAX_ALL_WORDS
        TRACKER._MAX_ALL_WORDS = max_all_words
        changes.append(f"max_all_words: {old} → {max_all_words}")
    if max_bigrams > 0:
        old = TRACKER._MAX_BIGRAMS
        TRACKER._MAX_BIGRAMS = max_bigrams
        changes.append(f"max_bigrams: {old} → {max_bigrams}")
    if threshold > 0:
        old = TRACKER._threshold
        TRACKER._threshold = threshold
        changes.append(f"threshold: {old} → {threshold}")

    if not changes:
        # Aktuelle Werte anzeigen
        return json.dumps({
            "max_tracked_words": TRACKER._MAX_TRACKED_WORDS,
            "max_all_words": TRACKER._MAX_ALL_WORDS,
            "max_bigrams": TRACKER._MAX_BIGRAMS,
            "threshold": TRACKER._threshold,
        }, ensure_ascii=False, indent=2)

    return json.dumps({
        "status": "ok",
        "changes": changes,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def tool_get_learning_stats() -> str:
    """Zeigt Statistiken über das automatische Mapping-Lernen.

    Enthält: Gesamtzahl erkannter Wörter, Kandidaten die die Schwelle
    erreicht haben, und die Top-20 häufigsten Wörter.
    """
    stats: dict[str, Any] = cast(dict[str, Any], TRACKER.get_stats())
    return json.dumps(stats, ensure_ascii=False, indent=2)


@mcp.tool()
def tool_approve_learning(
    word: Annotated[str, Field(
        description="Das zu bestätigende Wort (kleingeschrieben, z.B. 'kühlung')."
    )],
    canonical: Annotated[str, Field(
        description="Der kanonische Name (z.B. 'Kühlung')."
    )],
) -> str:
    """Bestätigt einen Mapping-Kandidaten und fügt ihn zu mappings.json hinzu.

    Rufe zuerst tool_get_learning_stats auf, um verfügbare Kandidaten zu sehen.
    """
    # Validierung der Eingabewerte, um unvalidiertes Schreiben in
    # mappings.json zu verhindern
    if not word or len(word) > 50 or not word.strip():
        return json.dumps({"status": "error",
                           "message": "Ungültiges Wort (leer oder zu lang)."},
                          ensure_ascii=False)
    if not canonical or len(canonical) > 100 or not canonical.strip():
        return json.dumps({"status": "error",
                           "message": "Ungültiger kanonischer Name (leer oder zu lang)."},
                          ensure_ascii=False)
    success = TRACKER.approve_candidate(word.lower(), canonical)
    if success:
        return json.dumps({
            "status": "ok",
            "message": f"'{word}' → '{canonical}' als Mapping hinzugefügt.",
        }, ensure_ascii=False)
    return json.dumps({
        "status": "error",
        "message": f"'{word}' ist kein bekannter Kandidat.",
        "hinweis": "Rufe tool_get_learning_stats auf, um verfügbare Kandidaten zu sehen.",
    })


# ---------------------------------------------------------------------------
#  Tools: Canonical & LLM-Dokumentation
# ---------------------------------------------------------------------------

@mcp.tool()
def tool_get_prompt_context(
    category: Annotated[str, Field(
        description="Optionaler Filter: Zeigt nur Mappings die diesen Substring enthalten."
    )] = "",
) -> str:
    """Gibt alle Canonical-Mappings als kompakte, token-optimierte Zeichenkette zurück.

    Format: *-ANL→Anleitung|*-MOD→Modell|**-MOFA→*-MOT+*-FAH
    Ideal als Prompt-Kontext für den LLM. Erspart bis zu 70% Tokens gegenüber JSON.
    """
    return TRACKER.get_prompt_context(category=category)


@mcp.tool()
def tool_save_llm_doku(
    path: Annotated[str, Field(
        description="Vollständiger Pfad zur llm-doku.md Datei (z.B. G:/Programmierung/MeinProjekt/llm-doku.md)."
    )],
    text: Annotated[str, Field(
        description="Der zu speichernde Dokumentationstext. Alle bekannten Canonicals werden automatisch angewendet."
    )],
    title: Annotated[str, Field(
        description="Optionaler Titel für die Doku (wird als Überschrift gesetzt)."
    )] = "",
) -> str:
    """Schreibt eine LLM-Dokumentationsdatei (llm-doku.md) in das angegebene Verzeichnis.

    Der Text wird automatisch mit Canonicals (*-PRÄFIX) komprimiert,
    um Tokens beim erneuten Lesen zu sparen. Bestehende Datei wird überschrieben.
    """
    _validate_input(text)
    doku_path = Path(path).resolve()

    # Sicherheitsprüfung: Pfad muss absolut sein
    if not doku_path.is_absolute():
        return json.dumps({
            "status": "error",
            "message": "Ungültiger Pfad. Nur absolute Pfade sind erlaubt."
        }, ensure_ascii=False)

    # Canonicals auf Text anwenden (mit Wortgrenzen, um Teilwort-Matches zu vermeiden)
    context = TRACKER.get_prompt_context()
    compressed_text = text
    for canonical_entry in context.split("|"):
        if "→" not in canonical_entry:
            continue
        can, word = canonical_entry.split("→", 1)
        # Nur ganze Wörter ersetzen, nicht Teilwörter (case-insensitive)
        escaped = re.escape(word)
        compressed_text = re.sub(rf"\b{escaped}\b", can, compressed_text, flags=re.IGNORECASE)

    if title:
        doku_content = f"# {title}\n\n{compressed_text}"
    else:
        doku_content = f"# LLM-Doku\n\n{compressed_text}"

    try:
        doku_path.parent.mkdir(parents=True, exist_ok=True)
        with open(doku_path, "w", encoding="utf-8") as fh:
            fh.write(doku_content)
        return json.dumps({
            "status": "ok",
            "message": f"Doku gespeichert: {doku_path}",
            "original_length": len(text),
            "compressed_length": len(compressed_text),
            "savings": len(text) - len(compressed_text),
        }, ensure_ascii=False, indent=2)
    except OSError as exc:
        return json.dumps({
            "status": "error",
            "message": f"Fehler beim Speichern: {exc}"
        }, ensure_ascii=False)


@mcp.tool()
def tool_expand_doku(
    path: Annotated[str, Field(
        description="Vollständiger Pfad zur llm-doku.md Datei."
    )],
    show_stats: Annotated[bool, Field(
        description="Bei true werden zusätzlich Token-Ersparnis und Canonical-Statistiken angezeigt."
    )] = False,
) -> str:
    """Liest eine llm-doku.md Datei und expandiert alle Canonicals zurück.

    Der LLM erhält den vollständigen Text, obwohl die Datei nur 30-40% der Tokens benötigt.
    """
    doku_path = Path(path).resolve()

    if not doku_path.exists():
        return json.dumps({
            "status": "error",
            "message": f"Datei nicht gefunden: {doku_path}"
        }, ensure_ascii=False)

    if not doku_path.is_file():
        return json.dumps({
            "status": "error",
            "message": "Pfad ist kein gültiger Dateipfad."
        }, ensure_ascii=False)

    try:
        content = doku_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return json.dumps({
            "status": "error",
            "message": f"Fehler beim Lesen: {exc}"
        }, ensure_ascii=False)

    expandierte_content = content
    expansions = []

    # Canonicals rückwärts expandieren (längste zuerst, um partielle Matches zu vermeiden)
    context = TRACKER.get_prompt_context()
    canonical_entries = sorted(
        context.split("|"),
        key=lambda x: len(x.split("→", 1)[0]) if "→" in x else 0,
        reverse=True,
    )

    for canonical_entry in canonical_entries:
        if "→" not in canonical_entry:
            continue
        can, word = canonical_entry.split("→", 1)
        if can in expandierte_content:
            count = expandierte_content.count(can)
            expandierte_content = expandierte_content.replace(can, word)
            expansions.append({can: word, "count": count})

    if show_stats:
        stats_content = "\n\n---\n## Expansions-Statistik\n"
        stats_content += f"Original-Länge: {len(content)} Zeichen\n"
        stats_content += f"Expandierte-Länge: {len(expandierte_content)} Zeichen\n"
        stats_content += f"Ersparnis: {len(content) - len(expandierte_content)} Zeichen\n"
        stats_content += f"Expandierte Canonicals: {len(expansions)}\n"
        for exp in expansions:
            for can, word in exp.items():
                stats_content += f"- {can} → {word} ({exp['count']}x)\n"
        expandierte_content += stats_content

    return expandierte_content


@mcp.tool()
def tool_config_llm_doku(
    path: Annotated[str, Field(
        description="Pfad zum Projektverzeichnis des Clients (z.B. G:/Programmierung/LLM Quota Checker)."
    )],
    filename: Annotated[str, Field(
        description="Dateiname der Doku (Standard: llm-doku.md)."
    )] = "llm-doku.md",
) -> str:
    """Konfiguriert den Pfad zur LLM-Dokumentationsdatei.

    Der MCP-Server merkt sich den Pfad und kann beim nächsten Start
    automatisch die Doku laden und expandieren.
    """
    project_path = Path(path).resolve()

    if not project_path.exists() or not project_path.is_dir():
        return json.dumps({
            "status": "error",
            "message": f"Verzeichnis nicht gefunden: {project_path}"
        }, ensure_ascii=False)

    if not filename.endswith(".md"):
        filename += ".md"

    doku_path = project_path / filename

    # In config.json speichern
    config_path = ROOT_DIR / "config.json"
    try:
        config: dict[str, Any] = {}
        if config_path.exists():
            with open(_safe_path(ROOT_DIR, config_path.name), encoding="utf-8") as fh:
                config = json.load(fh)

        config["llm_doku"] = {
            "path": str(doku_path),
            "enabled": True
        }

        with open(_safe_path(ROOT_DIR, config_path.name), "w", encoding="utf-8") as fh:
            json.dump(config, fh, ensure_ascii=False, indent=2)

        return json.dumps({
            "status": "ok",
            "message": f"LLM-Doku konfiguriert: {doku_path}",
            "path": str(doku_path),
        }, ensure_ascii=False, indent=2)
    except OSError as exc:
        return json.dumps({
            "status": "error",
            "message": f"Fehler beim Speichern: {exc}"
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
#  Server starten
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    finally:
        TRACKER.shutdown()
