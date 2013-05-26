"""
Telnet Protocol using the 'tulip' project of PEP 3156.

Requires Python 3.3.

For convenience, the 'tulip' module is included.

See the ``README`` file for details and license.
"""
__author__ = "Jeffrey Quast"
__url__ = u'https://github.com/jquast/telnetlib3/'
__copyright__ = "Copyright 2013"
__credits__ = ["Jim Storch",]
__license__ = 'ISC'


__all__ = ['TelnetServer', 'TelnetStreamReader']

from server import TelnetServer
from telopt import TelnetStreamReader
