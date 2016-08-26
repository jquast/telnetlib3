"""telnetlib3: an asyncio Telnet Protocol implemented in python."""
# pylint: disable=wildcard-import,undefined-variable
from .server_base import *
from .server_shell import *
from .server import *
from .stream_writer import *
from .stream_reader import *
from .client_base import *
from .client_shell import *
from .client import *
from .telopt import *
from .slc import *
from .accessories import get_version as __get_version

__all__ = (
    server_base.__all__ +
    server_shell.__all__ +
    server.__all__ +

    client_base.__all__ +
    client_shell.__all__ +
    client.__all__ +

    stream_writer.__all__ +
    stream_reader.__all__ +
    telopt.__all__ +
    slc.__all__
)

__author__ = "Jeff Quast"
__url__ = u'https://github.com/jquast/telnetlib3/'
__copyright__ = "Copyright 2013"
__credits__ = ["Jim Storch", "Wijnand Modderman-Lenstra"]
__license__ = 'ISC'
__version__ = __get_version()
