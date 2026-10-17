"""Regenerate the docs that are derived from the settings schema.

Run from the repo root after editing ``spotifixed/options_schema.py``::

    .venv/bin/python build.py

Reads ``[tool.optionsconfig]`` in ``pyproject.toml`` for the schema module and output
paths, then rewrites ``.env.example`` and the Configuration section of ``docs/setup.md``
(between the BEGIN/END_GENERATED_OPTIONS markers).
"""

from __future__ import annotations

from optionsconfig import EnvBuilder, ReadmeBuilder, logger


def main() -> None:
    logger.remove()  # keep OptionsConfig's own debug logging out of the output
    EnvBuilder().build()
    ReadmeBuilder().build()


if __name__ == "__main__":
    main()
