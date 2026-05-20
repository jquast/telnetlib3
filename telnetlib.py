"""Drop-in shim for telnetlib (removed from stdlib in Python 3.13)."""
import sys

if sys.version_info >= (3, 13):
    from telnetlib3.telnetlib import *  # noqa: F401, F403
else:
    sys.modules.pop(__name__, None)
    import telnetlib  # noqa: W0406
    sys.modules[__name__] = telnetlib
