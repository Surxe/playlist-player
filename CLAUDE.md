# spotifixed

Plays a cached Spotify playlist through mpv, with a remote-control page on localhost and a
lyric overlay drawn above fullscreen windows. The README is the front page; `docs/` explains
every command, setting and file (`setup.md` to install, sync and configure, `usage.md` for
the player, page and overlay, `development.md` for tests and tooling). Read the part you
are changing before changing it.

## Who it is for

**Write as if the person using spotifixed is not a developer.** Someone else may
have set it up for them, and from then on they use it only through the desktop launcher,
the page and the overlay. They never open a terminal on purpose and do not know what a
daemon, a command-line flag, an endpoint or a process is.

So everything such a person can see is written for them:

- **The page** (`spotifixed/server/static/`): labels, help text, tooltips, status
  pills and the messages shown when something fails. Say what a control does for them
  and where to find related things on the page, not how it works inside. Do not name
  commands (`ctl`, `serve`, `start --no-overlay`), files, settings variables or
  internals like redraws, streams or clients.
- **Anything the page shows that comes from the daemon**, such as the overlay's
  `problem` sentence. API errors returned for a page action should read as a sentence a
  user can act on.
- **What the launcher's terminal window prints in normal use** (the lines `start`
  prints), since that window is part of the application.

The rule a feature should meet: a non-developer can turn it on, try it and turn it off
from the page alone. When a capability exists only as a flag or a command, consider
whether the page should offer it too.

Developer-facing text stays as it is: `docs/`, docstrings, comments, CLI `--help`,
and errors from commands only a developer runs (`sync`, `align`, `pin`, `status`). `docs/` is
for whoever installs and maintains spotifixed and can stay technical, but when it
describes a page control, use the control's on-page name. The README is a front page for
someone deciding whether to try spotifixed: keep it short and point into `docs/`.

## Working here

- `scripts/verify.sh` runs the tests and lint; it must pass before a commit.
  `scripts/verify.sh --head` repeats the tests from a clean export of HEAD.
- Settings live in `spotifixed/options_schema.py`; after editing it, run
  `.venv/bin/python build.py` to regenerate `.env.example` and the
  Configuration section of `docs/setup.md`. Never edit the generated section by hand.
- Tests use synthetic data only and never reach a real service. Never commit anything
  from `cache/` (real audio, lyrics or track lists) or `state/`.
- `implementation/` is a gitignored planning document from when the repo was built;
  it is not kept up to date and is not a source of truth.
- Match the existing style: plain declarative prose in comments and docs, and no emojis
  anywhere in files.
