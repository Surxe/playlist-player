# Using spotifixed

The player, the page and the overlay, once spotifixed is [set up](setup.md).

## Playing

```bash
.venv/bin/python -m spotifixed start          # everything at once; see below
.venv/bin/python -m spotifixed serve          # the daemon alone; then open http://127.0.0.1:8765/
.venv/bin/python -m spotifixed ctl toggle     # also: play, pause, next, prev, stop
.venv/bin/python -m spotifixed ctl seek +10   # relative with a sign, absolute without
.venv/bin/python -m spotifixed ctl volume 70
```

`start` is the one command for everyday use. It runs the daemon, starts the overlay
beside it and opens the page in your browser, all from one terminal, and that terminal
is the application: Ctrl-C in it, or closing it, stops the daemon, mpv and the overlay
together, after the daemon has saved where it was. Run it a second time while it is
already running and it only opens the page. If the overlay cannot run here (no
layer-shell, no display) that is said once and the rest carries on. The overlay can also
be switched off and on from the page (see [The overlay](#the-overlay)), and `start` stops or starts it to
match. `--no-overlay` and `--no-browser` leave either part out; with `--no-overlay` the
page cannot switch the overlay on. To put it behind a desktop launcher, have the
launcher open a terminal running `start`; `spotifixed/server/static/icon.svg` is
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
`align` (see [Word timing](setup.md#word-timing)) use the times heard in the audio instead.

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
volume; 0, the default, turns it off. That includes the first play after spotifixed
starts, since the remembered song is put back paused; pausing part-way through a fade
starts the next one from silence. Skipping between songs while playing does not fade. A
change applies at once. Your volume is not changed while it fades. It needs mpv 0.37 or
newer; with an older mpv playback simply starts at full level.

**Same sound in both ears** (also under **Settings**, off by default) plays in mono: both
ears get the average of the left and right channels, so nothing panned to one side is
lost with one earbud in or with hearing that is weaker on one side. The output stays
two-channel, so it does not depend on how the sound server spreads a mono stream. A
change applies at once, to the song that is playing too, and is kept across restarts.

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
`SPOTIFIXED_MPV_MPRIS_PLUGIN` set to the plugin's path (Debian:
`/usr/lib/mpv-mpris/mpris.so`), the keyboard's media keys work too.

The daemon listens on loopback only, refuses requests that do not name its own address
or that come from another web origin, takes playlist and track IDs but never file
paths, and has no endpoint that downloads anything. The only things a request can
change are playback, a track's lyric offset and the overlay's appearance, each checked
against a fixed set of allowed values. mpv is started with no config file,
no scripts and no network access.

## The overlay

```bash
.venv/bin/python -m spotifixed overlay          # follows the daemon; Ctrl-C to quit
.venv/bin/python -m spotifixed overlay --demo   # a fixed line, for positioning
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
spotifixed's to close, so under `serve` or `start --no-overlay` the switch is greyed
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
setting back to its configured value (see [Configuration](setup.md#configuration)), and **Reset to
configured values** puts every one back.

Without the page, `overlay --demo` shows a fixed line for trying flags by hand, such as
`overlay --demo --overlay-anchor top-right --overlay-margin-y 40`.

If a game ignores it, run the game in borderless-window mode rather than exclusive
fullscreen.

