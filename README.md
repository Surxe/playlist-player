# playlist-player

Cache a Spotify playlist's audio and time-synced lyrics, play it through mpv, and show
the current lyric line in an overlay above a fullscreen application.

Nothing here uses an LLM at runtime. The track list comes from one Apify REST call per
playlist, lyrics from LRCLIB, audio through yt-dlp, and everything is cached on disk so
it is fetched once.

Linux only. The overlay additionally needs a Wayland compositor that supports the
layer-shell protocol (KDE Plasma, Sway and other wlroots compositors; not GNOME).

## How it fits together

| Command | What it does | Needs |
|---------|--------------|-------|
| `sync` | Fetches track list, audio, lyrics and cover art into the cache | network, yt-dlp, ffmpeg |
| `align` | Optional: times each word of the synced lyrics against the audio | PyTorch and Demucs (`requirements-align.txt`), ffmpeg; a GPU, in practice |
| `status` | Shows what is cached for a playlist | |
| `pin` | Chooses the audio for a track by hand | |
| `serve` | The daemon: owns mpv, publishes playback state, serves the remote-control page | mpv |
| `ctl` | Sends play, pause, next and so on to the daemon, for a keyboard shortcut | a running `serve` |
| `overlay` | Draws the current lyric line above other windows; hidden while paused | a running `serve`, GTK 4 layer-shell |
| `start` | The whole application at once: `serve`, `overlay` beside it, and the page opened in the browser | what those need |

`sync` is the only command that touches the network. `serve`, the page and the overlay
work from the cache: the page and the overlay are plain clients of the daemon's HTTP API
on 127.0.0.1, and neither reads the cache nor talks to mpv.

## Setup

Programs (Debian and Ubuntu names; other distributions have the same packages):

```bash
sudo apt-get install python3-venv ffmpeg mpv                       # required
sudo apt-get install mpv-mpris                                     # optional: media keys
sudo apt-get install libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0 python3-gi  # optional: overlay
```

Then, in a clone of this repo:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
scripts/prereq-check.sh
```

`--system-site-packages` lets the virtualenv see the system's GTK bindings, which pip
cannot install; leave it off if you do not want the overlay. yt-dlp is installed in the
virtualenv, not from a distribution package, because it has to be updated whenever
YouTube changes. Having `deno` or `node` on the PATH makes its extraction more reliable.

Run commands as `.venv/bin/python -m playlist_player <command>` from the repo. Only
`sync` needs the virtualenv at all: `serve`, `ctl` and `overlay` use nothing but the
standard library (and, for the overlay, the system GTK bindings), so they also run with
the system `python3`.

## The one secret

Fetching a playlist's track list is a paid Apify Actor run of about $0.01 and needs an
Apify API token in the `APIFY_TOKEN` environment variable (or in a gitignored `.env`).
See `secrets.env.example` for the two ways to supply it. The token is read only by
`sync`, and only when a playlist's track list is not cached yet.

The request is sent once and never retried automatically. The response is kept verbatim
under `cache/playlists/<id>/`, and every later `sync` works from that copy with no token
and no request; it is fetched again only with `sync --refresh-tracklist`. An exit status
of 2 means the source returned fewer tracks than the playlist holds.

## Syncing a playlist

```bash
.venv/bin/python -m playlist_player sync <playlist-url>     # everything, resumable
.venv/bin/python -m playlist_player sync <id> --limit 10    # the first ten tracks
.venv/bin/python -m playlist_player sync <id> --only lyrics # one stage: tracklist, audio, lyrics or cover
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

