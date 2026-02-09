"""Custom BBS/retro-computing codecs for telnetlib3.

Registers petscii and atarist codecs with Python's codecs module on import.
These encodings are then available for use with ``bytes.decode()`` and the
``--encoding`` CLI flag of ``telnetlib3-fingerprint``.
"""

import codecs
import importlib

_cache = {}
_aliases = {}


def _search_function(encoding):
    """Codec search function registered with codecs.register()."""
    normalized = encoding.lower().replace('-', '_')

    if normalized in _aliases:
        return _aliases[normalized]

    if normalized in _cache:
        return _cache[normalized]

    try:
        mod = importlib.import_module(f'.{normalized}', package=__name__)
    except ImportError:
        _cache[normalized] = None
        return None

    try:
        info = mod.getregentry()
    except AttributeError:
        _cache[normalized] = None
        return None

    _cache[normalized] = info

    if hasattr(mod, 'getaliases'):
        for alias in mod.getaliases():
            _aliases[alias] = info

    return info


codecs.register(_search_function)
