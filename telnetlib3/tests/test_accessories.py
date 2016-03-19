from telnetlib3.accessories import (
    encoding_from_lang,
    name_unicode,
    eightbits,
)

def test_name_unicode():
    """ Test mapping of ascii table to name_unicode result. """
    given_expected = {
        chr(0): r'^@',
        chr(1): r'^A',
        chr(26): r'^Z',
        chr(29): r'^]',
        chr(31): r'^_',
        chr(32): r' ',
        chr(126): r'~',
        chr(127): r'^?',
        chr(128): r'\x80',
        chr(254): r'\xfe',
        chr(255): r'\xff',
    }
    for given, expected in sorted(given_expected.items()):
        # exercise,
        result = name_unicode(given)

        # verify,
        assert result == expected

def test_eightbits():
    """ Test mapping of bit values to binary appearance string. """
    given_expected = {
        0: '0b00000000',
        127: '0b01111111',
        128: '0b10000000',
        255: '0b11111111',
    }
    for given, expected in sorted(given_expected.items()):
        # exercise,
        result = eightbits(given)

        # verify
        assert result == expected

def test_encoding_from_lang():
    """ Test inference of encoding from LANG value. """
    given_expected = {
        'en_US.UTF-8@misc': 'UTF-8',
        'en_US.UTF-8': 'UTF-8',
        'abc.def': 'def',
        '.def@ghi': 'def',
        'def@': 'def',
        'UTF-8': 'UTF-8',
    }
    for given, expected in sorted(given_expected.items()):
        # exercise,
        result = encoding_from_lang(given)

        # verify,
        assert result == expected