The audio search wants the album recording. A candidate labelled as another version is
held below the line for downloading by itself: live, remix, cover, karaoke, instrumental,
TV mix, backing track and the like in its title, a karaoke or instrumentals channel, or a
credit such as "originally performed by" or "made famous by" (a name after such a credit
does not count as the artist's). A label the Spotify title carries itself is not held
against it.

Karaoke uploads are not always labelled, so `align` (see Word timing) also measures how
much of each track's audio has a voice in it. When a track with synced lyrics turns out
to have next to no singing, `align` says so, and the next `sync` replaces the audio with
the best candidate not yet tried; what was rejected is listed in the track's
`rejected_audio.json`. After three such versions, or when no other version is left, the
audio it has is kept. A pinned track's audio is never replaced this way.

A track whose audio search finds nothing trustworthy is flagged `review` in `status`,
with its best candidates listed in `cache/misses.json`. Choose one by hand, then sync
that track again:

```bash
.venv/bin/python -m playlist_player pin <track-id> <url>    # or: pin <track-id> clear
.venv/bin/python -m playlist_player sync <id> --track <track-id>
```

Audio is downloaded for personal, local playback and never redistributed. Whether that
is acceptable under the terms of the services involved is for you to judge.

## Word timing

Optional, and only of use with the **Underline word** setting. Lyric files time lines,
not words, so by default the underlined word is an estimate from syllable counts. `align`
replaces the estimate with what is heard in the audio:

```bash
.venv/bin/pip install -r requirements-align.txt --extra-index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -m playlist_player align                 # every cached playlist, resumable
.venv/bin/python -m playlist_player align <id> --limit 10 # the first ten tracks of one
.venv/bin/python -m playlist_player align --track <id>    # one track; --force to do it again
```

For each track with synced lyrics and audio it separates the voice from the music
(Demucs), has a speech model say which letters it hears in every 20 ms of that voice (MMS
forced alignment), and places each line's words inside a window around that line's own
timestamp. The result is `words.json` beside the track's lyrics; the daemon sends it with
the lyrics, and the overlay and the page use it. Nothing else needs these packages, and a
track that has not been aligned simply keeps the estimate.

It works line by line. A line the model heard too faintly to trust (a processed or
buried voice, usually) is left out and keeps the estimate, so one song can have both;
`align` prints how many lines it placed. Word times are in the audio's own time, so the
track's offset does not move them, though a large change of offset moves where lines are
looked for and the track is aligned again on the next run. New audio for a track (a pin)
discards its word times. About five seconds a track on a recent GPU.

It also records the share of the track in which a voice is heard (`voice_share` in
`words.json`). Under 0.15 the audio is taken to have no singing, a karaoke backing or an
instrumental under the song's name, and `align` prints "no singing heard"; see Syncing a
playlist for what `sync` does about it. Songs with vocals measure about 0.4 and up.

## Playing

```bash
.venv/bin/python -m playlist_player start          # everything at once; see below
.venv/bin/python -m playlist_player serve          # the daemon alone; then open http://127.0.0.1:8765/
.venv/bin/python -m playlist_player ctl toggle     # also: play, pause, next, prev, stop
.venv/bin/python -m playlist_player ctl seek +10   # relative with a sign, absolute without
.venv/bin/python -m playlist_player ctl volume 70
```

`start` is the one command for everyday use. It runs the daemon, starts the overlay
beside it and opens the page in your browser, all from one terminal, and that terminal
is the application: Ctrl-C in it, or closing it, stops the daemon, mpv and the overlay
together, after the daemon has saved where it was. Run it a second time while it is
already running and it only opens the page. If the overlay cannot run here (no
layer-shell, no display) that is said once and the rest carries on. The overlay can also
be switched off and on from the page (see The overlay), and `start` stops or starts it to
match. `--no-overlay` and `--no-browser` leave either part out; with `--no-overlay` the
page cannot switch the overlay on. To put it behind a desktop launcher, have the
launcher open a terminal running `start`; `playlist_player/server/static/icon.svg` is
the icon the page uses and serves as the launcher's too.

`serve` starts mpv and owns it. The page is a remote control, not a player: sound comes
from mpv. Pick a playlist, click a track, and the lyric pane follows the song; the
offset buttons shift a track's lyrics earlier or later and the value is remembered in
`state/`. Space toggles, left and right seek five seconds, up and down change track.

The bar under the current lyric line fills from that line's start to the start of the
next one, which makes an offset readable: if the bar is already part-filled when the words
begin, the lyrics are ahead of the song, and if the words begin before it starts, they are
behind. Lyric files only record when each line starts, so the bar also runs through any
pause or instrumental that follows the words.

**Underline word** (in the overlay settings, off by default) underlines the word being
sung, in the overlay and in the lyric pane. Lyric files do not time words, so this is an
estimate: each word gets a share of its line by its syllables, and a line that lasts much
longer than its syllables would take at the pace of the lines around it is taken to end
early, with nothing underlined for the rest (an instrumental, usually). It follows the
same clock as the line, so it is only as good as the track's offset, and it cannot know
about uneven phrasing or a note held at the end of a line. Tracks that have been through
`align` (see Word timing) use the times heard in the audio instead.

**Shuffle** puts the playlist shown in a new random order and keeps it. The track list
is redrawn in that order, it is the order the playlist plays in from then on, and it is
still there after a restart. If that playlist is playing, the queue is rearranged at once
without interrupting the song, which is placed first so that everything else is still to
come. Press it again for another order; **Original order** puts the playlist back the way
it is on Spotify. Tracks a later sync adds go to the end of a kept order.

**Resume.** The daemon remembers the playlist, the song, its position and the volume, and
puts them back when it starts: the same song at the same place, at the same volume, paused.
It does not begin playing by itself. A song saved at its very end starts from the beginning.

**Fade in** (under **Settings** on the page) makes the music fade in each time it is
resumed after a pause, over the number of seconds you set, instead of coming on at full
volume; 0, the default, turns it off. That includes the first play after playlist-player
starts, since the remembered song is put back paused; pausing part-way through a fade
starts the next one from silence. Skipping between songs while playing does not fade. A
change applies at once. Your volume is not changed while it fades. It needs mpv 0.37 or
newer; with an older mpv playback simply starts at full level.

**Tracks** (also under **Settings**) chooses which tracks a playlist is made of. Both
switches are on to begin with, so nothing is left out. With **Include tracks without
synced lyrics** off, tracks that have no synced lyrics (plain text only, instrumental,
or none found) are taken out of the track list and skipped when playing. With **Include
tracks without audio** off, tracks with no audio, which can never be played, are no
longer shown. A change applies at once, to the queue that is playing too; the song that
is on carries on even if it is one of those left out. A shuffled order is not disturbed:
a track that is left out keeps its place for when it is included again.

**Page** (also under **Settings**) keeps the page light. **Show lyrics** off hides the
lyric pane, with its bar and underline, and stops the page from fetching lyrics at all;
the overlay is not affected. **Show time and position** off hides the elapsed time and
the slider. The page only redraws while something it shows is moving: every frame for
synced lyrics, once a second for the time alone, and not at all while paused or with
both switches off. The lyric offset is set in the lyric pane, so turn lyrics back on to
adjust it. Media keys and `ctl` go to the daemon, not the page, and work even with the
page closed; `start --no-browser` is the lightest way to run.

`ctl` exists so a desktop shortcut can pause from inside a fullscreen application. With
`PLAYLIST_PLAYER_MPV_MPRIS_PLUGIN` set to the plugin's path (Debian:
`/usr/lib/mpv-mpris/mpris.so`), the keyboard's media keys work too.

