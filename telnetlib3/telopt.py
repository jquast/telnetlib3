from telnetlib import (  # noqa
    LINEMODE, NAWS, NEW_ENVIRON, BINARY, SGA, ECHO, STATUS,
    TTYPE, TSPEED, LFLOW, XDISPLOC, IAC, DONT, DO, WONT,
    WILL, SE, NOP, TM, DM, BRK, IP, AO, AYT, EC, EL, EOR,
    GA, SB, LOGOUT, CHARSET, SNDLOC, theNULL,

    # not supported or used, feel free to contribute support !
    ENCRYPT, AUTHENTICATION, TN3270E, XAUTH, RSP,
    COM_PORT_OPTION, SUPPRESS_LOCAL_ECHO, TLS, KERMIT,
    SEND_URL, FORWARD_X, PRAGMA_LOGON, SSPI_LOGON,
    PRAGMA_HEARTBEAT, EXOPL, X3PAD, VT3270REGIME, TTYLOC,
    SUPDUPOUTPUT, SUPDUP, DET, BM, XASCII, RCP, NAMS,
    RCTE, NAOL, NAOP, NAOCRD, NAOHTS, NAOHTD, NAOFFD,
    NAOVTS, NAOVTD, NAOLFD)

__all__ = (
    'ABORT', 'ACCEPTED', 'AO', 'AUTHENTICATION', 'AYT', 'BINARY', 'BM',
    'BRK', 'CHARSET', 'CMD_EOR', 'COM_PORT_OPTION', 'DET', 'DM', 'DO',
    'DONT', 'EC', 'ECHO', 'EL', 'ENCRYPT', 'EOF', 'EOR', 'ESC', 'EXOPL',
    'FORWARD_X', 'GA', 'IAC', 'INFO', 'IP', 'IS', 'KERMIT', 'LFLOW',
    'LFLOW_OFF', 'LFLOW_ON', 'LFLOW_RESTART_ANY', 'LFLOW_RESTART_XON',
    'LINEMODE', 'LOGOUT', 'MCCP2_COMPRESS', 'MCCP_COMPRESS', 'NAMS',
    'NAOCRD', 'NAOFFD', 'NAOHTD', 'NAOHTS', 'NAOL', 'NAOLFD', 'NAOP',
    'NAOVTD', 'NAOVTS', 'NAWS', 'NEW_ENVIRON', 'NOP', 'PRAGMA_HEARTBEAT',
    'PRAGMA_LOGON', 'RCP', 'RCTE', 'REJECTED', 'REQUEST', 'RSP', 'SB',
    'SE', 'SEND', 'SEND_URL', 'SGA', 'SNDLOC', 'SSPI_LOGON', 'STATUS',
    'SUPDUP', 'SUPDUPOUTPUT', 'SUPPRESS_LOCAL_ECHO', 'SUSP', 'TLS', 'TM',
    'TN3270E', 'TSPEED', 'TTABLE_ACK', 'TTABLE_IS', 'TTABLE_NAK',
    'TTABLE_REJECTED', 'TTYLOC', 'TTYPE', 'USERVAR', 'VALUE', 'VAR',
    'VT3270REGIME', 'WILL', 'WONT', 'X3PAD', 'XASCII', 'XAUTH',
    'XDISPLOC', 'theNULL', 'name_command', 'name_commands',
)

(EOF, SUSP, ABORT, CMD_EOR) = (
    bytes([const]) for const in range(236, 240))
(IS, SEND, INFO) = (bytes([const]) for const in range(3))
(VAR, VALUE, ESC, USERVAR) = (bytes([const]) for const in range(4))
(LFLOW_OFF, LFLOW_ON, LFLOW_RESTART_ANY, LFLOW_RESTART_XON) = (
    bytes([const]) for const in range(4))
(REQUEST, ACCEPTED, REJECTED, TTABLE_IS, TTABLE_REJECTED,
    TTABLE_ACK, TTABLE_NAK) = (bytes([const]) for const in range(1, 8))
(MCCP_COMPRESS, MCCP2_COMPRESS) = (bytes([85]), bytes([86]))

#: List of globals that may match an iac command option bytes
_DEBUG_OPTS = dict([(value, key)
                    for key, value in globals().items() if key in
                    ('LINEMODE', 'LMODE_FORWARDMASK', 'NAWS', 'NEW_ENVIRON',
                     'ENCRYPT', 'AUTHENTICATION', 'BINARY', 'SGA', 'ECHO',
                     'STATUS', 'TTYPE', 'TSPEED', 'LFLOW', 'XDISPLOC', 'IAC',
                     'DONT', 'DO', 'WONT', 'WILL', 'SE', 'NOP', 'DM', 'TM',
                     'BRK', 'IP', 'ABORT', 'AO', 'AYT', 'EC', 'EL', 'EOR',
                     'GA', 'SB', 'EOF', 'SUSP', 'ABORT', 'CMD_EOR', 'LOGOUT',
                     'CHARSET', 'SNDLOC', 'MCCP_COMPRESS', 'MCCP2_COMPRESS',
                     'ENCRYPT', 'AUTHENTICATION', 'TN3270E', 'XAUTH', 'RSP',
                     'COM_PORT_OPTION', 'SUPPRESS_LOCAL_ECHO', 'TLS',
                     'KERMIT', 'SEND_URL', 'FORWARD_X', 'PRAGMA_LOGON',
                     'SSPI_LOGON', 'PRAGMA_HEARTBEAT', 'EXOPL', 'X3PAD',
                     'VT3270REGIME', 'TTYLOC', 'SUPDUPOUTPUT', 'SUPDUP',
                     'DET', 'BM', 'XASCII', 'RCP', 'NAMS', 'RCTE', 'NAOL',
                     'NAOP', 'NAOCRD', 'NAOHTS', 'NAOHTD', 'NAOFFD', 'NAOVTS',
                     'NAOVTD', 'NAOLFD', )])


def name_command(byte):
    """Return string description for (maybe) telnet command byte."""
    return _DEBUG_OPTS.get(byte, repr(byte))


def name_commands(cmds, sep=' '):
    """Return string description for array of (maybe) telnet command bytes."""
    return sep.join([name_command(bytes([byte])) for byte in cmds])
