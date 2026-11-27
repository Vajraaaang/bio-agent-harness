"""Stream JSON summary records and plot sidecars with a bounded individual value."""

from __future__ import annotations

import io
import json
from itertools import islice

from devbench.engine.workflow_operations import OperationBlocked

from ._sources import BLOCK, QUOTA, check_time, clinical


class _Tokens:
    def __init__(self, reader, until):
        self.reader = reader
        self.until = until
        self.text = ""
        self.pos = 0
        self.eof = False
        self.decoder = json.JSONDecoder()

    def _more(self):
        check_time(self.until)
        chunk = self.reader.read(65536)
        self.text = self.text[self.pos :] + chunk
        self.pos = 0
        self.eof = not chunk

    def peek(self):
        while True:
            while self.pos < len(self.text) and self.text[self.pos].isspace():
                self.pos += 1
            if self.pos < len(self.text):
                return self.text[self.pos]
            if self.eof:
                return ""
            self._more()

    def take(self, symbol):
        if self.peek() != symbol:
            raise ValueError("Invalid JSON table framing")
        self.pos += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.text, self.pos)
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError("Invalid JSON value") from None
            else:
                if end < len(self.text) or self.eof:
                    self.pos = end
                    return value
            if len(self.text) - self.pos > BLOCK:
                raise OperationBlocked(QUOTA)
            self._more()

    def rows(self):
        self.take("[")
        if self.peek() != "]":
            yield "row", self.value()
            while self.peek() == ",":
                self.take(",")
                yield "row", self.value()
        self.take("]")


def _columns(tokens):
    columns = tokens.value()
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise ValueError("Expected a flat column list")
    return columns


def _object(tokens):
    tokens.take("{")
    fields = {}
    tabular = False
    keys = set()
    while tokens.peek() != "}":
        if keys:
            tokens.take(",")
        key = tokens.value()
        if not isinstance(key, str) or key in keys:
            raise ValueError("Duplicate or invalid JSON summary field")
        keys.add(key)
        if len(keys) > 4096:
            raise OperationBlocked(QUOTA)
        tokens.take(":")
        if key in {"data", "rows"} and tokens.peek() == "[":
            if tabular:
                raise ValueError("Ambiguous summary row collection")
            yield from tokens.rows()
            tabular = True
        elif key == "columns":
            yield "columns", _columns(tokens)
        else:
            fields[key] = tokens.value()
            if len(json.dumps(fields, ensure_ascii=False)) > BLOCK:
                raise OperationBlocked(QUOTA)
    tokens.take("}")
    if tabular:
        yield "metadata", fields
    else:
        yield "row", fields


def _events(stream, until):
    stream.seek(0)
    reader = io.TextIOWrapper(stream, encoding="utf-8-sig")
    tokens = _Tokens(reader, until)
    try:
        if tokens.peek() == "[":
            yield from tokens.rows()
        else:
            yield from _object(tokens)
        if tokens.peek():
            raise ValueError("Unexpected trailing JSON content")
    finally:
        reader.detach()


def _private_keys(value, manifest):
    if isinstance(value, str):
        return manifest and clinical(value)
    if isinstance(value, dict):
        return any(
            clinical(key) or _private_keys(val, manifest) for key, val in value.items()
        )
    if isinstance(value, list):
        return any(_private_keys(v, manifest) for v in value)
    return False


def header(stream, until, *, manifest=False):
    count = 0
    declared = None
    names = {}
    hidden = False
    max_width = 0
    for kind, row in _events(stream, until):
        if kind == "columns":
            declared = row
            hidden |= any(map(clinical, row))
        elif kind == "metadata":
            hidden |= _private_keys(row, manifest)
        elif isinstance(row, dict):
            count += 1
            hidden |= _private_keys(row, manifest)
            names.update(dict.fromkeys(row))
        elif isinstance(row, list):
            count += 1
            max_width = max(len(row), max_width)
            hidden |= _private_keys(row, manifest)
        else:
            raise ValueError("JSON summaries require records or rows")
        if len(names) > 4096:
            raise OperationBlocked(QUOTA)
    names = list(names) if declared is None else declared
    if len(set(names)) != len(names) or max_width > len(names):
        raise ValueError("JSON table fields disagree with the row layout")
    return names, count, hidden


def reader(stream, until):
    fields, count, hidden = header(stream, until)
    if hidden:
        from ._sources import PRIVATE

        raise OperationBlocked(PRIVATE)

    def scan(columns):
        indices = [fields.index(key) for key in columns]
        for kind, row in _events(stream, until):
            if kind != "row":
                continue
            if isinstance(row, dict):
                yield tuple(row.get(key) for key in columns)
            else:
                if len(row) != len(fields):
                    raise ValueError("JSON row width changed")
                yield tuple(row[i] for i in indices)

    def rows(columns, start=0, limit=None):
        stop = None if limit is None else start + limit
        return islice(scan(columns), start, stop)

    return fields, rows, count