The daemon listens on loopback only, refuses requests that do not name its own address
or that come from another web origin, takes playlist and track IDs but never file
paths, and has no endpoint that downloads anything. The only things a request can
change are playback, a track's lyric offset and the overlay's appearance, each checked
against a fixed set of allowed values. mpv is started with no config file,
no scripts and no network access.

## The overlay

```bash
.venv/bin/python -m playlist_player overlay          # follows the daemon; Ctrl-C to quit
.venv/bin/python -m playlist_player overlay --demo   # a fixed line, for positioning
```

The overlay shows the current lyric line on the compositor's overlay layer, the one
drawn above fullscreen windows. It takes no focus, clicks pass through it, and it is on
screen only while a line is due: it is removed (not just made transparent) when playback
pauses, between lines, for tracks without synced lyrics, and whenever the daemon is not
running. It is a client of the daemon's API and nothing more.

**Show lyrics on top of other windows** (at the top of the Overlay section under
**Settings**) switches the overlay off and on. Off, the overlay program is closed, not
hidden, so it takes no memory or processor time until it is switched on again; its
settings are kept either way, and the choice is remembered for the next start. This works
for the overlay that `start` runs. One started by hand (`overlay` beside `serve`) is not
playlist-player's to close, so under `serve` or `start --no-overlay` the switch is greyed
out. If the overlay stops by itself, the page says so, and switching it off and on tries
again.

