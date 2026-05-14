"""
Drop-in shim for telnetlib on 3.13+.

On Python 3.12 and earlier, `import telnetlib` should find the module in the standard library, but
in 3.13+, that standard module is removed. That should cause this shim to be found, which just
imports the (mostly) unadulterated copy this project vendors.
"""

# local
# pylint: disable=wildcard-import,unused-wildcard-import
from telnetlib3.telnetlib import *  # noqa: F401, F403
