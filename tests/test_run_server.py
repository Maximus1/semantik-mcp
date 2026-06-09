"""
Tests für run_server.py des Semantik MCP Servers.

Testet:
  - Syntax-Validierung mit py_compile
  - Server-Start/Stop/Restart
  - Watchdog-Handler (Debounce, Datei-Filterung)
  - Fehlerbehandlung (Syntaxfehler → kein Neustart)
"""

from scripts.run_server import validate_syntax, ServerProcess, ServerFileHandler
import os
import sys
import py_compile
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_DIR = Path(__file__).parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


# ---------------------------------------------------------------------------
#  Tests: Syntax-Validierung
# ---------------------------------------------------------------------------


class TestValidateSyntax:
    """Tests für die Syntax-Validierung mit py_compile."""

    def test_gültiger_code(self, tmp_path: Path):
        """Gültiger Python-Code → (True, "")."""
        valid_file = tmp_path / "valid.py"
        valid_file.write_text("x = 1\ny = 2\n", encoding="utf-8")
        ok, error = validate_syntax(valid_file)
        assert ok is True
        assert error == ""

    def test_ungültiger_code(self, tmp_path: Path):
        """Ungültiger Python-Code → (False, Fehlermeldung)."""
        invalid_file = tmp_path / "invalid.py"
        invalid_file.write_text("def foo(\n  unvollständig", encoding="utf-8")
        ok, error = validate_syntax(invalid_file)
        assert ok is False
        assert "SyntaxError" in error or "Syntax" in error

    def test_leere_datei(self, tmp_path: Path):
        """Leere Datei ist gültiges Python → (True, "")."""
        empty_file = tmp_path / "empty.py"
        empty_file.write_text("", encoding="utf-8")
        ok, error = validate_syntax(empty_file)
        assert ok is True

    def test_mcp_server_main_py_ist_gültig(self):
        """Die echte mcp_server/main.py muss syntaktisch gültig sein."""
        server_path = PROJECT_DIR / "mcp_server" / "main.py"
        if server_path.exists():
            ok, error = validate_syntax(server_path)
            assert ok is True, f"mcp_server/main.py hat Syntaxfehler: {error}"

    def test_run_server_py_ist_gültig(self):
        """Die echte run_server.py muss syntaktisch gültig sein."""
        run_path = PROJECT_DIR / "scripts" / "run_server.py"
        if run_path.exists():
            ok, error = validate_syntax(run_path)
            assert ok is True, f"run_server.py hat Syntaxfehler: {error}"


# ---------------------------------------------------------------------------
#  Tests: ServerProcess
# ---------------------------------------------------------------------------


class TestServerProcess:
    """Tests für den Server-Prozess-Manager."""

    def test_init_nicht_laufend(self):
        """Neuer ServerProcess ist nicht laufend."""
        proc = ServerProcess()
        assert proc.is_running is False
        assert proc.pid is None

    def test_start_und_stop(self):
        """Server starten und sauber beenden."""
        proc = ServerProcess()
        # Starte einen einfachen Python-Prozess (nicht server.py, da stdio)
        proc._process = __import__("subprocess").Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"]
        )
        assert proc.is_running is True
        assert proc.pid is not None

        proc.stop()
        assert proc.is_running is False

    def test_restart(self):
        """Server wird neu gestartet (mit gemocktem Popen)."""
        proc = ServerProcess()

        # Mocke subprocess.Popen, da stdin in pytest nicht verfügbar ist
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Läuft noch
        mock_proc.pid = 1000
        mock_proc.wait.return_value = 0

        import subprocess
        proc._process = mock_proc
        proc._process.pid = 1000

        with patch.object(subprocess, "Popen") as mock_popen:
            new_mock = MagicMock()
            new_mock.pid = 2000
            new_mock.poll.return_value = None
            mock_popen.return_value = new_mock

            proc.restart()

            assert proc.pid == 2000
            assert proc.pid != 1000
            # Alte Prozess-Methode sollte aufgerufen worden sein
            mock_proc.terminate.assert_called_once()

    def test_stop_auf_nicht_gestarteten_prozess(self):
        """Stop auf nicht gestarteten Prozess → kein Fehler."""
        proc = ServerProcess()
        proc.stop()  # Sollte keinen Fehler werfen
        assert proc.is_running is False

    def test_stop_mit_bereits_beendetem_prozess(self):
        """Stop auf bereits beendeten Prozess → kein Fehler."""
        proc = ServerProcess()
        proc._process = __import__("subprocess").Popen(
            [sys.executable, "-c", "pass"]  # Beendet sofort
        )
        import time
        time.sleep(0.5)  # Warten bis beendet
        proc.stop()  # Sollte keinen Fehler werfen
        assert proc.is_running is False


