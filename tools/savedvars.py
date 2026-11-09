"""Read a WoW SavedVariables file (the Lua tables the client writes at logout / /reload).

Handles the subset the client writes: assignments of tables, strings, numbers,
booleans and nil, with ["key"] or bare keys, positional values and -- comments.
No Lua is executed.
"""
from __future__ import annotations

import re


class LuaSyntaxError(ValueError):
    pass


_NUM = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ESC = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n"}


class _P:
    def __init__(self, s: str):
        self.s, self.i = s, 0

    def ws(self) -> None:
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif s.startswith("--", self.i):
                m = re.match(r"--\[(=*)\[", s[self.i:])
                if m:
                    end = s.find("]" + m.group(1) + "]", self.i)
                    self.i = len(s) if end < 0 else end + len(m.group(1)) + 2
                else:
                    j = s.find("\n", self.i)
                    self.i = len(s) if j < 0 else j
            else:
                break

    def peek(self) -> str:
        self.ws()
        return self.s[self.i] if self.i < len(self.s) else ""

    def eat(self, ch: str) -> None:
        if self.peek() != ch:
            raise LuaSyntaxError("expected %r at %d, found %r" % (ch, self.i, self.s[self.i:self.i + 12]))
        self.i += 1

    def string(self) -> str:
        q = self.s[self.i]
        self.i += 1
        out = []
        while True:
            if self.i >= len(self.s):
                raise LuaSyntaxError("unterminated string")
            c = self.s[self.i]
            if c == q:
                self.i += 1
                return "".join(out)
            if c == "\\":
                self.i += 1
                d = self.s[self.i]
                if d.isdigit():
                    m = re.match(r"\d{1,3}", self.s[self.i:])
                    out.append(chr(int(m.group(0))))
                    self.i += len(m.group(0))
                    continue
                out.append(_ESC.get(d, d))
            else:
                out.append(c)
            self.i += 1

    def value(self):
        c = self.peek()
        if c == "":
            raise LuaSyntaxError("unexpected end of file")
        if c == "{":
            return self.table()
        if c in "\"'":
            return self.string()
        if c == "[" and re.match(r"\[=*\[", self.s[self.i:]):
            m = re.match(r"\[(=*)\[", self.s[self.i:])
            end = self.s.find("]" + m.group(1) + "]", self.i)
            body = self.s[self.i + len(m.group(0)):end]
            self.i = end + len(m.group(1)) + 2
            return body
        m = _NUM.match(self.s, self.i)
        if m:
            self.i = m.end()
            t = m.group(0)
            return int(t, 16) if t.lower().startswith(("0x", "-0x")) else (float(t) if re.search(r"[.eE]", t) else int(t))
        m = _NAME.match(self.s, self.i)
        if m:
            self.i = m.end()
            return {"true": True, "false": False, "nil": None}.get(m.group(0), m.group(0))
        raise LuaSyntaxError("unexpected %r at %d" % (self.s[self.i:self.i + 12], self.i))

    def table(self):
        self.eat("{")
        items, arr = {}, []
        while True:
            c = self.peek()
            if c == "":
                raise LuaSyntaxError("unterminated table")
            if c == "}":
                self.i += 1
                break
            key = None
            if c == "[" and not re.match(r"\[=*\[", self.s[self.i:]):
                self.i += 1
                key = self.value()
                self.eat("]")
                self.eat("=")
            else:
                m = _NAME.match(self.s, self.i) if c else None
                if m:
                    j = m.end()
                    k = j
                    while k < len(self.s) and self.s[k] in " \t":
                        k += 1
                    if k < len(self.s) and self.s[k] == "=" and not self.s.startswith("==", k):
                        key = m.group(0)
                        self.i = k + 1
            v = self.value()
            if key is None:
                arr.append(v)
            else:
                items[key] = v
            if self.peek() in ",;":
                self.i += 1
        if arr and not items:
            return arr
        for n, v in enumerate(arr, 1):
            items[n] = v
        return items


def parse(text: str) -> dict:
    """{'GlobalName': value, ...} for every top-level ``Name = value`` assignment."""
    p = _P(text)
    out = {}
    while True:
        p.ws()
        if p.i >= len(p.s):
            return out
        m = _NAME.match(p.s, p.i)
        if not m:
            raise LuaSyntaxError("expected a name at %d" % p.i)
        p.i = m.end()
        p.eat("=")
        out[m.group(0)] = p.value()
