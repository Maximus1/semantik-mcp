"""
Semantik MCP Server – Entwickler-Runner mit Hot-Reload
======================================================

Dieses Skript startet den MCP-Server (server.py) als Subprozess
und überwacht Änderungen an server.py via watchdog.

Bei einer Dateiänderung:
  1. wird die Syntax mit ``python -m py_compile server.py`` validiert
  2. bei Erfolg: Server-Prozess wird beendet und neu gestartet
  3. bei Fehler: Server läuft weiter, Fehlermeldung wird angezeigt

Start:
    python run_server.py

Abhängigkeiten:
    pip install watchdog
"""

import logging
import py_compile
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Import watchdog optional – wenn nicht installiert, wird Hot‑Reload deaktiviert
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:  # pragma: no cover
    # Fallback, wenn watchdog nicht verfügbar ist
    Observer = None  # type: ignore
    FileSystemEventHandler = object  # type: ignore
    HAS_WATCHDOG = False

# ---------------------------------------------------------------------------
#  Logger (nach stderr, damit stdout für MCP-Protokoll frei bleibt)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [run_server] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("run-server")

# ---------------------------------------------------------------------------
#  Konstanten
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = PROJECT_ROOT / "mcp_server" / "main.py"
DEBOUNCE_SECONDS = 0.5  # Verhindert mehrfache Triggers bei schnellen Änderungen


# ---------------------------------------------------------------------------
#  Server-Prozess-Manager
# ---------------------------------------------------------------------------

class ServerProcess:
    """Verwaltet den Server-Subprozess (Start, Stopp, Neustart)."""

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen[bytes]] = None

    @property
    def is_running(self) -> bool:
        """True wenn der Prozess aktiv ist."""
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> Optional[int]:
        """PID des laufenden Prozesses oder None."""
        return self._process.pid if self._process else None

    @property
    def returncode(self) -> Optional[int]:
        """Exit-Code des beendeten Prozesses oder None."""
        return self._process.returncode if self._process else None

    def start(self) -> subprocess.Popen[bytes]:
        """
        Startet den Server-Subprozess.

        Stdin/Stdout/Stderr werden vom Parent geerbt, damit der
        MCP-Client (Stdio-Transport) mit dem Server kommunizieren kann.
        """
        self._process = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, str(SERVER_SCRIPT)],
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        logger.info("Server gestartet (PID: %d)", self._process.pid)
        return self._process

    def stop(self) -> None:
        """Beendet den Server-Subprozess sauber (SIGTERM → SIGKILL)."""
        if self._process is None:
            return

        if self._process.poll() is None:
            # Prozess läuft noch → sauber beenden
            try:
                if sys.platform == "win32":
                    # Windows: terminate() sendet SIGTERM-Äquivalent
                    self._process.terminate()
                else:
                    self._process.send_signal(signal.SIGTERM)
                self._process.wait(timeout=5)
                logger.info("Server beendet (PID: %d)", self._process.pid)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Server reagierte nicht auf SIGTERM → erzwinge Kill (PID: %d)",
                    self._process.pid,
                )
                self._process.kill()
                self._process.wait()
                logger.info("Server gekillt (PID: %d)", self._process.pid)
            except OSError as exc:
                logger.error("Fehler beim Beenden des Servers: %s", exc)

        self._process = None

    def restart(self) -> Optional[subprocess.Popen[bytes]]:
        """Beendet und startet den Server neu."""
        self.stop()
        return self.start()


# ---------------------------------------------------------------------------
#  Syntax-Validierung
# ---------------------------------------------------------------------------

def validate_syntax(path: Path) -> tuple[bool, str]:
    """
    Validiert die Syntax einer Python-Datei mit py_compile.

    Args:
        path: Pfad zur .py-Datei.

    Returns:
        Tupel (ist_gültig, Fehlermeldung). Bei Erfolg: (True, "").
    """
    try:
        py_compile.compile(str(path), doraise=True)
        return True, ""
    except py_compile.PyCompileError as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
