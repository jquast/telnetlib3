r"""
Big5-BBS hybrid codec for Taiwanese BBS systems.

Traditional Taiwanese BBS systems (PttBBS, DreamBBS, etc.) send a byte stream
that mixes Big5-encoded Chinese text with single-byte half-width (半形) art
characters whose byte values (0xA1-0xFE) overlap with Big5 lead bytes.  These
lone high bytes appear immediately before ANSI escape sequences (``\x1b[...``)
and cannot form valid Big5 pairs since ESC (0x1B) is not a valid Big5 second
byte (which must be 0x40-0x7E or 0xA1-0xFE).

Decoding algorithm:

- When a Big5 lead byte (0xA1-0xFE) is followed by a valid Big5 second byte
  (0x40-0x7E or 0xA1-0xFE) AND the pair maps to a defined Big5 character,
  the pair is decoded as Big5.
- When a lead byte is followed by a structurally valid second byte but the
  pair is undefined in Big5 (e.g. 0xF9 0xF9), the lone lead byte is decoded
  via CP437 and the second byte is re-processed.  This handles BBS art that
  uses repeated high bytes as decorative fills (e.g. ∙∙∙∙ from 0xF9 runs).
- When a lead byte is followed by any other byte (e.g. ESC), the lone lead
  byte is decoded via CP437 and the following byte is re-processed.
- Bytes below 0xA1 are decoded via latin-1 (identical to ASCII for 0x00-0x7F).
"""

# std imports
import codecs
from typing import Tuple, Union


class Codec(codecs.Codec):
    """Big5-BBS stateless codec (decodes entire buffer at once with final=True)."""

    def encode(self, input: str, errors: str = "strict") -> Tuple[bytes, int]:
        """Encode string to bytes, preferring Big5 with CP437 fallback per character."""
        result = []
        for char in input:
            try:
                result.append(char.encode("big5"))
            except UnicodeEncodeError:
                encoded, _ = codecs.charmap_encode(char, errors, _CP437_ENCODING_TABLE)
                result.append(encoded)
        return b"".join(result), len(input)

    def decode(self, input: bytes, errors: str = "strict") -> Tuple[str, int]:
        """Decode bytes using Big5/CP437 hybrid algorithm."""
        dec = IncrementalDecoder(errors)
        return dec.decode(input, final=True), len(input)


class IncrementalEncoder(codecs.IncrementalEncoder):
    """Big5-BBS incremental encoder; Big5 primary, CP437 fallback."""

    def encode(self, input: str, final: bool = False) -> bytes:
        """Encode input string incrementally."""
        result = []
        for char in input:
            try:
                result.append(char.encode("big5"))
            except UnicodeEncodeError:
                encoded, _ = codecs.charmap_encode(char, self.errors, _CP437_ENCODING_TABLE)
                result.append(encoded)
        return b"".join(result)

    def reset(self) -> None:
        """Reset encoder state (stateless; no-op)."""

    def getstate(self) -> int:
        """Return encoder state."""
        return 0

    def setstate(self, state: Union[int, str]) -> None:
        """Restore encoder state."""


class IncrementalDecoder(codecs.IncrementalDecoder):
    """
    Big5-BBS incremental decoder with one-byte lookahead.

    Holds at most one pending Big5 lead byte between calls.
    """

    def __init__(self, errors: str = "strict") -> None:
        """Initialize decoder with empty pending buffer."""
        super().__init__(errors)
        self._buf: bytes = b""

    def decode(self, input: bytes, final: bool = False) -> str:  # type: ignore[override]
        """Decode input bytes using Big5/CP437 hybrid algorithm."""
        data = self._buf + input
        self._buf = b""
        result = []
        i = 0
        while i < len(data):
            b = data[i]
            if 0xA1 <= b <= 0xFE:
                if i + 1 < len(data):
                    b2 = data[i + 1]
                    if (0x40 <= b2 <= 0x7E) or (0xA1 <= b2 <= 0xFE):
                        try:
                            result.append(bytes([b, b2]).decode("big5", errors="strict"))
                            i += 2
                        except (UnicodeDecodeError, LookupError):
                            # Structurally valid but undefined in Big5 — treat
                            # the lone lead byte as a CP437 half-width character.
                            result.append(bytes([b]).decode("cp437"))
                            i += 1
                    else:
                        result.append(bytes([b]).decode("cp437"))
                        i += 1
                elif not final:
                    self._buf = bytes([b])
                    break
                else:
                    result.append(bytes([b]).decode("cp437"))
                    i += 1
            else:
                result.append(bytes([b]).decode("latin-1"))
                i += 1
        return "".join(result)

    def reset(self) -> None:
        """Reset decoder state."""
        self._buf = b""

    def getstate(self) -> Tuple[bytes, int]:
        """Return decoder state as (buffer, flags) tuple."""
        return (self._buf, 0)

    def setstate(self, state: Tuple[bytes, int]) -> None:
        """Restore decoder state from (buffer, flags) tuple."""
        self._buf = state[0]


class StreamWriter(Codec, codecs.StreamWriter):
    """Big5-BBS stream writer."""


class StreamReader(Codec, codecs.StreamReader):
    """Big5-BBS stream reader."""


def getregentry() -> codecs.CodecInfo:
    """Return the codec registry entry."""
    return codecs.CodecInfo(
        name="big5bbs",
        encode=Codec().encode,
        decode=Codec().decode,  # type: ignore[arg-type]
        incrementalencoder=IncrementalEncoder,
        incrementaldecoder=IncrementalDecoder,
        streamreader=StreamReader,
        streamwriter=StreamWriter,
    )


def getaliases() -> Tuple[str, ...]:
    """Return codec aliases (normalized: hyphens replaced with underscores)."""
    return ("big5_bbs",)


def _build_cp437_encoding_table() -> dict[int, int]:
    """Build a Unicode ordinal → byte value map for CP437."""
    table: dict[int, int] = {}
    for byte_val in range(256):
        char = bytes([byte_val]).decode("cp437")
        table[ord(char)] = byte_val
    return table


_CP437_ENCODING_TABLE = _build_cp437_encoding_table()
