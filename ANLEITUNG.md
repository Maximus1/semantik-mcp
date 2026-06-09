# Semantik MCP Server – Vollständige Anleitung

---

## Warum? (Sinnstiftung)

### Das Problem: Token-Verschwendung bei LLMs

Jeder LLM (ChatGPT, Claude, etc.) hat ein begrenztes **Kontextfenster**. Das ist der Speicher, in den die gesamte Konversation passt – System-Prompt, Dokumentation, Chat-Verlauf, alles zusammen.

**Die Rechnung:**
- 1 Token ≈ ¾ Wort (bei englischen Texten)
- 1 Token ≈ ½ Wort (bei deutschen Texten, wegen Umlaute)
- GPT-4: 128.000 Tokens ≈ ~64.000 deutsche Wörter
- Claude: 200.000 Tokens ≈ ~100.000 deutsche Wörter

**Das Problem:** Wenn dein LLM bei jedem Start seine gesamte Projektdokumentation laden muss, fressen **wiederkehrende Wörter** wertvollen Kontext:

```
"Dokumentation" = 13 Zeichen = ~7 Tokens
"Dokumentation" (5x im Text) = 35 Tokens
```

**Mit Canonicals:**
```
*-DOKU = 6 Zeichen = ~3 Tokens
*-DOKU (5x im Text) = 15 Tokens
Ersparnis: 20 Tokens = 57% weniger
```

### Die Lösung: Automatische Token-Kompression

Dieser MCP-Server:
1. **Zählt** automatisch alle Wörter, die durch ihn laufen
2. **Erkennt** ab 5 Vorkommen ein häufiges Wort
3. **Generiert** ein `*-PRÄFIX`-Mapping (z.B. `*-DOKU→Dokumentation`)
4. **Ersetzt** in zukünftigen Dokumentationen das Wort durch das Prefix
5. **Expandiert** beim nächsten LLM-Start die Canonicals zurück

**Ergebnis:** Dein LLM hat den gleichen Informationsgehalt, aber mit 50-85% weniger Tokens.

### Für wen ist das?

| Nutzer | Nutzen |
|--------|--------|
| **Cline/Claude Desktop Nutzer** | Doku wird komprimiert geladen → mehr Platz für Chat |
| **Prompt-Ingenieure** | System-Prompts mit weniger Tokens → mehr Platz für Beispiele |
| **Teams mit mehreren Agents** | Gleiche `mappings.json` → konsistente Terminologie |
| **OpenRouter-Nutzer** | Weniger Tokens = weniger Kosten |

---

## Was? (Information)

### Die 16 Tools im Detail

#### Textanalyse (7 Tools)

| Tool | Eingabe | Ausgabe | Wann nutzen? |
|------|---------|---------|-------------|
| `tool_map_text` | Text | Normierter Text | Vor jeder Textverarbeitung |
| `tool_summarize_text` | Text, max_sentences | Kurzfassung | Bei langen Dokumenten |
| `tool_compare_versions` | Text1, Text2 | Diffs | Bei Textänderungen |
| `tool_extract_entities` | Text | Entity-Liste | Bei Fachbegriffen |
| `tool_detect_language` | Text | "de"/"en"/"unknown" | Vor Übersetzungen |
| `tool_translate_text` | Text, target_lang | Übersetzter Text | Bei Sprachaufgaben |
| `tool_optimize_code` | Python-Code | Formatierter Code | Bei Code-Formatierung |

#### Mapping & Lernen (3 Tools)

| Tool | Eingabe | Ausgabe | Wann nutzen? |
|------|---------|---------|-------------|
| `tool_get_mappings` | category (optional) | JSON-Dictionary | Bei Bedarf |
| `tool_get_learning_stats` | - | Statistiken | Nach 10+ Aufrufen |
| `tool_approve_learning` | word, canonical | Status | Bei Kandidaten |

#### Canonical & Doku (4 Tools)

| Tool | Eingabe | Ausgabe | Wann nutzen? |
|------|---------|---------|-------------|
| `tool_get_prompt_context` | category (optional) | Kompakte Zeichenkette | Vor LLM-Start |
| `tool_save_llm_doku` | path, text, title | Status | Bei Doku-Änderungen |
| `tool_expand_doku` | path, show_stats | Expandierter Text | Bei jedem Start |
| `tool_config_llm_doku` | path, filename | Status | Einmalig |

