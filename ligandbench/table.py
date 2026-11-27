"""The CSV, read once, with every cell kept as the text it was.

Standard library only. A record is a data row: numbered from one after the
header, which is what a person counting rows in a spreadsheet would say, and
what every message here uses -- never the physical line, which quoted newlines
make a different number.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .errors import BadInput

__all__ = ["Table", "parse_csv"]


@dataclass(frozen=True)
class Table:
    headers: list[str]
    records: list[list[str]]


def parse_csv(data: bytes | str) -> Table:
    """The header and records of CSV text, or a :class:`BadInput` saying what
    is wrong and where.

    A UTF-8 byte-order mark is dropped. Blank lines are not records. Quoted
    commas and newlines are cell text. Headers are trimmed; cells are not.
    """
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise BadInput(
                "the file is not UTF-8 text (%s); save it as CSV UTF-8" % exc.reason
            ) from None
    else:
        text = data.removeprefix("﻿")
    if "\x00" in text:
        raise BadInput("the file contains NUL bytes, so it is not a text CSV")

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    headers: list[str] | None = None
    records: list[list[str]] = []
    try:
        for fields in reader:
            if not fields:  # a blank line
                continue
            if headers is None:
                headers = _check_headers([f.strip() for f in fields])
                continue
            n = len(records) + 1
            if len(fields) != len(headers):
                raise BadInput(
                    "record %d has %d fields but the header has %d" % (n, len(fields), len(headers))
                )
            records.append(fields)
    except csv.Error as exc:
        near = "record %d" % (len(records) + 1) if headers else "the header"
        raise BadInput("the file is not valid CSV near %s: %s" % (near, exc)) from None
    if headers is None:
        raise BadInput("the file is empty")
    if not records:
        raise BadInput("the file has a header but no records")
    return Table(headers, records)


def _check_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    for i, h in enumerate(headers, 1):
        if not h:
            raise BadInput("column %d has an empty header" % i)
        if h in seen:
            raise BadInput("columns %d and %d are both headed %r; rename one" % (seen[h], i, h))
        seen[h] = i
    return headers
