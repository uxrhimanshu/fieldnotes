#!/usr/bin/env python3
"""fieldnotes — build a forum corpus you can defend in a methods section.

    python3 fieldnotes.py hn "alert fatigue" --since 2024-01-01 --out corpus/
    python3 fieldnotes.py reddit "onboarding" --subreddit sysadmin --out corpus/

Every run writes the corpus and a sampling log describing exactly how it was drawn.
The log is not optional and cannot be suppressed: a corpus without its provenance is
not evidence, and the moment the two can be separated, they will be.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from corpus import Pseudonymiser, Row, sort_rows, write_csv
from sampling import SamplingLog, VERSION
from sources import SOURCES, FetchError


def build(args, command: str) -> int:
    fetch, to_rows = SOURCES[args.source]

    parameters = {
        "subreddit": getattr(args, "subreddit", None),
        "since": args.since, "until": args.until,
        "thread_limit": args.limit, "include_comments": not args.no_comments,
        "min_length_chars": args.min_length, "min_score": args.min_score,
        "max_depth": args.max_depth, "pseudonymised": not args.real_names,
    }
    log = SamplingLog(args.source, args.query, parameters, command)

    try:
        if args.source == "reddit":
            threads = fetch(args.query, args.subreddit, args.since, args.until,
                            args.limit, not args.no_comments, log)
        else:
            threads = fetch(args.query, args.since, args.until,
                            args.limit, not args.no_comments, log)
    except FetchError as exc:
        print("fetch failed: %s" % exc, file=sys.stderr)
        return 2

    rows = to_rows(threads)
    log.stage("items retrieved (posts + comments)", len(rows))

    rows = _filter(rows, args, log)
    log.stage("items included in corpus", len(rows))
    _check_coverage(rows, args, threads, log)

    # Pseudonymise last, so the funnel counts above describe real retrieval and the
    # mapping only ever covers authors who actually made it into the corpus.
    pseudo = Pseudonymiser(enabled=not args.real_names)
    rows = [Row(**{**r.__dict__, "author": pseudo(r.author)}) for r in rows]
    rows = sort_rows(rows)

    os.makedirs(args.out, exist_ok=True)
    corpus_path = os.path.join(args.out, "corpus.csv")
    write_csv(corpus_path, rows)
    log.write_json(os.path.join(args.out, "sampling-log.json"), rows)
    log.write_markdown(os.path.join(args.out, "SAMPLING.md"), rows)

    written = [corpus_path, os.path.join(args.out, "sampling-log.json"),
               os.path.join(args.out, "SAMPLING.md")]

    if pseudo.enabled and pseudo.count:
        key_path = os.path.join(args.out, "authors.key.json")
        with open(key_path, "w", encoding="utf-8") as handle:
            json.dump({
                "note": "Maps pseudonyms back to real handles. This file re-identifies "
                        "every quote in the corpus. Store it separately, and delete it "
                        "when the study closes.",
                "mapping": pseudo.mapping,
            }, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        written.append(key_path)

    print("%d items across %d threads → %s"
          % (len(rows), len({r.thread_id for r in rows}), args.out))
    for path in written:
        print("  " + path)
    if log.errors:
        print("\n%d fetch error(s) recorded in the sampling log — the corpus is "
              "incomplete in a way that is probably not random." % len(log.errors),
              file=sys.stderr)
    return 0


def _check_coverage(rows: list[Row], args, threads, log) -> None:
    """Warn when --limit, not the query, decided what the corpus contains.

    Search endpoints return newest-first. Ask for three years with a limit of
    twenty and you get the most recent twenty, not twenty spread across three
    years — a corpus that looks like a date-bounded sample and is actually a
    recency sample. The reader cannot spot this from the CSV, so the log says it.
    """
    if not rows:
        log.caveat("The query returned nothing. That is a result about this source "
                   "and this wording, not about the topic.")
        return
    if len(threads) >= args.limit:
        log.caveat(
            "The thread limit (%d) was reached, so the corpus is truncated: it is "
            "the most recent %d matching threads, not all of them. Raise --limit or "
            "narrow the query before treating this as complete coverage."
            % (args.limit, args.limit))
        dates = sorted(r.created_utc[:10] for r in rows if r.created_utc)
        if args.since and dates and dates[0] > args.since:
            log.caveat(
                "Requested items from %s onward, but the earliest item returned is "
                "%s. The gap is a truncation artefact, not evidence that nothing was "
                "posted in between." % (args.since, dates[0]))


def _filter(rows: list[Row], args, log) -> list[Row]:
    """Apply filters one at a time, recording each drop.

    Done as separate passes rather than one predicate so the log can say which rule
    removed what. "487 items excluded" is not a method; "412 below 80 characters,
    75 below score 2" is.
    """
    def drop(predicate, reason):
        nonlocal rows
        keep = [r for r in rows if not predicate(r)]
        log.excluded(reason, len(rows) - len(keep))
        rows = keep

    if args.since:
        drop(lambda r: r.created_utc and r.created_utc[:10] < args.since,
             "posted before %s" % args.since)
    if args.until:
        drop(lambda r: r.created_utc and r.created_utc[:10] >= args.until,
             "posted on or after %s" % args.until)
    if args.max_depth is not None:
        drop(lambda r: r.depth > args.max_depth,
             "nested deeper than %d replies" % args.max_depth)
    if args.min_score is not None:
        drop(lambda r: r.kind == "comment" and r.score < args.min_score,
             "comment scored below %d" % args.min_score)

    drop(lambda r: r.kind == "comment" and not r.text.strip(),
         "comment empty, deleted or removed")

    if args.min_length:
        drop(lambda r: r.kind == "comment" and len(r.text) < args.min_length,
             "comment shorter than %d characters" % args.min_length)

    seen, unique = set(), []
    for row in rows:
        key = (row.thread_id, row.item_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    log.excluded("duplicate item", len(rows) - len(unique))
    return unique


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="fieldnotes",
        description="Build a forum corpus with the sampling log that makes it citable.")
    parser.add_argument("source", choices=sorted(SOURCES), help="where to collect from")
    parser.add_argument("query", help="search terms")
    parser.add_argument("--subreddit", help="restrict a reddit search to one subreddit")
    parser.add_argument("--since", metavar="YYYY-MM-DD", help="drop items before this date")
    parser.add_argument("--until", metavar="YYYY-MM-DD", help="drop items on or after this date")
    parser.add_argument("--limit", type=int, default=25, metavar="N",
                        help="maximum threads to collect (default 25)")
    parser.add_argument("--no-comments", action="store_true",
                        help="collect only the opening posts")
    parser.add_argument("--min-length", type=int, default=0, metavar="CHARS",
                        help="drop comments shorter than this")
    parser.add_argument("--min-score", type=int, metavar="N",
                        help="drop comments scoring below this")
    parser.add_argument("--max-depth", type=int, metavar="N",
                        help="drop replies nested deeper than this")
    parser.add_argument("--real-names", action="store_true",
                        help="keep real handles instead of A1, A2 pseudonyms")
    parser.add_argument("--out", default="corpus", metavar="DIR",
                        help="output directory (default ./corpus)")
    parser.add_argument("--version", action="version", version="fieldnotes " + VERSION)
    args = parser.parse_args(argv)

    command = "python3 fieldnotes.py " + " ".join(
        (a if a.startswith("-") or " " not in a else '"%s"' % a)
        for a in (argv if argv is not None else sys.argv[1:]))
    return build(args, command)


if __name__ == "__main__":
    sys.exit(main())
