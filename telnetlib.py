"""Drop-in shim for telnetlib (removed from stdlib in Python 3.13)."""
from telnetlib3.telnetlib import *  # noqa: F401, F403
