# playlist-player

Cache a Spotify playlist's audio and time-synced lyrics, play it through mpv, and show
the current lyric line in an overlay above a fullscreen application.

Nothing here uses an LLM at runtime. The tracklist comes from one Apify REST call per
playlist, lyrics from LRCLIB, audio through yt-dlp, and everything is cached on disk so
it is fetched once.

> Status: under construction. Syncing (`sync`, `status`, `pin`) and the daemon (`serve`,
> the remote page, `ctl`) work. The lyric `overlay` and the installer for running the
> daemon from a reviewed copy are not built yet. This README grows with each stage.

## How it fits together

| Command | What it does | Runs as |
|---------|--------------|---------|
| `sync` | Fetches tracklist, audio, lyrics and cover art into `cache/` | dev, from this repo |
| `status` | Shows what is cached for a playlist | either |
| `pin` | Chooses the audio for a track by hand | dev |
| `serve` | The daemon: owns mpv, publishes playback state, serves the remote-control page | ethan, from an installed copy |
| `ctl` | Sends play/pause/next to the daemon, for a global shortcut | ethan |
| `overlay` | Draws the current lyric line above other windows; hidden while paused | ethan |

The page and the overlay are both plain clients of the daemon's HTTP API on 127.0.0.1.
Neither reads the cache and neither talks to mpv.

## Setup

System packages (Debian 13):

```bash
sudo apt-get install mpv mpv-mpris libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0 yt-dlp-
```

The trailing `yt-dlp-` keeps Debian's yt-dlp out. yt-dlp is installed in the virtualenv
instead, because it has to be updated whenever YouTube changes.

