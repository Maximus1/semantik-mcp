# Semantik MCP Server

Ein MCP-Server für semantische Textanalyse mit automatischem Mapping-Lernen und **Token-Kompression via Canonicals**.

Stellt 15 Tools bereit, die über jeden MCP-fähigen Client (Claude Desktop, Cline, VS Code, etc.) nutzbar sind.

---

## Ersteinrichtung

Beim ersten Start im MCP-Client wird nach dem LLM-Provider gefragt.

### Option 1: MCP-Konfiguration (env-Block)

```json
{
  "mcpServers": {
    "semantik": {
      "command": "python",
      "args": ["g:/Programmierung/semantik MCP/mcp_server/main.py"],
      "env": {
        "LLM_PROVIDER": "ollama",
        "LLM_BASE_URL": "http://localhost:11434",
        "LLM_MODEL": "llama3.2",
        "LLM_API_KEY": "",
        "AUTO_LEARN_MODE": "approve",
        "LEARNING_THRESHOLD": "5"
      }
    }
  }
}
```

### Option 2: Setup-Prompt im Agenten

Der Agent ruft den `setup`-Prompt auf → zeigt Konfigurationsanleitung → Nutzer ruft `tool_configure` auf.

### Option 3: tool_configure direkt aufrufen

```
tool_configure(provider="ollama", model="llama3.2")
```

Die Konfiguration wird in `config.json` gespeichert und beim nächsten Start automatisch geladen (einmalig).

---

## Tools (15)

| Tool | Funktion | Beschreibung |
|------|----------|--------------|
| `tool_map_text` | Normierung | Ersetzt Fachbegriff-Varianten + zählt Wörter für Lernen |
| `tool_summarize_text` | Zusammenfassen | Wählt die wichtigsten Sätze basierend auf Term-Häufigkeit |
| `tool_compare_versions` | Vergleich | Zeigt Added/Removed/Unchanged zwischen zwei Textversionen |
| `tool_extract_entities` | Extraktion | Listet alle erkannten Fachbegriffe im Text auf |
| `tool_detect_language` | Spracherkennung | Erkennt Deutsch/Englisch anhand von Indikator-Wörtern |
| `tool_get_mappings` | Inspektion | Zeigt alle verfügbaren Terminologiemappings |
| `tool_optimize_code` | Code-Formatierung | Entfernt Trailing-Whitespace und normalisiert Zeilenenden |
| `tool_translate_text` | Übersetzung | Übersetzt Text via Google Translate |
| `tool_configure` | Setup | Konfiguriert LLM-Provider einmalig (speichert in config.json) |
| `tool_get_learning_stats` | Lernen | Zeigt Wort-Häufigkeiten und Mapping-Kandidaten |
| `tool_approve_learning` | Lernen | Bestätigt einen Kandidaten als neues Mapping |
| `tool_get_prompt_context` | **Canonical** | Gibt kompakte Canonical-Zeichenkette für LLM-Prompt |
| `tool_save_llm_doku` | **Doku** | Schreibt LLM-Dokumentation mit Canonical-Kompression |
| `tool_expand_doku` | **Doku** | Expandiert Canonicals aus LLM-Doku zurück zur Vollform |
| `tool_config_llm_doku` | **Doku** | Konfiguriert Pfad zur LLM-Dokumentationsdatei |

---

## Canonical-Kompressionssystem

Das Herzstück des Servers: **Automatische Token-Kompression durch Canonicals**.

Ein Canonical ist eine Kurzzeichenkette, die ein langes Wort oder Satzteil im Prompt ersetzt. Der LLM kann die Doku komprimiert lesen und beim ersten Start expandieren – das spart **50-85% Tokens**.

### Die 3 Kompressionsebenen

| Ebene | Marker | Beispiel | Ersparnis |
|-------|--------|----------|-----------|
| 1 – Einzelwort | `*-PRÄFIX` | `*-DOKU→Dokumentation` | bis 70% |
| 2 – Satzteil | `*-PRÄFIX` | `*-ZB→zum Beispiel` | bis 80% |
| 3 – Zusammensetzung | `**-PRÄFIX` | `**-MOFA→*-MOT+*-FAH` | bis 85% |

**Beispiel für eine token-optimierte Doku:**
```
# LLM-Doku – Semantik MCP
*-DOKU ist *-D1 *-TE, *-WLC *-FKT *-LLM *-A1 *-C.
```
Statt:
```
# LLM-Doku – Semantik MCP
Dokumentation ist die erste Aufgabe, welche die Funktionsweise des LLMs an einem Beispielcode zeigt.
```

### Auto-Canonical-Workflow

1. **Zählung:** Jeder `tool_map_text`-Aufruf zählt **alle** Wörter (auch bekannte)
2. **Schwelle:** Ab `LEARNING_THRESHOLD` (Standard: 5) Vorkommen prüft der Server:
   - Ist das Wort ≥ 4 Buchstaben?
   - Ist das Canonical kürzer als das Wort?
   - Ist das Wort nicht in der Blacklist (`protected_terms.json`)?