#### Konfiguration (2 Tools)

| Tool | Eingabe | Ausgabe | Wann nutzen? |
|------|---------|---------|-------------|
| `tool_configure` | provider, model, api_key, base_url | Status | Einmalig beim Setup |
| `tool_configure_tracker` | max_tracked_words, max_all_words, max_bigrams, threshold | Status | Bei Bedarf |

### Die 3 Kompressionsebenen

```
Ebene 1: Einzelwort
  *-DOKUMENTATION → Dokumentation    (spart ~60%)
  *-INSTALLATION  → Installation     (spart ~55%)
  *-KONFIGURATION → Konfiguration    (spart ~50%)

Ebene 2: Satzteil
  *-ZUMBEISPIEL   → zum Beispiel     (spart ~65%)
  - Definition    → zur Definition   (spart ~60%)

Ebene 3: Zusammensetzung (Bigramme)
  **-MOFA         → *-MOT + *-FAH   (Motorrad fahren, spart ~75%)
  **-DOKUANL      → *-DOKU + *-ANL  (Dokumentation Anleitung, spart ~70%)
```

### Architektur im Detail

```
┌─────────────────────────────────────────────────────────────┐
│                     MCP-Client (Cline)                       │
│                                                              │
│  User schreibt: "Fasse dieses Dokument zusammen"            │
│                                                              │
│  Cline denkt: "Ich nutze tool_summarize_text"               │
│  Cline ruft:  tool_summarize_text(text="...")                │
└────────────────────────┬────────────────────────────────────┘
                         │ JSON-RPC über Stdio
┌────────────────────────▼────────────────────────────────────┐
│                    Semantik MCP Server                        │
│                                                              │
│  1. tool_summarize_text(text) wird ausgeführt                │
│  2. tracker.record(text) zählt ALLE Wörter                   │
│  3. Ab 5 Vorkommen → _auto_approve() generiert Canonical    │
│  4. Nach 30s Idle → _persist_now() schreibt mappings.json   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Word-Frequency-Tracker                                │   │
│  │                                                       │   │
│  │ _counts:        unbekannte Wörter → Kandidaten       │   │
│  │ _all_counts:    ALLE Wörter → Auto-Canonical         │   │
│  │ _bigram_counts: Wortpaare → Bigram-Canonical         │   │
│  │ _canonical_pool: Canonical → Wort (Kollisionsschutz) │   │
│  │                                                       │   │
│  │ Limits:                                               │   │
│  │   _MAX_TRACKED_WORDS = 10.000                        │   │
│  │   _MAX_ALL_WORDS     = 20.000                        │   │
│  │   _MAX_BIGRAMS       = 5.000                         │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Canonical Engine                                      │   │
│  │                                                       │   │
│  │ _generate_canonical(word):                            │   │
│  │   1. Wort ≥ 4 Buchstaben? → Ja/Nein                  │   │
│  │   2. Prefix: Ersten 5 Buchstaben groß                │   │
│  │   3. Ersparnis: Wort > Canonical? → Ja/Nein          │   │
│  │   4. Kollision: Prüfe _canonical_pool                │   │
│  │   5. Bei Kollision: Index dranhängen (*-PRÄFIX1)     │   │
│  │                                                       │   │
│  │ _generate_bigram_canonical(bigram):                   │   │
│  │   1. Prüfe ob beide Wörter eigene Canonicals haben   │   │
│  │   2. Bigram-Canonical: **- + 2+2 Buchstaben          │   │
│  │   3. Kollision: Index dranhängen                     │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Persistenz                                            │   │
│  │                                                       │   │
│  │ _schedule_persist():                                  │   │
│  │   → threading.Timer(30s, _persist_if_idle)            │   │
│  │                                                       │   │
│  │ _persist_now():                                       │   │
│  │   → JSON.dump(mappings, "mappings.json")              │   │
│  │                                                       │   │
│  │ shutdown():                                           │   │
│  │   → cancel alle Timer                                 │   │
│  │   → _persist_now() wenn dirty                         │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### Sicherheitsarchitektur im Detail

| Schutz | Mechanismus | Warum? |
|--------|------------|--------|
| **Memory-DoS** | Wort-Limits: 10k/20k/5k | Verhindert Speicherüberlauf bei langen Sitzungen |
| **ReDoS** | `re.escape()` + 200 Patterns max | Verhindert Regex-Backtracking-Angriffe |
| **SSRF** | Host-Whitelist (localhost, openrouter.ai) | Verhindert Zugriff auf interne Netze |
| **Path Traversal** | `is_relative_to()` + `is_absolute()` | Verhindert Zugriff auf Dateien außerhalb des Projekts |
| **AST-Injection** | Whitelist erlaubter AST-Knoten | Verhindert Ausführung von schädlichem Code |
| **Thread-Safety** | `threading.RLock` für alle Zugriffe | Verhindert Race-Conditions bei parallelen Aufrufen |
| **Canonical-Kollision** | Max 999 Versuche | Verhindert Endlosschleife bei zu vielen Kollisionen |
| **Input-Länge** | 100.000 Zeichen max | Verhindert DoS durch extrem lange Texte |

---

## Wie? (Anwendung)

### Schritt 1: Installation

```bash
# Ins Projektverzeichnis wechseln
cd "g:\Programmierung\semantik MCP"

