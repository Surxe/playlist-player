"""``serve``: the daemon. Owns the player, publishes its state, serves the remote page.

Everything here binds to loopback and speaks JSON. The API takes playlist and track IDs
only, never paths or URLs, and offers no way to start a download: the daemon needs no
token and makes no outbound request.
"""
