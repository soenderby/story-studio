"""Story Studio: a local editor for illustrated stories."""
import logging

from .version import __version__

# The app adds its log file in app.start(); library use stays silent.
logging.getLogger('story_studio').addHandler(logging.NullHandler())

__all__ = ['__version__']