#  Watchdog-Handler
# ---------------------------------------------------------------------------

class ServerFileHandler(FileSystemEventHandler):
    """Reagiert auf Änderungen an server.py und startet bei Bedarf neu."""

    def __init__(self, server: ServerProcess) -> None:
        super().__init__()
        self.server = server
        self._last_trigger: float = 0.0

    def on_modified(self, event) -> None:  # noqa: ANN001
        """Wird bei Dateiänderungen aufgerufen."""
        if event.is_directory:
            return

        # Auf Änderungen im mcp_server Ordner reagieren
        if Path(event.src_path).resolve() != SERVER_SCRIPT.resolve():
            return

        # Debounce: Verhindert mehrfache Auslösung bei schnellen Änderungen
        now = time.time()
        if now - self._last_trigger < DEBOUNCE_SECONDS:
            return
        self._last_trigger = now

        logger.info("Änderung erkannt in %s", SERVER_SCRIPT.name)

        valid, error = validate_syntax(SERVER_SCRIPT)
        if valid:
            logger.info("Syntax OK → Server wird neugestartet...")
            self.server.restart()
            logger.info("Server neu gestartet (PID: %d)", self.server.pid)
        else:
            logger.error(
                "SYNTAXFEHLER – Server wird NICHT neugestartet:\n%s", error
            )

    def on_created(self, event) -> None:  # noqa: ANN001
        """Reagiert auf Erstellung (z.B. nach Löschen)."""
        self.on_modified(event)


# ---------------------------------------------------------------------------
#  Hauptprogramm
# ---------------------------------------------------------------------------

def main() -> None:
    """Startet den Server mit Watchdog-Hot-Reload."""

    # 1. Syntax vor dem ersten Start validieren
    valid, error = validate_syntax(SERVER_SCRIPT)
    if not valid:
        logger.error(
            "FATALER FEHLER: Syntaxfehler in %s:\n%s\n"
            "Server wird nicht gestartet.",
            SERVER_SCRIPT.name,
            error,
        )
        sys.exit(1)

    # 2. Server-Prozess erstellen
    server = ServerProcess()

    # 3. Watchdog-Observer (optional, wenn watchdog installiert ist)
    observer: Optional[Observer] = None
    if HAS_WATCHDOG:
        handler = ServerFileHandler(server)
        observer = Observer()
        observer.schedule(handler, str(PROJECT_ROOT), recursive=False)
        observer.start()
        logger.info("Watchdog aktiv – überwache %s", SERVER_SCRIPT.name)
    else:
        logger.warning(
            "watchdog nicht installiert. Hot-Reload ist deaktiviert. "
            "Installieren mit: pip install watchdog"
        )

    # 4. Server zum ersten Mal starten
    server.start()

    # 5. Hauptschleife: Auf Server-Crash wachen und ggf. neu starten
    try:
        while True:
            time.sleep(1)
            if not server.is_running:
                exit_code = server.returncode if server.returncode is not None else "unbekannt"
                logger.warning(
                    "Server-Prozess beendet (Exit-Code: %s). "
                    "Starte in 2 Sekunden neu...",
                    exit_code,
                )
                time.sleep(2)
                # Syntax erneut prüfen vor Neustart
                valid, error = validate_syntax(SERVER_SCRIPT)
                if valid:
                    server.start()
                else:
                    logger.error(
                        "Syntaxfehler nach Crash – warte auf Dateiänderung.\n%s", error, )
                    # Warte bis sich die Datei ändert (Watchdog übernimmt)
                    while not server.is_running:
                        time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Strg+C empfangen – Server wird beendet...")
    finally:
        server.stop()
        if observer:
            observer.stop()
            observer.join(timeout=5)
        logger.info("Run-Server beendet.")


if __name__ == "__main__":
    main()