# Abhängigkeiten installieren
uv sync

# Oder mit pip
pip install -r requirements.txt
```

### Schritt 2: Cline-Konfiguration

Öffne die Cline-Einstellungen:
`File → Preferences → Settings → Extensions → Cline → Edit in settings.json`

Füge diesen Block in `cline_mcp_settings.json` hinzu:

```json
{
  "mcpServers": {
    "semantik": {
      "autoApprove": [
        "tool_map_text", "tool_summarize_text", "tool_compare_versions",
        "tool_extract_entities", "tool_detect_language", "tool_get_mappings",
        "tool_optimize_code", "tool_translate_text", "tool_configure",
        "tool_configure_tracker", "tool_get_learning_stats", "tool_approve_learning",
        "tool_get_prompt_context", "tool_save_llm_doku", "tool_expand_doku",
        "tool_config_llm_doku"
      ],
      "disabled": false,
      "timeout": 60,
      "command": "uv",
      "args": ["run", "--directory", "G:/Programmierung/semantik MCP", "mcp_server/main.py"],
      "env": {
        "LLM_PROVIDER": "openrouter",
        "LLM_BASE_URL": "https://openrouter.ai/api/v1",
        "LLM_MODEL": "openai/gpt-oss-120b:free",
        "LLM_API_KEY": "DEIN_OPENROUTER_API_KEY"
      },
      "type": "stdio"
    }
  }
}
```

**Wichtig:** Ersetze `DEIN_OPENROUTER_API_KEY` durch deinen persönlichen API-Key.
Den Key erhältst du unter [https://openrouter.ai/keys](https://openrouter.ai/keys).

**Alternative Provider:**

```json
// Ollama (lokal, kostenlos)
"env": {
  "LLM_PROVIDER": "ollama",
  "LLM_BASE_URL": "http://localhost:11434",
  "LLM_MODEL": "llama3.2"
}

// LM Studio (lokal, kostenlos)
"env": {
  "LLM_PROVIDER": "lmstudio",
  "LLM_BASE_URL": "http://localhost:1234/v1",
  "LLM_MODEL": "local-model"
}
```

### Schritt 3: VS Code neu starten

`Ctrl+Shift+P` → "Developer: Reload Window"

### Schritt 4: Cline-Regeln aktivieren

Die `.clinerules`-Datei wurde bereits erstellt. Cline liest sie automatisch und nutzt die MCP-Tools bei jeder Textverarbeitung.

### Schritt 5: Server testen

Schreib einfach an Cline:
> "Fasse dieses Dokument zusammen: [Text einfügen]"

Cline wird automatisch `tool_summarize_text` aufrufen.

---

### Detaillierte Tool-Beispiele

#### Beispiel 1: Text normalisieren

```
Eingabe: "Die Kühlung und die kühlung funktionieren beide."
Aufruf: tool_map_text(text="Die Kühlung und die kühlung funktionieren beide.")
Ausgabe: "Die Kühlung und die Kühlung funktionieren beide."
```

#### Beispiel 2: Zusammenfassen

```
Eingabe: "Langer Text mit 10 Sätzen..."
Aufruf: tool_summarize_text(text="...", max_sentences=3)
Ausgabe: "Die 3 wichtigsten Sätze..."
```

#### Beispiel 3: Canonicals abrufen

```
Aufruf: tool_get_prompt_context()
Ausgabe: "*-DOKU→Dokumentation|*-FKT→Funktionsweise|*-INST→Installation"
```

#### Beispiel 4: LLM-Doku speichern

```
Aufruf: tool_save_llm_doku(
    path="G:/Programmierung/MeinProjekt/llm-doku.md",
    text="Die Dokumentation beschreibt die Funktionsweise der Installation.",
    title="Mein Projekt"
)
Ausgabe: {
    "status": "ok",
    "original_length": 75,
    "compressed_length": 52,
    "savings": 23
}
```

#### Beispiel 5: Doku expandieren

```
Aufruf: tool_expand_doku(path="G:/Programmierung/MeinProjekt/llm-doku.md")
Ausgabe: "Die Dokumentation beschreibt die Funktionsweise der Installation."
```

#### Beispiel 6: Limits anpassen

```
Aufruf: tool_configure_tracker(threshold=10, max_all_words=50000)
Ausgabe: {
    "status": "ok",
    "changes": ["threshold: 5 → 10", "max_all_words: 20000 → 50000"]
}
```

---

### Automatischer Workflow

```
Tag 1:   Cline analysiert deine Texte → Server zählt Wörter
Tag 2:   Mehr Texte → Server zählt weiter
Tag 3:   "Dokumentation" hat 5 Vorkommen → Auto-Canonical generiert
Tag 4+:  Ab jetzt spart jeder LLM-Aufruf Tokens

