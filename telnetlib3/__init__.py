"""telnetlib3: an asyncio Telnet Protocol implemented in python."""

# flake8: noqa: F405
# fmt: off
# isort: off
# Import order matters: server_shell symbols must be exported before server
# import due to function_lookup("telnetlib3.telnet_server_shell") at server.py load time
from . import server_base
from . import server_shell
from .server_shell import *  # noqa - Must export before server import
from . import server
from . import stream_writer
from . import stream_reader
from . import client_base
from . import client_shell
from . import client
from . import telopt
from . import slc
from . import telnetlib
from .server_base import *  # noqa
from .server import *  # noqa
from .stream_writer import *  # noqa
from .stream_reader import *  # noqa
from .client_base import *  # noqa
from .client_shell import *  # noqa
from .client import *  # noqa
from .telopt import *  # noqa
from .telnetlib import *  # noqa
from .slc import *  # noqa
try:
    from . import pty_shell as _pty_shell_module
    from .pty_shell import *  # noqa
    PTY_SUPPORT = True  # invalid-name
except ImportError:
    _pty_shell_module = None
    PTY_SUPPORT = False  # invalid-name
from . import guard_shells as _guard_shells_module
from .guard_shells import *  # noqa
from . import sync as _sync_module
from .sync import *  # noqa
from .accessories import get_version as __get_version
# isort: on
# fmt: on

__all__ = (
    server_base.__all__
    + server_shell.__all__
    + server.__all__
    + client_base.__all__
    + client_shell.__all__
    + client.__all__
    + stream_writer.__all__
    + stream_reader.__all__
    + telopt.__all__
    + slc.__all__
    + telnetlib.__all__
    + (_pty_shell_module.__all__ if PTY_SUPPORT else ())
    + _guard_shells_module.__all__
    + _sync_module.__all__
)  # noqa

__author__ = "Jeff Quast"
__url__ = "https://github.com/jquast/telnetlib3/"
__copyright__ = "Copyright 2013"
__credits__ = ["Jim Storch", "Wijnand Modderman-Lenstra"]
__license__ = "ISC"
__version__ = __get_version()
