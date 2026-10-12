"""The overlay's import discipline, which its correctness on screen depends on.

``libgtk4-layer-shell`` only works if it is loaded before ``libwayland-client``, i.e.
before anything imports ``gi``. So: one module may import ``gi``, the entry module loads
the library before importing anything else from the package, and nothing else that the
CLI or the tests import may pull ``gi`` in by accident.
"""

from __future__ import annotations

import ast
import subprocess
import sys

import playlist_player
from playlist_player.config import REPO_ROOT

PACKAGE = __import__("pathlib").Path(playlist_player.__file__).parent
GTK_MODULE = PACKAGE / "overlay" / "render" / "gtk_layer.py"


def _imports_gi(path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "gi" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "gi":
            return True
    return False


def test_only_the_gtk_renderer_imports_gi():
    offenders = [
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if path != GTK_MODULE and _imports_gi(path)
    ]
    assert not offenders and _imports_gi(GTK_MODULE)


def test_the_entry_module_loads_the_layer_shell_library_before_any_package_import():
    tree = ast.parse((PACKAGE / "overlay" / "main.py").read_text(encoding="utf-8"))
    body = [n for n in tree.body if not isinstance(n, ast.Expr)]  # skip the docstring
    names = []
    for node in body:
        if isinstance(node, ast.ImportFrom):
            names.append(("from", "." * node.level + (node.module or "")))
        elif isinstance(node, ast.Import):
            names.append(("import", node.names[0].name))
        elif isinstance(node, ast.Try):
            names.append(("load", ast.unparse(node.body[0])))
            break
    assert names[-1][0] == "load" and "libgtk4-layer-shell.so.0" in names[-1][1]
    # Everything before the load is the standard library; nothing from this package.
    assert all(kind == "import" or not name.startswith(".") for kind, name in names[:-1])
    assert ("import", "ctypes") in names


def test_nothing_but_the_renderer_pulls_gi_into_the_process():
    code = (
        "import sys\n"
        "import playlist_player.cli\n"
        "import playlist_player.overlay.main\n"
        "import playlist_player.overlay.client\n"
        "import playlist_player.overlay.presenter\n"
        "import playlist_player.overlay.render.base\n"
        "import playlist_player.server.app\n"
        "print('gi' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
