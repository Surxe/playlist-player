"""Architecture guard: imports inside the package only ever point downward.

The layers, lowest first. A module may import from its own top-level unit or from a
strictly lower layer, never sideways into another unit on the same layer and never
upward. The overlay additionally must stay a pure API client: it may not reach into the
cache, the sources or the playback engine even though those sit below it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import playlist_player

PACKAGE = Path(playlist_player.__file__).parent

LAYERS: dict[str, int] = {
    "models": 0, "errors": 0, "options_schema": 0,
    "http": 1, "config": 1, "cache": 1, "lyrics": 1,
    "sources": 2, "playback": 2, "overlay_settings": 2,
    "sync": 3, "server": 3, "overlay": 3,
    "cli": 4, "__main__": 5,
}  # fmt: skip

OVERLAY_FORBIDDEN = {"cache", "sources", "playback", "sync", "server"}


def _unit(path: Path) -> str:
    rel = path.relative_to(PACKAGE)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _imported_units(path: Path) -> set[str]:
    """Top-level units of this package that ``path`` imports, by absolute or relative import."""
    rel_parts = path.relative_to(PACKAGE).parts
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(rel_parts[:-1])
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                target = base + (node.module.split(".") if node.module else [])
                if target:
                    found.add(target[0])
                else:  # "from . import x" at the package root names units directly
                    found.update(alias.name for alias in node.names)
            elif node.module and node.module.split(".")[0] == "playlist_player":
                parts = node.module.split(".")
                found.update([parts[1]] if len(parts) > 1 else [a.name for a in node.names])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "playlist_player" and len(parts) > 1:
                    found.add(parts[1])
    return found


def _modules() -> list[Path]:
    return sorted(
        p for p in PACKAGE.rglob("*.py") if p.name != "__init__.py" or p.parent != PACKAGE
    )


def test_every_unit_has_a_layer():
    units = {_unit(p) for p in _modules()} - {"__init__"}
    assert units <= set(LAYERS), f"add these to LAYERS: {sorted(units - set(LAYERS))}"


def test_imports_only_point_downward():
    violations = []
    for path in _modules():
        unit = _unit(path)
        if unit not in LAYERS:
            continue
        for target in _imported_units(path) - {unit}:
            if target not in LAYERS:
                continue  # e.g. "from . import __version__"
            if LAYERS[target] >= LAYERS[unit]:
                violations.append(f"{path.relative_to(PACKAGE)} ({unit}) imports {target}")
    assert not violations, "\n".join(violations)


def test_overlay_is_a_pure_api_client():
    violations = [
        f"{path.relative_to(PACKAGE)} imports {target}"
        for path in _modules()
        if _unit(path) == "overlay"
        for target in _imported_units(path) & OVERLAY_FORBIDDEN
    ]
    assert not violations, "\n".join(violations)


def test_the_guard_actually_detects_a_violation(tmp_path, monkeypatch):
    """A guard that cannot fail is worthless: plant an upward import and expect it caught."""
    fake = tmp_path / "pkg"
    (fake / "cache").mkdir(parents=True)
    (fake / "cache" / "bad.py").write_text(
        "from ..sync import pipeline\nfrom playlist_player.cli import main\n"
    )
    monkeypatch.setattr(f"{__name__}.PACKAGE", fake)
    assert _imported_units(fake / "cache" / "bad.py") == {"sync", "cli"}
