# Semantik MCP Server – Token-Kompression für LLM-Dokumentation

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-kompatibel-green)](https://modelcontextprotocol.io)
[![Tests](https://img.shields.io/badge/tests-145%20passed-brightgreen)]()

---

## Warum? (Sinnstiftung)

### Das Problem

LLMs haben begrenzte Kontextfenster. Jedes Token kostet Geld und Speicher. Wenn ein LLM bei jedem Start seine gesamte Projektdokumentation neu laden muss, fressen lange Fachbegriffe und wiederkehrende Satzteile wertvollen Kontext – ohne Informationsgewinn.

**Beispiel:** Der Satz "Die Dokumentation beschreibt die Funktionsweise der Installation" enthält 68 Zeichen. Mit Canonicals: "Die *-DOKU beschreibt die *-FKT der *-INST" – nur 44 Zeichen. **35% weniger Tokens** – bei gleichem Informationsgehalt.

### Die Lösung

Dieser MCP-Server analysiert automatisch alle Texte, die durch ihn verarbeitet werden, erkennt häufige Wörter und Satzteile und ersetzt sie durch kurze Canonical-Platzhalter (`*-PRÄFIX`). Der LLM kann dieselbe Dokumentation mit 50-85% weniger Tokens lesen – und beim ersten Start expandieren wir die Canonicals zurück zur Vollform.

### Für wen?

- **LLM-Agenten** (Cline, Claude Desktop, Continue), die bei jedem Neustart Doku laden müssen
- **Prompt-Ingenieure**, die maximale Token-Effizienz aus ihren Prompt-Templates holen wollen
- **Teams**, die LLM-basierte Dokumentationssysteme betreiben und Token-Kosten sparen möchten

---

## Was? (Information)

### Die 16 Tools im Überblick

| Kategorie | Tool | Funktion |
|-----------|------|----------|
| **Textanalyse** | `tool_map_text` | Normiert Fachbegriffe, zählt Wörter für Lernen |
| | `tool_summarize_text` | Fasst Texte zusammen (Term-Häufigkeit) |
| | `tool_compare_versions` | Vergleicht zwei Textversionen |
| | `tool_extract_entities` | Extrahiert erkannte Fachbegriffe |
| | `tool_detect_language` | Erkennt Deutsch/Englisch |
| | `tool_translate_text` | Übersetzt via Google Translate |
| | `tool_optimize_code` | Formatiert Code sicher (AST-Whitelist) |
| **Mapping** | `tool_get_mappings` | Zeigt alle Terminologiemappings |
| | `tool_approve_learning` | Bestätigt Mapping-Kandidaten |
| | `tool_get_learning_stats` | Zeigt Wort-Frequenzen und Kandidaten |
| **Canonical** | `tool_get_prompt_context` | Gibt kompakte Canonical-Zeichenkette |
| | `tool_save_llm_doku` | Schreibt Doku mit Canonical-Kompression |
| | `tool_expand_doku` | Expandiert Canonicals zurück zur Vollform |
| **Konfiguration** | `tool_configure` | LLM-Provider einrichten |
| | `tool_configure_tracker` | Limits und Schwellen zur Laufzeit anpassen |
| | `tool_config_llm_doku` | Pfad zur LLM-Dokumentation konfigurieren |

### Die 3 Kompressionsebenen

```
Ebene 1: Einzelwort           *-DOKUMENTATION → Dokumentation    (spart ~60%)
Ebene 2: Satzteil             *-ZUMBEISPIEL  → zum Beispiel      (spart ~65%)
Ebene 3: Zusammensetzung      **-MOFA        → *-MOT+*-FAH       (spart ~75%)
```

### Architektur

```
┌─────────────────────────────────────────────────────────┐
│                   MCP-Client (Cline/Claude/VSCode)       │
└────────────────────────┬────────────────────────────────┘
                         │ Stdio (JSON-RPC)
┌────────────────────────▼────────────────────────────────┐
│                   Semantik MCP Server                    │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ 15 Tools  │  │   Tracker    │  │  Canonical Engine  │  │
│  │ (MCP API) │──│ (Zählung +   │──│ (*-PRÄFIX-Gen.)    │  │
│  │           │  │  Auto-Canon.)│  │                    │  │
│  └──────────┘  └──────┬───────┘  └───────────────────┘  │
│                        │                                 │
│               ┌───────▼────────┐                         │
│               │  Idle-Persistenz│                         │
│               │  (30s, mappings.json)│                    │
│               └────────────────┘                          │
└──────────────────────────────────────────────────────────┘
```

### Sicherheitsarchitektur

| Schutz | Mechanismus |
|--------|------------|
| **Memory-DoS** | Wort-Limits: 10.000/20.000/5.000 (einstellbar) |
| **ReDoS** | `re.escape()` + Längenbegrenzung auf 200 Patterns |
| **SSRF** | Host-Whitelist (localhost, openrouter.ai) |
| **Path Traversal** | `is_relative_to()` + `is_absolute()` |
| **AST-Injection** | Whitelist erlaubter AST-Knoten |
| **Thread-Safety** | `threading.RLock` für alle Zugriffe |
| **Canonical-Kollision** | Maximal 999 Versuche (keine Endlosschleife) |

---

## Wie? (Anwendung)

### Installation

```bash
# Mit uv (empfohlen)
uv sync

# Oder mit pip
pip install -r requirements.txt
```

### MCP-Konfiguration (einmalig)

Füge diesen Block in deine MCP-Client-Konfiguration ein:

```json
{
  "mcpServers": {
    "semantik": {
      "command": "uv",
      "args": ["run", "--directory", "G:/Programmierung/semantik MCP", "mcp_server/main.py"],
      "env": {
        "LLM_PROVIDER": "ollama",
        "LLM_BASE_URL": "http://localhost:11434",
        "LLM_MODEL": "llama3.2",
        "AUTO_LEARN_MODE": "approve",
        "LEARNING_THRESHOLD": "5"
      }
    }
  }
}
```

### Erste Schritte

**1. Provider konfigurieren** (einmalig):
```
tool_configure(provider="ollama", model="llama3.2")
```

**2. Texte analysieren lassen** – jeder Aufruf zählt automatisch Wörter:
```
tool_map_text(text="Die Dokumentation beschreibt die Funktionsweise der Installation")
```

**3. Auto-Canonical läuft im Hintergrund** – nach 5 Vorkommen eines Wortes generiert der Server automatisch ein `*-PRÄFIX`-Mapping:
```
→ *-DOKU für "Dokumentation"
→ *-FKT für "Funktionsweise"
→ *-INST für "Installation"
```

**4. Prompt-Kontext abrufen** – kompakte Zeichenkette für deinen LLM-Prompt:
```
tool_get_prompt_context()
→ *-DOKU→Dokumentation|*-FKT→Funktionsweise|*-INST→Installation
```

**5. LLM-Doku speichern** – mit Canonical-Kompression:
```
tool_save_llm_doku(
    path="G:/Programmierung/MeinProjekt/llm-doku.md",
    text="Die Dokumentation beschreibt die Funktionsweise der Installation."
)
→ Ersparnis: 35%
```

**6. Doku beim Neustart expandieren:**
```
tool_expand_doku(path="G:/Programmierung/MeinProjekt/llm-doku.md")
→ "Die Dokumentation beschreibt die Funktionsweise der Installation."
```

### Limits anpassen

Standardwerte für die meisten Fälle ausreichend. Anpassung nur bei Bedarf:

```json
tool_configure_tracker(
    threshold=10,       // Canonical erst ab 10 Vorkommen
    max_all_words=50000 // Mehr Wörter im Tracking
)
```

### Entwickler-Runner mit Hot-Reload

```bash
python scripts/run_server.py
```

### Tests ausführen

```bash
pytest                    # 145 Tests
pytest --cov=mcp_server   # Mit Coverage
```

---

## Was wäre, wenn? (Adaption)

### Erweiterungsmöglichkeiten

**1. Eigene Domänen-Mappings einspielen:**  
Lege in `protected_terms.json` Fachbegriffe fest, die nie ersetzt werden:
```json
{
  "terms": ["def", "class", "import"],
  "phrases": ["zum Beispiel", "in der Regel"]
}
```

**2. Blacklist für Canonicals erweitern:**  
Füge in `protected_terms.json` unter `"phrases"` Satzteile hinzu, die als Canonical zu ungenau wären.

**3. Doku aus anderen Tools generieren:**  
Nutze die MCP-Resource `config://status` um den Server-Zustand in deine Doku einzubinden.

### Best Practices

| Situation | Empfehlung |
|-----------|-----------|
| **Viele kurze Wörter** | Threshold erhöhen (z.B. 10) |
| **Wenige lange Fachbegriffe** | Threshold senken (z.B. 3) |
| **Server läuft dauerhaft** | Limits großzügig (50k/100k) |
| **Server startet oft neu** | `save_llm_doku` + `expand_doku` Workflow |
| **API-Key-Sicherheit** | `SEMANTIK_MASTER_KEY` statt Datei |

### Bekannte Grenzen

- **Canonicals nur für Wörter ≥ 4 Buchstaben** – kürzere Wörter sparen nichts
- **Maximal 999 Kollisionen pro Prefix** – bei extrem vielen ähnlichen Wörtern wird abgebrochen
- **Bigramm-Canonicals nur wenn beide Einzelwörter Canonicals haben** – kein direktes Mapping ohne Vorstufe
- **Kein automatisches Rate-Limiting** – für SaaS-Betrieb bitte einen API-Gateway vorschalten

### Migration

Das Format der `mappings.json` ist abwärtskompatibel:
- Bestehende Mappings (`"Kühlung": ["kühlung", ...]`) bleiben unverändert
- Canonical-Mappings werden als zusätzliche Einträge hinzugefügt (`"*-KÜHL": ["kühlung"]`)
- Beim nächsten Start werden beide Formate geladen

---

**Lizenz:** MIT  
**Repository:** [github.com/Maximus1/semantik-mcp](https://github.com/Maximus1/semantik-mcp)