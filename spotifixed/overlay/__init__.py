"""``overlay``: the current lyric line, drawn above whatever else is on screen.

A pure client of the daemon's HTTP API. It learns the playback state from the event
stream, asks once per track for that track's lyric lines, and works out the current line
locally. It never reads the cache and never talks to the player, and nothing in this
package may import the code that does.
"""
