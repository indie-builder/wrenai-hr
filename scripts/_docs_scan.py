"""Shared fence/history scan for Markdown commands and raw source references."""
from __future__ import annotations

import re
from typing import Callable, Iterator, NamedTuple

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
MARKER = re.compile(r"^\s*<!-- docs:(historical(?::start|:end)?|examples) -->\s*$")
RETIRED_SEGMENT = "hr-delivery"
SEGMENT = re.compile(r"[^\w.-]+")


class TextLine(NamedTuple):
    number: int
    text: str
    active: bool


class CodeBlock(NamedTuple):
    language: str
    lines: list[tuple[int, str]]
    active: bool


def scan(text: str, error: Callable[[int, str], None] | None = None) -> Iterator[TextLine | CodeBlock]:
    """Yield prose and closed fences in order; optionally report structural errors.

    Raw references follow the latest range marker, while command validation keeps
    the first unmatched start for diagnostics, including malformed nested ranges.
    Markers inside fences are literal content. Header exemptions apply from their
    position onward; links remain visible even in exempt prose.
    """
    report = error or (lambda number, message: None)
    fence = None
    block = []
    exempt = seen_fence = in_history = False
    history_start = None
    for number, line in enumerate(text.splitlines(), 1):
        match = FENCE.match(line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
                yield CodeBlock(fence[2], block, not exempt and history_start is None)
                fence, block = None, []
            else:
                block.append((number, line))
            continue
        marker = MARKER.match(line)
        if marker:
            kind = marker[1]
            if kind in {"historical", "examples"}:
                if seen_fence:
                    report(number, "整篇历史/模板标记必须放在首个代码块前")
                else:
                    exempt = True
            elif kind == "historical:start":
                in_history = True
                if history_start is not None:
                    report(number, "历史区段不能嵌套")
                else:
                    history_start = number
            else:
                in_history = False
                if history_start is None:
                    report(number, "历史区段 end 缺少 start")
                else:
                    history_start = None
        if match:
            info = match[2].strip().split()
            fence = (match[1][0], len(match[1]), info[0].lower() if info else "", number)
            seen_fence = True
        else:
            yield TextLine(number, line, not (marker or exempt or in_history))
    if fence:
        report(fence[3], "代码围栏未闭合")
    if history_start is not None:
        report(history_start, "历史区段 start 缺少 end")
