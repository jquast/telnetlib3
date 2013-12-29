import asyncio
import socket


def future_hostname(future_gethostbyaddr, fallback_ip):

    if future_gethostbyaddr.done():
        try:
            val = future_gethostbyaddr.result()[0]
        except socket.herror as err:
            if err.errno == 1:
                # Errno 1: Unknown host
                val = fallback_ip
            else:
                raise
        return _wrap_future_result(future_gethostbyaddr, val)
    return future_gethostbyaddr


def future_reverse_ip(future_gethostbyaddr, fallback_ip):
    if future_gethostbyaddr.done():
        try:
            val = future_gethostbyaddr.result()[2][0]
        except socket.herror as err:
            if err.errno == 1:
                # Errno 1: Unknown host
                val = fallback_ip
            else:
                raise
        return _wrap_future_result(future_gethostbyaddr, val)
    return future_gethostbyaddr


def future_fqdn(future_gethostbyaddr, fallback_ip):
    if future_gethostbyaddr.done():
        try:
            val = future_gethostbyaddr.result()[0]
        except socket.herror as err:
            if err.errno == 1:
                # Errno 1: Unknown host
                val = fallback_ip
            else:
                raise
        return _wrap_future_result(future_gethostbyaddr, val)
    return future_gethostbyaddr


def _wrap_future_result(future, result):
    """ Instantiate a future and set the value as ``result``.
    """
    # For some low-level socket data, we want to return only the
    # most-basic string or numeric value, such as IP address,
    # port, hostname, and not the actual socket object represented
    # by the Future result, while still returning asyncio.Future().
    future = asyncio.Future()
    future.set_result(result)
    return future
