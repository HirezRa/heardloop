"""Minimal multipart/form-data reader for small, already size-capped request bodies.

The framework's own form parser needs an extra package and spools the whole body before the handler runs; the
analytics import wants to refuse an oversized upload while it is still streaming. The caller reads the body in
chunks (aborting at its cap) and hands the bytes here. Only plain name -> bytes fields are supported.
"""

import re

MAX_PARTS = 10

_BOUNDARY = re.compile(r'boundary=(?:"([^"\r\n]{1,70})"|([^;\s"]{1,70}))', re.IGNORECASE)
_NAME = re.compile(r'(?:^|;)\s*name="([^"\r\n]{1,64})"', re.IGNORECASE)


class FormDataError(ValueError):
    """The body is not a well-formed multipart/form-data payload."""


def parse_multipart(content_type: str, body: bytes) -> dict[str, bytes]:
    """Field name -> raw value. Duplicate names and malformed parts raise `FormDataError`."""
    if not content_type.lower().lstrip().startswith("multipart/form-data"):
        raise FormDataError("not multipart/form-data")
    match = _BOUNDARY.search(content_type)
    if match is None:
        raise FormDataError("missing boundary")
    delimiter = b"\r\n--" + (match.group(1) or match.group(2)).encode("ascii", "strict")
    fields: dict[str, bytes] = {}
    chunks = (b"\r\n" + body).split(delimiter)
    closed = False
    for chunk in chunks[1:]:  # chunks[0] is the preamble
        if chunk.startswith(b"--"):
            closed = True
            break
        start = chunk.find(b"\r\n")
        if start < 0:
            raise FormDataError("malformed part")
        part = chunk[start + 2 :]
        end = part.find(b"\r\n\r\n")
        if end < 0:
            raise FormDataError("malformed part")
        headers = part[:end].decode("latin-1")
        value = part[end + 4 :]
        name = None
        for line in headers.split("\r\n"):
            key, _, rest = line.partition(":")
            if key.strip().lower() == "content-disposition":
                found = _NAME.search(rest)
                name = found.group(1) if found else None
        if name is None or name in fields:
            raise FormDataError("bad field name")
        fields[name] = value
        if len(fields) > MAX_PARTS:
            raise FormDataError("too many parts")
    if not closed:
        raise FormDataError("unterminated body")
    return fields