Virtualenv (`--system-site-packages` is required: the GTK bindings are a system package):

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements-dev.txt
scripts/prereq-check.sh
```

## The one secret

Only the tracklist fetch needs a secret, an Apify API token. It lives in
`~/.config/playlist-player/secrets.env` (mode 600) in Ethan's home, never in this repo;
see `secrets.env.example`. `sync` reads it from the `APIFY_TOKEN` environment variable,
and only when a playlist's tracklist is not cached yet. Fetch a tracklist once with the
wrapper that loads the secrets file and then runs `sync <playlist> --only tracklist`:

```bash
scripts/fetch-tracklist.sh 'https://open.spotify.com/playlist/<id>'
```

Each fetch is a paid Actor run of about $0.01, sent once and never retried
automatically. The response is kept verbatim under `cache/playlists/<id>/` and every
later `sync` works from that copy with no token and no request. It is fetched again only
on an explicit `--refresh` (`sync --refresh-tracklist`). An exit status of 2 means the
source returned fewer tracks than the playlist holds.

## Syncing a playlist

```bash
.venv/bin/python -m playlist_player sync <playlist-url>     # everything, resumable
.venv/bin/python -m playlist_player sync <id> --limit 10    # the first ten tracks
.venv/bin/python -m playlist_player sync <id> --only lyrics # one stage: audio, lyrics or cover
.venv/bin/python -m playlist_player sync <id> --dry-run     # what would be fetched
.venv/bin/python -m playlist_player status <id>             # per-track table and totals
```

Per track, in this order: audio (yt-dlp), then lyrics (LRCLIB), then cover art. The
order matters: the lyrics record is chosen by the measured length of the audio that was
actually downloaded, which is what keeps the overlay in time.

`sync` can be interrupted at any point and run again; what is on disk is what is done.
What a source does not have is remembered in `cache/misses.json` and not asked for
again for `MISS_RETRY_DAYS` (or until `--retry-misses`). A stage that fails outright
(network trouble, yt-dlp broken by a YouTube change) is not remembered and is simply
retried by the next `sync`. When downloads start failing, update yt-dlp first:

```bash
.venv/bin/pip install -U yt-dlp
```

A track whose audio search finds nothing trustworthy is flagged `review` in `status`,
with its best candidates listed in `cache/misses.json`. Choose one by hand, then sync
that track again:

```bash
.venv/bin/python -m playlist_player pin <track-id> <url>    # or: pin <track-id> clear
.venv/bin/python -m playlist_player sync <id> --track <track-id>
```

## Playing

```bash
.venv/bin/python -m playlist_player serve          # the daemon; then open http://127.0.0.1:8765/
.venv/bin/python -m playlist_player ctl toggle     # also: play, pause, next, prev, stop
.venv/bin/python -m playlist_player ctl seek +10   # relative with a sign, absolute without
.venv/bin/python -m playlist_player ctl volume 70
```

`serve` starts mpv and owns it. The page is a remote control, not a player: sound comes
from mpv. Pick a playlist, click a track, and the lyric pane follows the song; the
offset buttons shift a track's lyrics earlier or later and the value is remembered in
`state/`. Space toggles, left and right seek five seconds, up and down change track.

`ctl` exists so a desktop shortcut can pause from inside a fullscreen application. With
`PLAYLIST_PLAYER_MPV_MPRIS_PLUGIN` set, the keyboard's media keys work too.

The daemon listens on loopback only, refuses requests that do not name its own address
or that come from another web origin, takes playlist and track IDs but never file
paths, and has no endpoint that downloads anything.

## Development

```bash
.venv/bin/python -m pytest                 # loopback only; spawns mpv (silent), ffmpeg and node
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python build.py                  # regenerate .env.example and the section below
```

Everything under `cache/` is third-party content (audio, lyrics, tracklists) and is
gitignored. Tests use synthetic data only; never commit a real lyric, audio file or
Actor response.

## Configuration

Every setting can be given as a CLI flag, an environment variable, or a line in a `.env`
file in the working directory, in that order of precedence. `APIFY_TOKEN` is the
exception: supply it through the environment only. The generated list below shows a flag
for it because the generator documents one for every option; that flag is deliberately
not registered, so a token cannot end up on a command line.

<!-- BEGIN_GENERATED_OPTIONS -->
#### Storage

* **PLAYLIST_PLAYER_CACHE_DIR** - Directory for everything fetched: tracklists, audio, lyrics, cover art. A relative path resolves against the repo root, not the working directory.
  - Default: `"cache"`
  - Command line: `--cache-dir`

* **PLAYLIST_PLAYER_STATE_DIR** - Directory for hand-authored per-track values (timing offsets, audio pins). Kept apart from the cache so wiping the cache never loses a correction.
  - Default: `"state"`
  - Command line: `--state-dir`


#### Tracklist (Apify)

* **APIFY_TOKEN** - Apify API token. Environment only, never a CLI argument and never a committed file. Needed solely to fetch a playlist's track list that is not cached yet; see secrets.env.example for where the real value lives.
  - Default: None
  - Command line: `--apify-token`

* **PLAYLIST_PLAYER_TRACKLIST_ACTOR** - Apify Actor that returns a playlist's tracks, as user~name. The response shape is Actor-specific, so a new Actor also needs a parser.
  - Default: `"s-r~free-spotify-playlist-intelligence"`
  - Command line: `--tracklist-actor`

* **PLAYLIST_PLAYER_APIFY_MAX_CHARGE_USD** - Charge cap sent with each Actor run, in US dollars.
  - Default: `"0.05"`
  - Command line: `--apify-max-charge-usd`

* **PLAYLIST_PLAYER_APIFY_BASE_URL** - Apify API origin. Tests point this at a local stub.
  - Default: `"https://api.apify.com"`
  - Command line: `--apify-base-url`


#### Sync

* **PLAYLIST_PLAYER_LRCLIB_BASE_URL** - LRCLIB origin, the lyrics source.
  - Default: `"https://lrclib.net"`
  - Command line: `--lrclib-base-url`

* **PLAYLIST_PLAYER_AUDIO_DURATION_TOLERANCE_S** - An audio candidate within this many seconds of Spotify's duration gets full marks for duration.
  - Default: `"3.0"`
  - Command line: `--audio-duration-tolerance-s`

* **PLAYLIST_PLAYER_AUDIO_DURATION_REJECT_S** - An audio candidate further than this from Spotify's duration is rejected.
  - Default: `"7.0"`
  - Command line: `--audio-duration-reject-s`

* **PLAYLIST_PLAYER_LYRICS_DURATION_TOLERANCE_S** - A lyrics record further than this from the audio's real duration is still kept, but flagged as low timing confidence.
  - Default: `"2.0"`
  - Command line: `--lyrics-duration-tolerance-s`

* **PLAYLIST_PLAYER_SYNC_WORKERS** - Tracks processed in parallel. Keep low to stay polite to the sources.
  - Default: `"2"`
  - Command line: `--sync-workers`

* **PLAYLIST_PLAYER_MISS_RETRY_DAYS** - A recorded miss (no lyrics, no audio match) is retried after this many days.
  - Default: `"14"`
  - Command line: `--miss-retry-days`


#### Daemon

* **PLAYLIST_PLAYER_HOST** - Address the daemon binds. Anything other than loopback is refused.
  - Default: `"127.0.0.1"`
  - Command line: `--host`

* **PLAYLIST_PLAYER_PORT** - Port for the daemon's HTTP API and remote-control page.
  - Default: `"8765"`
  - Command line: `--port`

* **PLAYLIST_PLAYER_MPV_BIN** - mpv executable.
  - Default: `"mpv"`
  - Command line: `--mpv-bin`

* **PLAYLIST_PLAYER_MPV_AUDIO_OUTPUT** - mpv --ao value. Empty lets mpv choose; tests use `null` for silence.
  - Default: `""` (empty)
  - Command line: `--mpv-audio-output`

* **PLAYLIST_PLAYER_MPV_MPRIS_PLUGIN** - Path to mpv's MPRIS plugin (Debian: /usr/lib/mpv-mpris/mpris.so) so desktop media keys control playback. Empty disables it.
  - Default: `""` (empty)
  - Command line: `--mpv-mpris-plugin`


#### Overlay

* **PLAYLIST_PLAYER_OVERLAY_MONITOR** - Output connector to show the overlay on, e.g. DP-2. Empty: compositor's choice.
  - Default: `""` (empty)
  - Command line: `--overlay-monitor`

* **PLAYLIST_PLAYER_OVERLAY_ANCHOR** - Screen edge or corner the overlay is attached to.
  - Default: `"bottom"`
  - Command line: `--overlay-anchor`

* **PLAYLIST_PLAYER_OVERLAY_MARGIN_X** - Horizontal distance in pixels from the anchored edge.
  - Default: `"0"`
  - Command line: `--overlay-margin-x`

* **PLAYLIST_PLAYER_OVERLAY_MARGIN_Y** - Vertical distance in pixels from the anchored edge.
  - Default: `"120"`
  - Command line: `--overlay-margin-y`

* **PLAYLIST_PLAYER_OVERLAY_MAX_WIDTH** - Maximum line width in pixels; longer lines wrap.
  - Default: `"1200"`
  - Command line: `--overlay-max-width`

* **PLAYLIST_PLAYER_OVERLAY_FONT** - Font family and style.
  - Default: `"Sans Bold"`
  - Command line: `--overlay-font`

* **PLAYLIST_PLAYER_OVERLAY_FONT_SIZE** - Font size in points.
  - Default: `"32"`
  - Command line: `--overlay-font-size`

* **PLAYLIST_PLAYER_OVERLAY_COLOR** - Text colour.
  - Default: `"#FFFFFF"`
  - Command line: `--overlay-color`

* **PLAYLIST_PLAYER_OVERLAY_OUTLINE_COLOR** - Outline colour, for contrast against any background.
  - Default: `"#000000"`
  - Command line: `--overlay-outline-color`

* **PLAYLIST_PLAYER_OVERLAY_LINES** - 1 shows the current line; 2 adds the next line, dimmed.
  - Default: `"1"`
  - Command line: `--overlay-lines`

* **PLAYLIST_PLAYER_OVERLAY_LEAD_MS** - Show each line this many milliseconds before its timestamp.
  - Default: `"150"`
  - Command line: `--overlay-lead-ms`

* **PLAYLIST_PLAYER_OVERLAY_FADE_MS** - Fade duration when a line appears or disappears.
  - Default: `"120"`
  - Command line: `--overlay-fade-ms`


<!-- END_GENERATED_OPTIONS -->
