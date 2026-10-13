"""``sync``: bring a playlist to a fully cached state, one resumable step at a time.

This layer decides what needs fetching and where results are kept. The sources below it
only know how to ask their provider; the cache below it only knows paths and writes.
"""
