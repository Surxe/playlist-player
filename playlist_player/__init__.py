"""playlist-player: cache a Spotify playlist's audio and time-synced lyrics, play it
through mpv, and show the current lyric line in an overlay.

Package map (imports only ever point downward in this list):

* ``cli``                         -- argparse entry point; the only place commands are wired
* ``sync`` / ``server`` / ``overlay`` -- the three things a user runs
* ``sources`` / ``playback``      -- adapters for external systems, each behind a protocol
* ``cache`` / ``lyrics`` / ``config`` / ``http`` -- shared building blocks
* ``models`` / ``errors``         -- plain data and exceptions; import nothing from the package
"""

__version__ = "0.1.0"
