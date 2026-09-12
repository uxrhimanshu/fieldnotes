"""Corpus rows, author pseudonymisation and CSV output.

A corpus row is one post or one comment, keeping enough of the thread structure
(parent, depth, thread id) that a reader can reconstruct who was replying to whom.
Flattening a conversation into a bag of text loses the thing that makes forum data
worth reading.
"""
from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass, asdict, field
from typing import Iterable

FIELDS = [
    "thread_id", "item_id", "parent_id", "depth", "kind",
    "author", "created_utc", "score", "title", "text", "permalink",
]

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


def clean_text(raw: str | None) -> str:
    """Unescape and de-tag, but keep paragraph breaks.

    Forum text arrives as HTML fragments. Collapsing it to a single line would
    destroy quoting and list structure, which are often exactly what a reader is
    coding for, so only runs of spaces are collapsed.
    """
    if not raw:
        return ""
    text = raw.replace("</p><p>", "\n\n").replace("<p>", "\n\n").replace("<br>", "\n")
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()


@dataclass
class Row:
    thread_id: str
    item_id: str
    parent_id: str
    depth: int
    kind: str                # "post" or "comment"
    author: str
    created_utc: str         # ISO 8601, UTC
    score: int
    title: str
    text: str
    permalink: str


class Pseudonymiser:
    """Maps author handles to stable A1, A2, … labels.

    On by default. A public handle is an identifier: it links a quote in your
    findings back to a real person's whole posting history, which is usually more
    exposure than the person imagined when they replied to a thread. The mapping is
    written to a separate file so it can be kept apart from the corpus, or deleted.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._map: dict[str, str] = {}

    def __call__(self, author: str | None) -> str:
        author = (author or "").strip()
        if not author:
            return "[deleted]"
        if not self.enabled:
            return author
        if author not in self._map:
            self._map[author] = "A%d" % (len(self._map) + 1)
        return self._map[author]

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._map)

    @property
    def count(self) -> int:
        return len(self._map)


def write_csv(path, rows: Iterable[Row]) -> int:
    rows = list(rows)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    return len(rows)


def sort_rows(rows: list[Row]) -> list[Row]:
    """Deterministic order: thread, then reading order within the thread.

    Two runs over the same data must produce byte-identical files, or the sampling
    log is describing something the reader cannot check.
    """
    return sorted(rows, key=lambda r: (r.thread_id, r.created_utc, r.depth, r.item_id))