### Positioning and styling it

Open the page and press **Settings**. The Overlay section changes a running overlay as you move
the controls: the edge, corner or `center` it is anchored to, the two margins, the
monitor, font, size, colours, a second (next) line, how early a line appears and the
fade. **Show a sample line** puts a stand-in line on screen so you can do this with
nothing playing; it goes away by itself when the panel or the tab is closed.

Changes are saved automatically and take effect immediately: there is nothing to save
or copy anywhere. They are kept in `state/overlay.json` and are still there after a
restart. A highlighted row has been changed in the panel; the `x` beside it puts that one
setting back to its configured value (the Configuration section below), and **Reset to
configured values** puts every one back.

Without the page, `overlay --demo` shows a fixed line for trying flags by hand, such as
`overlay --demo --overlay-anchor top-right --overlay-margin-y 40`.

If a game ignores it, run the game in borderless-window mode rather than exclusive
fullscreen.

## Where things are kept

| Path | Holds |
|------|-------|
| `cache/` | Everything fetched: track lists, audio, lyrics, covers. Safe to delete; `sync` rebuilds it. |
| `state/` | What you changed by hand: lyric offsets, audio pins, and overlay settings set from the page (with whether the overlay is switched on). Not rebuilt. |
| `state/session.json` | Each shuffled playlist's order, and what the daemon resumes: the playlist, song, position and volume, with the settings made on the page (fade in, which tracks are included). Rewritten as playback changes; deleting it just starts fresh. |

Both are gitignored and sit in the repo by default; `PLAYLIST_PLAYER_CACHE_DIR` and
`PLAYLIST_PLAYER_STATE_DIR` move them. Files get ordinary permissions, whatever your
umask allows.

This repo ships no installer and does nothing about user accounts. To run the daemon
and overlay from somewhere other than a working clone (as a different account than the
one that syncs, say), copy the `playlist_player/` directory there, run it with the
system `python3 -m playlist_player`, and point the two directory settings at the shared
cache. Sharing a cache between accounts is ordinary Unix: a group-owned, setgid
directory and a umask of 002 for whoever writes to it.

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                 # loopback only; spawns mpv (silent), ffmpeg and node
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python build.py                  # regenerate .env.example and the section below
scripts/verify.sh                          # the three checks above; --head tests a clean export
scripts/headless-kwin.sh run <command>     # run something in a display-less KWin, e.g. the overlay
```

Tests never reach a real service: every third-party API is replaced by a loopback
server, and a guard fails any test that connects elsewhere. The overlay tests start a
headless KWin themselves and are skipped where KWin or the layer-shell bindings are
missing. The page's settings panel is tested in a headless Chrome, driven by node over
the DevTools protocol, and skipped where either is missing. `overlay --demo --snapshot out.png` renders one frame to a PNG (over a half
dark, half light backdrop) for a look without a graphical session.

Everything under `cache/` is third-party content (audio, lyrics, track lists). Tests use
synthetic data only; never commit a real lyric, audio file or Actor response.

## Configuration

Every setting can be given as a CLI flag, an environment variable, or a line in a `.env`
file in the working directory, in that order of precedence. `APIFY_TOKEN` is the
exception: supply it through the environment or `.env` only. The generated list below
shows a flag for it because the generator documents one for every option; that flag is
deliberately not registered, so a token cannot end up on a command line.

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

* **PLAYLIST_PLAYER_OVERLAY_UNDERLINE_WORD** - Underline the word being sung, in the overlay and on the page. The word is estimated from the line timing, so it needs the track's offset to be right.
  - Default: `"false"`
  - Command line: `--overlay-underline-word`


<!-- END_GENERATED_OPTIONS -->