Beim nächsten LLM-Start:
  1. tool_expand_doku() lädt komprimierte Doku
  2. Canonicals werden expandiert → LLM hat Volltext
  3. Ersparnis: 50-85% weniger Tokens
```

---

### Konfigurationsoptionen

#### Env-Variablen (in der MCP-Konfiguration)

| Variable | Beschreibung | Standard |
|----------|-------------|----------|
| `LLM_PROVIDER` | Provider | (leer) |
| `LLM_BASE_URL` | Basis-URL | (leer) |
| `LLM_MODEL` | Modellname | (leer) |
| `LLM_API_KEY` | API-Key | (leer) |
| `AUTO_LEARN_MODE` | Lern-Modus | "approve" |
| `LEARNING_THRESHOLD` | Schwelle | "5" |

#### Tracker-Limits (über `tool_configure_tracker`)

| Parameter | Beschreibung | Standard | Minimum | Maximum |
|-----------|-------------|----------|---------|---------|
| `max_tracked_words` | Max. eindeutige Wörter | 10.000 | 100 | 50.000 |
| `max_all_words` | Max. Wörter im All-Counter | 20.000 | 100 | 100.000 |
| `max_bigrams` | Max. Bigramme | 5.000 | 100 | 50.000 |
| `threshold` | Schwelle für Auto-Canonical | 5 | 2 | 100 |

#### Dateien

| Datei | Beschreibung | Wann ändern? |
|-------|-------------|-------------|
| `mappings.json` | Terminologiemappings + Canonicals | Nie manuell (automatisch) |
| `protected_terms.json` | Geschützte Begriffe | Bei Bedarf |
| `config.json` | LLM-Konfiguration | Nie manuell (über Tools) |
| `.clinerules` | Cline-Verhaltensregeln | Nie ändern |

---

## Was wäre, wenn? (Adaption)

### Szenario 1: "Ich habe eine neue Domäne mit Fachbegriffen"

**Lösung:** Füge die Fachbegriffe in `protected_terms.json` hinzu, damit sie nicht durch Canonicals ersetzt werden:

```json
{
  "terms": ["def", "class", "import", "Druck", "Temperatur"],
  "phrases": ["in der Regel", "zum Beispiel", "im Gegensatz dazu"]
}
```

### Szenario 2: "Mein LLM nutzt zu viele Tokens"

**Lösung:** Erhöhe den Threshold, damit nur wirklich häufige Wörter Canonicals bekommen:

```
tool_configure_tracker(threshold=10)
```

### Szenario 3: "Ich möchte die Stats sehen"

**Lösung:** Rufe die Statistiken auf:

```
tool_get_learning_stats()
```

### Szenario 4: "Ich möchte einen Kandidaten bestätigen"

**Lösung:** Prüfe die Stats und bestätige dann:

```
tool_get_learning_stats()  → Zeigt Kandidaten
tool_approve_learning(word="kühlung", canonical="Kühlung")
```

### Szenario 5: "Ich möchte die Doku für ein anderes Projekt nutzen"

**Lösung:** Konfiguriere den Pfad:

```
tool_config_llm_doku(path="G:/Programmierung/MeinProjekt")
```

### Szenario 6: "Der Server ist zu langsam"

**Lösung:** Reduziere die Limits:

```
tool_configure_tracker(max_all_words=5000, max_bigrams=1000)
```

### Szenario 7: "Ich möchte den Server debuggen"

**Lösung:** Starte den Server manuell:

```bash
cd "g:\Programmierung\semantik MCP"
python mcp_server/main.py
```

Der Server lauscht auf Stdio. Du kannst dann mit `curl` testen.

### Szenario 8: "Ich möchte den Server als Service betreiben"

**Lösung:** Nutze `supervisor` oder `systemd`:

```ini
# /etc/supervisor/conf.d/semantik-mcp.conf
[program:semantik-mcp]
command=uv run --directory /path/to/semantik-mcp mcp_server/main.py
directory=/path/to/semantik-mcp
autostart=true
autorestart=true
stderr_logfile=/var/log/semantik-mcp.err.log
stdout_logfile=/var/log/semantik-mcp.out.log
```

### Szenario 9: "Ich möchte mehrere Agents mit gleichem Wissensstand"

**Lösung:** Teile die `mappings.json` zwischen allen Agents:

```bash
# Auf allen Rechnern:
cp mappings.json /shared/path/mappings.json
```

Dann in der MCP-Konfiguration:
```json
"args": ["run", "--directory", "/shared/path/semantik-mcp", "mcp_server/main.py"]
```

### Szenario 10: "Ich möchte die Canonicals manuell überprüfen"

**Lösung:** Rufe `tool_get_prompt_context()` auf und prüfe die Ausgabe:

```
tool_get_prompt_context()
→ "*-DOKU→Dokumentation|*-FKT→Funktionsweise"
```

Wenn ein Canonical nicht gefällt, kannst du es in `mappings.json` löschen.

---

### Bekannte Grenzen

| Grenze | Grund | Workaround |
|--------|-------|-----------|
| **Canonicals nur für Wörter ≥ 4 Buchstaben** | Kürzere Wörter sparen nichts | Manuell in `protected_terms.json` schützen |
| **Maximal 999 Kollisionen pro Prefix** | Verhindert Endlosschleife | Threshold erhöhen |
| **Bigramme nur wenn beide Einzelwörter Canonicals haben** | Kein direktes Mapping | Erst Einzelwörter trainieren |
| **Kein automatisches Rate-Limiting** | Für SaaS-Betrieb | API-Gateway vorschalten |
| **Nur Python-Code optimieren** | AST-Whitelist | Andere Sprachen manuell formatieren |
| **Keine persistenten Canonicals bei Neustart** | Designsentscheidung | `tool_save_llm_doku` nutzen |

---

### Migration

Das Format der `mappings.json` ist abwärtskompatibel:

**Bestehende Mappings (manuell):**
```json
{
  "Kühlung": ["Kühlung", "Kuehlung", "kühlung"]
}
```

**Neue Canonical-Mappings (automatisch):**
```json
{
  "Kühlung": ["Kühlung", "Kuehlung", "kühlung"],
  "*-KÜHL": ["kühlung"]
}
```

Beim nächsten Start werden beide Formate geladen.

---

### Häufige Fragen

**Q: Was passiert mit meinen bestehenden Mappings?**
A: Nichts. Canonicals werden als zusätzliche Einträge hinzugefügt.

**Q: Kann ich Canonicals wieder löschen?**
A: Ja, lösche den Eintrag in `mappings.json`. Beim nächsten Start wird er nicht wieder generiert.

**Q: Wie lange lernt der Server?**
A: Abhängig vom Threshold (Standard: 5). Bei 10 Texten pro Tag ≈ 1 Tag.

**Q: Was passiert, wenn der Server abstürzt?**
A: Die `mappings.json` bleibt erhalten. Beim Neustart wird der Lernstand wiederhergestellt.

**Q: Kann ich den Server auf mehreren Rechnern nutzen?**
A: Ja, teile die `mappings.json` über eine gemeinsame Dateifreigabe.

**Q: Wie prüfe ich, ob der Server läuft?**
A: Rufe `tool_get_learning_stats()` auf. Wenn Stats kommen, läuft der Server.

---

**Repository:** [github.com/Maximus1/semantik-mcp](https://github.com/Maximus1/semantik-mcp)
**Lizenz:** MIT