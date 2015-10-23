"""telnetlib3: an asyncio Telnet Protocol implemented in python."""
# pylint: disable=wildcard-import,undefined-variable
from .server import *
from .client import *
from .telsh import *
from .telopt import *
from .slc import *
from .conio import *

__all__ = (server.__all__ +
           client.__all__ +
           telsh.__all__ +
           telopt.__all__ +
           slc.__all__ +
           conio.__all__)

__author__ = "Jeff Quast"
__url__ = u'https://github.com/jquast/telnetlib3/'
__copyright__ = "Copyright 2013"
__credits__ = ["Jim Storch", "Wijnand Modderman-Lenstra"]
__license__ = 'ISC'
