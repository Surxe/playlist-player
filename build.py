"""Regenerate the docs that are derived from the settings schema.

Run from the repo root after editing ``playlist_player/options_schema.py``::

    .venv/bin/python build.py

Reads ``[tool.optionsconfig]`` in ``pyproject.toml`` for the schema module and output
paths, then rewrites ``.env.example`` and the README's Configuration section (between the
BEGIN/END_GENERATED_OPTIONS markers).
"""

from __future__ import annotations

from pathlib import Path

from optionsconfig import EnvBuilder, ReadmeBuilder, logger

from playlist_player.options_schema import OPTIONS_SCHEMA

README = Path(__file__).parent / "README.md"


def _drop_flags_for_secrets() -> None:
    """Stop the README advertising a CLI flag for options that deliberately have none.

    OptionsConfig documents an ``arg`` for every option, but ``config.py`` never registers
    one for a ``sensitive`` option: a secret on a command line shows in the process list.
    OptionsConfig has no way to say "environment only", so correct its output here.
    """
    text = README.read_text(encoding="utf-8")
    for option in OPTIONS_SCHEMA.values():
        if option.get("sensitive"):
            text = text.replace(
                f"  - Command line: `{option['arg']}`",
                "  - Command line: none (environment only)",
            )
    README.write_text(text, encoding="utf-8")


def main() -> None:
    logger.remove()  # keep OptionsConfig's own debug logging out of the output
    EnvBuilder().build()
    ReadmeBuilder().build()
    _drop_flags_for_secrets()


if __name__ == "__main__":
    main()
