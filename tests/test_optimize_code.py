"""
Tests für das optimize_code Tool des Semantik MCP Servers.

Testet die Whitespace-Optimierung und AST-Sicherheit gegen
die echte ``tool_optimize_code`` Funktion aus ``mcp_server/main.py``.

Die echte ``tool_optimize_code`` führt folgende Operationen durch:
- AST-Validierung (SyntaxError → Original)
- AST-Whitelist-Check (verbotene Knoten → Original)
- Whitespace-Bereinigung (rstrip + leere Zeilen am Ende)

Hinweis: Docstrings werden NICHT entfernt (das war ein Feature des
alten, gelöschten server.py).
"""

import ast
import sys
from pathlib import Path

import pytest

# Projektverzeichnis zum Pfad hinzufügen
PROJECT_DIR = Path(__file__).parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# Importiere die echte Tool-Funktion
from mcp_server.main import tool_optimize_code  # noqa: E402


# ---------------------------------------------------------------------------
#  Tests: Basis-Funktionalität
# ---------------------------------------------------------------------------


class TestOptimizeCodeBasic:
    """Tests für die Grundfunktionalität."""

    def test_einfacher_code_wird_optimiert(self):
        """Einfacher Code wird durch Whitespace-Bereinigung optimiert."""
        code = "x = 1   \ny = 2\t  \n"
        result = tool_optimize_code(code=code)
        # Trailing Whitespace sollte weg sein
        for line in result.splitlines():
            if line:
                assert line == line.rstrip(), f"Trailing WS in: {line!r}"

    def test_f_string_wird_beibehalten(self):
        """F-Strings werden nicht verändert."""
        code = '''\
name = "Welt"
msg = f"Hallo, {name}!"
print(msg)
'''
        result = tool_optimize_code(code=code)
        assert 'f"Hallo, {name}!"' in result

    def test_trailing_leerzeilen_am_ende_entfernt(self):
        """Leerzeilen am Ende des Codes werden entfernt."""
        code = "x = 1\n\n\n\n"  # Viele Leerzeilen am Ende
        result = tool_optimize_code(code=code)
        # Keine trailing leeren Zeilen
        assert not result.endswith("\n\n")

    def test_trailing_whitespace_pro_zeile_entfernt(self):
        """Trailing Whitespace pro Zeile wird entfernt."""
        code = "x = 1   \ny = 2\t  "
        result = tool_optimize_code(code=code)
        for line in result.splitlines():
            if line:
                assert line == line.rstrip()

    def test_leerer_code(self):
        """Leerer Code gibt leeren String zurück."""
        result = tool_optimize_code(code="")
        assert result == ""


# ---------------------------------------------------------------------------
#  Tests: Syntax-Validierung
# ---------------------------------------------------------------------------


class TestOptimizeCodeSyntaxValidation:
    """Tests für die Syntax-Validierung."""

    def test_gültiger_code_wird_akzeptiert(self):
        """Syntaktisch korrekter Code wird optimiert."""
        code = "x = 1\ny = 2\nz = x + y"
        result = tool_optimize_code(code=code)
        # Code sollte immer noch parsen
        ast.parse(result)

    def test_syntaxfehler_gibt_original_zurück(self):
        """Code mit Syntaxfehlern wird unverändert zurückgegeben."""
        code = "def foo(\n  unvollständig"
        result = tool_optimize_code(code=code)
        # Bei Syntaxfehler: Original zurückgeben
        assert result == code

    def test_output_ist_gültiges_python(self):
        """Output ist immer gültiges Python (oder Original bei Fehler)."""
        code = '''\
def addiere(a, b):
    """Addiert zwei Zahlen."""
    return a + b

x = addiere(1, 2)
'''
        result = tool_optimize_code(code=code)
        # Output sollte parsbar sein
        ast.parse(result)


# ---------------------------------------------------------------------------
#  Tests: AST-Whitelist-Sicherheit
# ---------------------------------------------------------------------------


class TestOptimizeCodeAstWhitelist:
    """Tests für die AST-Whitelist (verhindert gefährliche Konstrukte)."""

    def test_lambda_wird_abgelehnt(self):
        """Lambda-Expressions sind nicht in der Whitelist → Originalcode zurück."""
        code = "f = lambda x: x * 2"
        result = tool_optimize_code(code=code)
        # Lambda ist NICHT in _ALLOWED_NODES → Code wird unverändert zurückgegeben
        assert result == code

    def test_normaler_code_geht_durch(self):
        """Normaler Python-Code (assign, function, etc.) geht durch die Whitelist."""
        code = '''\
def foo(x):
    y = x + 1
    return y
'''
        result = tool_optimize_code(code=code)
        # Sollte nicht das Original sein, sondern optimiert
        assert "def foo(x):" in result
        assert "return y" in result

    def test_comprehension_nicht_in_whitelist(self):
        """List/Dict/Set Comprehensions sind nicht in der Whitelist."""
        # ListComp, DictComp, SetComp, GeneratorExp sind nicht erlaubt
        code = "x = [i for i in range(10)]"
        result = tool_optimize_code(code=code)
        # Comprehensions sind nicht in _ALLOWED_NODES → Original zurück
        assert result == code


# ---------------------------------------------------------------------------
#  Tests: Whitespace-Normalisierung
# ---------------------------------------------------------------------------


class TestOptimizeCodeWhitespace:
    """Tests für die Whitespace-Normalisierung."""

    def test_tabs_werden_beibehalten(self):
        """Tabs innerhalb einer Zeile werden beibehalten (nur trailing entfernt)."""
        code = "def f():\n\tx = 1"  # Tab + x
        result = tool_optimize_code(code=code)
        # Tabs am Zeilenanfang bleiben
        assert "\tx = 1" in result

    def test_zeilenanzahl_nicht_deutlich_reduziert(self):
        """Die Whitespace-Bereinigung soll Zeilen nicht radikal entfernen."""
        code = "x = 1\ny = 2\nz = 3"
        result = tool_optimize_code(code=code)
        # Alle 3 Zuweisungen sollten noch da sein
        assert "x = 1" in result
        assert "y = 2" in result
        assert "z = 3" in result


# ---------------------------------------------------------------------------
#  Tests: Klassen und komplexe Strukturen
# ---------------------------------------------------------------------------


class TestOptimizeCodeComplexStructures:
    """Tests für komplexere Code-Strukturen."""

    def test_klasse_mit_methoden(self):
        """Klassen mit Methoden bleiben funktional."""
        code = '''\
class Foo:
    def __init__(self, x):
        self.x = x

    def get(self):
        return self.x
'''
        result = tool_optimize_code(code=code)
        assert "class Foo:" in result
        assert "def __init__" in result
        assert "def get" in result

    def test_imports_bleiben_erhalten(self):
        """Imports bleiben erhalten."""
        code = '''\
import os
import sys
from pathlib import Path
'''
        result = tool_optimize_code(code=code)
        assert "import os" in result
        assert "import sys" in result
        assert "from pathlib import Path" in result
