"""Adapters for the outside world: where tracklists, lyrics and audio come from.

Each kind of source has a protocol in its ``base.py`` and exactly one implementation
today. A source knows how to ask its provider and how to read the answer; it does not
know about the cache. Deciding whether to ask at all, and where the answer is kept, is
the sync layer's job.
"""
