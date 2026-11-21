"""A 302 redirect service for Emby libraries that live in 115.

One job: reverse proxy the Emby server,
rewrite PlaybackInfo so MediaSources point here, and answer the play request
with a 302 to the cloud URL.
"""

from .app import APP_VERSION as __version__  # noqa: F401
