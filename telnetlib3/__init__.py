"""
telnetlib3: a Telnet Protocol implemented in python.

Requires Python 3.3 or 3.4. and the 'asyncio' module
(to be distributed with python3.4). See the README file
for details.
"""
__author__ = "Jeffrey Quast"
__url__ = u'https://github.com/jquast/telnetlib3/'
__copyright__ = "Copyright 2013"
__credits__ = ["Jim Storch", "Wijnand Modderman-Lenstra"]
__license__ = 'ISC'

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