3. **Canonical-Generierung:** Automatisch → `*-PRÄFIX` (bei Kollision: `*-PRÄFIX1`, `*-PRÄFIX2`, ...)
4. **Bigramm-Erkennung:** Häufige Wortpaare → `**-PRÄFIX`
5. **Idle-Persistenz:** Nach 30s ohne Aktivität schreibt der Server die neuen Mappings in `mappings.json`

### Blacklist (geschützte Begriffe)

`protected_terms.json` schützt Wörter und Satzteile vor Canonical-Ersetzung:

```json
{
  "terms": ["def", "class", "import"],
  "phrases": ["zum Beispiel", "das heißt"]
}
```

---

## LLM-Dokumentation

Der Server kann eine token-optimierte LLM-Dokumentation verwalten:

| Tool | Funktion |
|------|----------|
| `tool_config_llm_doku(path)` | Konfiguriert Pfad zur `llm-doku.md` im Projektverzeichnis |
| `tool_save_llm_doku(path, text)` | Schreibt Doku + komprimiert mit Canonicals |
| `tool_expand_doku(path)` | Expandiert alle Canonicals → Volltext für den LLM |

**Workflow:**
```
LLM schreibt Doku → tool_save_llm_doku() komprimiert mit Canonicals
↓
Beim Neustart → tool_expand_doku() expandiert Canonicals → LLM hat Volltext
↓
Ersparnis: 50-85% weniger Tokens beim Speichern/Laden
```

---

## Sicherheit

Die Anwendung beinhaltet Schutzmechanismen gegen:

- **Path Traversal**: `_safe_path()` prüft alle Pfadzugriffe; `path.is_absolute()` für externe Pfade
- **ReDoS**: Regex-Pattern werden mit `re.escape()` und Längenbegrenzung geschützt
- **Unsichere AST-Ausführung**: `tool_optimize_code` nutzt eine AST-Whitelist
- **SSRF**: `tool_configure` nutzt Host-Whitelist + URL-Validierung
- **Memory-DoS**: Wort-Tracking auf **10.000** begrenzt, All-Words auf **20.000**, Bigramme auf **5.000**
- **Input-Länge**: Alle Texteingaben sind auf **100.000** Zeichen begrenzt
- **Canonical-Kollision**: Maximal **999** Versuche, dann Abbruch (keine Endlosschleife)
- **Thread-Sicherheit**: Alle Zugriffe auf den Tracker sind durch `threading.RLock` geschützt

### Sicherheits-Hinweise

- Die Datei `secret.key` (automatisch erzeugt) liegt im Projektverzeichnis und
  ist in `.gitignore` enthalten. Setze stattdessen `SEMANTIK_MASTER_KEY` als
  Umgebungsvariable für Produktionsumgebungen.
- `tool_configure` erlaubt nur bestimmte Hosts (localhost, 127.0.0.1, openrouter.ai)
  um SSRF-Angriffe zu verhindern.

---

## Installation

```bash
# Mit uv (empfohlen)
uv sync

# Oder mit pip
pip install -r requirements.txt
```

### Abhängigkeiten

- `mcp[cli]>=1.0.0` – MCP-Server-Framework
- `requests>=2.31.0` – HTTP-Client
- `deep-translator>=1.11.0` – Google Translate Integration

---

## Starten

### MCP-Server (Stdio)

```bash
python mcp_server/main.py
# oder
uv run mcp_server/main.py
```

### Entwickler-Runner mit Hot-Reload

```bash
python scripts/run_server.py
```

### Einbindung in MCP-Clients (Cline, Continue, Claude Desktop, VS Code)

```json
{
  "mcpServers": {
    "semantik": {
      "command": "uv",
      "args": ["run", "mcp_server/main.py"],
      "env": {
        "LLM_PROVIDER": "ollama",
        "LLM_BASE_URL": "http://localhost:11434",
        "LLM_MODEL": "llama3.2"
      }
    }
  }
}
```

Voraussetzung: Laufender LLM-Provider (Ollama, LM Studio oder OpenRouter),
konfiguriert in `config.json` oder via Env-Variablen.

---

## Automatisches Mapping-Lernen

Der Server verfügt über einen eingebauten **Word-Frequency-Tracker**:

1. **Zählung:** Bei jedem `tool_map_text` oder `tool_extract_entities` Aufruf werden alle Wörter gezählt
2. **Schwelle:** Ab `LEARNING_THRESHOLD` (Standard: 5) Vorkommen wird ein Wort zum Kandidaten
3. **Auto-Canonical:** Der Server generiert automatisch `*-PRÄFIX`-Mappings für häufige Wörter
4. **Persistenz:** Bei Leerlauf (>30s ohne Aktivität) werden neue Mappings in `mappings.json` geschrieben (niedrige CPU/GPU-Last)

### MCP-Prompt: `setup`

Zeigt den Konfigurationsstatus oder gibt Setup-Anweisungen aus.

