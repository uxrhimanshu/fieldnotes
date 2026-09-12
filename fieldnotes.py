#!/usr/bin/env python3
"""fieldnotes — build a forum corpus you can defend in a methods section.

    python3 fieldnotes.py hn "alert fatigue" --since 2024-01-01 --out corpus/
    python3 fieldnotes.py hn "alert fatigue" "security warning" --out corpus/
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
        "items_must_match_a_query_term": getattr(args, "items_must_match", False),
    }
    log = SamplingLog(args.source, args.query, parameters, command)

    # Each query is searched separately and the results merged, rather than the
    # caller running the tool several times and concatenating the CSVs by hand.
    # Hand-merging would leave the corpus with no single log that accounts for
    # it, which is the one thing this tool exists to prevent.
    rows: list[Row] = []
    seen_items: set[str] = set()
    duplicates = 0
    retrieved = 0

    for query in args.query:
        try:
            if args.source == "reddit":
                threads = fetch(query, args.subreddit, args.since, args.until,
                                args.limit, not args.no_comments, log)
            else:
                threads = fetch(query, args.since, args.until,
                                args.limit, not args.no_comments, log)
        except FetchError as exc:
            print("fetch failed: %s" % exc, file=sys.stderr)
            return 2

        found = to_rows(threads)
        retrieved += len(found)
        log.stage("retrieved for query %r" % query, len(found))
        _check_coverage(found, args, threads, log, query)

        for row in found:
            if row.item_id in seen_items:
                duplicates += 1
                continue
            seen_items.add(row.item_id)
            rows.append(row)

    # The retrieval stage counts everything the searches returned, repeats
    # included, so that every exclusion in the log sits *after* it and the funnel
    # reads as one sequence. Recording the cross-query repeats before this line
    # would make the table not add up for anyone who checked.
    log.stage("items retrieved (posts + comments)", retrieved)
    log.excluded("already retrieved under an earlier query", duplicates)

    titles = {r.thread_id: r.title for r in rows if r.kind == "post" and r.title}
    rows = _filter(rows, args, log)
    log.stage("items included in corpus", len(rows))

    # Carry the thread's title onto every comment, but only after filtering. A
    # relevance screen can drop the opening post while keeping replies, and a
    # reply whose thread title has gone is a quote with no context. Doing it
    # before the screen would instead let one on-topic title re-admit every
    # reply in the thread, which is the opposite of what the screen is for.
    rows = [r if r.title else Row(**{**r.__dict__, "title": titles.get(r.thread_id, "")})
            for r in rows]

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


def _check_coverage(rows: list[Row], args, threads, log, query: str) -> None:
    """Warn when --limit, not the query, decided what the corpus contains.

    Search endpoints return newest-first. Ask for three years with a limit of
    twenty and you get the most recent twenty, not twenty spread across three
    years — a corpus that looks like a date-bounded sample and is actually a
    recency sample. The reader cannot spot this from the CSV, so the log says it.
    """
    if not rows:
        log.caveat("The query %r returned nothing. That is a result about this "
                   "source and this wording, not about the topic." % query)
        return
    if len(threads) >= args.limit:
        log.caveat(
            "The thread limit (%d) was reached for %r, so that query's contribution "
            "is truncated: it is the most recent %d matching threads, not all of "
            "them. Raise --limit or narrow the query before treating this as "
            "complete coverage." % (args.limit, query, args.limit))
        dates = sorted(r.created_utc[:10] for r in rows if r.created_utc)
        if args.since and dates and dates[0] > args.since:
            log.caveat(
                "Requested items from %s onward, but the earliest item %r returned "
                "is %s. The gap is a truncation artefact, not evidence that nothing "
                "was posted in between." % (args.since, query, dates[0]))


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

    if getattr(args, "items_must_match", False):
        terms = [q.casefold() for q in args.query]
        drop(lambda r: not any(t in (r.title + " " + r.text).casefold() for t in terms),
             "does not mention any query term")

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
    parser.add_argument("query", nargs="+", metavar="QUERY",
                        help="search terms; give several and they are searched "
                             "separately and merged into one corpus")
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
    parser.add_argument("--items-must-match", action="store_true",
                        help="keep only items that mention a query term. Search "
                             "matches whole threads, so without this a corpus "
                             "carries every reply to a thread that mentioned the "
                             "topic once")
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