# ---------------------------------------------------------------------------
#  Tests: Watchdog-Handler
# ---------------------------------------------------------------------------


class TestServerFileHandler:
    """Tests für den Watchdog-Datei-Handler."""

    def _make_handler(self, server: ServerProcess) -> ServerFileHandler:
        """Erstellt einen Handler mit debounced _last_trigger."""
        handler = ServerFileHandler(server)
        handler._last_trigger = 0.0  # Reset debounce
        return handler

    def test_ignoriert_verzeichnis_events(self):
        """Verzeichnisänderungen werden ignoriert."""
        proc = ServerProcess()
        handler = self._make_handler(proc)

        event = MagicMock()
        event.is_directory = True
        # Pfad zur echten mcp_server/main.py
        event.src_path = str(PROJECT_DIR / "mcp_server" / "main.py")

        # Sollte keinen Restart auslösen
        handler.on_modified(event)
        assert proc.is_running is False

    def test_ignoriert_fremde_dateien(self):
        """Änderungen an anderen Dateien werden ignoriert."""
        proc = ServerProcess()
        handler = self._make_handler(proc)

        event = MagicMock()
        event.is_directory = False
        event.src_path = str(PROJECT_DIR / "mcp_server" / "llm_providers.py")

        handler.on_modified(event)
        assert proc.is_running is False

    def test_syntaxfehler_verhindert_neustart(self):
        """Syntaxfehler in server.py verhindert Neustart."""
        proc = ServerProcess()
        handler = self._make_handler(proc)

        event = MagicMock()
        event.is_directory = False
        event.src_path = str(PROJECT_DIR / "mcp_server" / "main.py")

        # Mocke validate_syntax um einen Fehler zu simulieren
        with patch(
            "scripts.run_server.validate_syntax",
            return_value=(False, "SyntaxError: bad syntax"),
        ):
            handler.on_modified(event)

        # Server sollte NICHT gestartet/ge restarted worden sein
        assert proc.is_running is False

    def test_debounce_verhindert_doppelten_trigger(self):
        """Schnelle aufeinanderfolgende Events werden gedrosselt."""
        proc = ServerProcess()
        handler = self._make_handler(proc)

        event = MagicMock()
        event.is_directory = False
        event.src_path = str(PROJECT_DIR / "mcp_server" / "main.py")

        # Setze _last_trigger in die Zukunft (debounce aktiv)
        import time
        handler._last_trigger = time.time()  # Gerade eben getriggert

        handler.on_modified(event)
        # Sollte nicht nochmal triggern
        assert proc.is_running is False


# ---------------------------------------------------------------------------
#  Tests: Import/Modul-Validierung
# ---------------------------------------------------------------------------


class TestModuleImports:
    """Tests dass alle Module korrekt importiert werden können."""

    def test_import_mcp_server_main(self):
        """mcp_server/main.py lässt sich importieren."""
        from mcp_server import main as mcp_main
        assert mcp_main is not None

    def test_import_llm_providers(self):
        """mcp_server/llm_providers.py lässt sich importieren."""
        from mcp_server.llm_providers import OllamaProvider, OpenAICompatibleProvider, get_provider
        assert OllamaProvider is not None
        assert OpenAICompatibleProvider is not None
        assert get_provider is not None

    def test_import_run_server(self):
        """scripts/run_server.py lässt sich importieren."""
        from scripts.run_server import validate_syntax, ServerProcess, ServerFileHandler
        assert validate_syntax is not None
        assert ServerProcess is not None
        assert ServerFileHandler is not None