### MCP-Resource: `config://status`

Zeigt den aktuellen Konfigurationsstatus als JSON.

---

## Konfiguration

### Env-Variablen (MCP-Konfiguration)

| Env-Variable | Beschreibung | Beispiel |
|-------------|-------------|----------|
| `LLM_PROVIDER` | Provider | `ollama`, `lmstudio`, `openrouter` |
| `LLM_BASE_URL` | Basis-URL | `http://localhost:11434` |
| `LLM_MODEL` | Modellname | `llama3.2` |
| `LLM_API_KEY` | API-Key | `sk-or-xxx` (nur OpenRouter) |
| `AUTO_LEARN_MODE` | Lern-Modus | `approve` oder `auto` |
| `LEARNING_THRESHOLD` | Schwelle | `5` |

### Ersetzungstabellen (`mappings.json`)

Normale Mappings:
```json
{
  "Kühlung": ["Kühlung", "Kuehlung", "kühlung", "KÜHLUNG", "Kühlgerät"]
}
```

Canonical-Mappings (auto-generiert):
```json
{
  "*-DOKU": ["dokumentation"],
  "*-ANL": ["anleitung"],
  "**-MOFA": ["motorrad fahren"]
}
```

**Format:** `{ "Canonical_oder_Name": ["Variante1", ...] }`

### LLM-Provider (`config.json`)

```json
{
  "llm": {
    "default_provider": "ollama",
    "ollama": {
      "base_url": "http://localhost:11434",
      "default_model": "llama3.2"
    }
  }
}
```

### Geschützte Begriffe (`protected_terms.json`)

Liste von Identifier-Namen und Satzteilen, die nicht durch Canonicals ersetzt werden:

```json
{
  "terms": ["def", "class", "import"],
  "phrases": ["zum Beispiel", "das heißt"]
}
```

---

## Projektstruktur

```
semantik MCP/
├── mcp_server/
│   ├── __init__.py            # Paket-Marker
│   ├── main.py                # FastMCP-Server (15 Tools + Prompt + Resource)
│   ├── llm_providers.py       # LLM-Provider-Abstraktion
│   └── tracker.py             # WordFrequencyTracker + Canonical-Generierung
├── scripts/
│   └── run_server.py          # Dev-Hot-Reload-Tool
├── mappings.json              # Terminologiemapping + Canonicals
├── protected_terms.json       # Geschützte Identifier + Satzteile
├── config.json                # LLM-Provider-Konfiguration
├── pyproject.toml             # Paket-Definition (uv)
├── README.md                  # Diese Datei
└── tests/
    ├── conftest.py
    ├── test_map_text.py
    ├── test_optimize_code.py
    ├── test_summarize_text.py
    ├── test_llm_providers.py
    ├── test_run_server.py
    ├── test_ssrf_protection.py
    └── test_tracker.py
```

---

## Tests

```bash
# Alle Tests ausführen
pytest

# Mit Coverage
pytest --cov=mcp_server
```

**Test-Statistik:** 145 Tests (davon 54 speziell für das Canonical-System)

---

## Architektur

### mcp_server/main.py (aktueller Server)

- **Transport:** Stdio (JSON-RPC über stdin/stdout)
- **Framework:** FastMCP mit Pydantic-Validierung
- **Tools:** 15 MCP-konforme Tools mit `Annotated[Type, Field(...)]`-Signaturen
- **Setup:** MCP-Prompt `setup` + MCP-Resource `config://status` + `tool_configure`
- **Canonical:** 4 neue Tools für Token-Kompression und LLM-Doku
- **Lernen:** Integrierter WordFrequencyTracker mit idle-basierter Persistenz
- **Dependencies:** `mcp[cli]`, `deep-translator`

### mcp_server/tracker.py (Word-Frequency-Tracker)

- **In-Memory-Datenbank:** Zählt Wortvorkommen über alle Tool-Aufrufe
- **All-Words-Counter:** Zählt **alle** Wörter (auch bekannte) für Auto-Canonical
- **Bigramm-Tracking:** Erkennt häufige Wortpaare für zusammengesetzte Canonicals
- **Canonical-Generierung:** Auto `*-PRÄFIX` für Wörter ≥ 5 Vorkommen + Token-Ersparnis
- **Kollisionsmanagement:** Index-basiert (`*-MOD`, `*-MOD1`, `*-MOD2`, ...)
- **Prompt-Kontext:** Kompakte Zeichenkette `*-ANL→Anleitung|*-MOD→Modell`
- **Idle-Persistenz:** 30s Timeout, `threading.Timer`, niedrige CPU/GPU-Last
- **Speicher-Limits:** Wörter 10.000, All-Words 20.000, Bigramme 5.000
- **Thread-Sicherheit:** `threading.RLock` für alle Zugriffe

## Spracherkennung

- **Deutsch:** "und", "der", "die", "das", "ist", "ein", "eine", "von", "mit"
- **Englisch:** "and", "the", "is", "a", "an", "of", "with", "in", "to"