#!/usr/bin/env python3
"""Offline tests — `python3 test_fieldnotes.py`. No network, no dependencies.

Everything here runs against fixtures. A test suite that needs a live API is a test
suite that fails for reasons unrelated to the code, and this tool's whole claim is
that its output can be checked.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from corpus import Pseudonymiser, Row, clean_text, sort_rows, write_csv
from sampling import SamplingLog, UNIVERSAL_CAVEATS
import sources

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  " + name)
    else:
        FAIL += 1
        print("FAIL  " + name + (("\n        " + str(detail)) if detail else ""))


# --------------------------------------------------------------------------
# text cleaning
# --------------------------------------------------------------------------

check("html entities are decoded",
      clean_text("it &quot;works&quot; &amp; is fine") == 'it "works" & is fine',
      clean_text("it &quot;works&quot; &amp; is fine"))

check("paragraph breaks survive tag stripping",
      "\n\n" in clean_text("<p>first</p><p>second</p>"),
      repr(clean_text("<p>first</p><p>second</p>")))

check("runs of spaces collapse but newlines do not",
      clean_text("a    b\n\nc") == "a b\n\nc", repr(clean_text("a    b\n\nc")))

check("empty input is empty, not None", clean_text(None) == "")


# --------------------------------------------------------------------------
# pseudonymisation
# --------------------------------------------------------------------------

p = Pseudonymiser()
check("the same handle always gets the same label", p("alice") == p("alice") == "A1")
check("a different handle gets a different label", p("bob") == "A2")
check("labels are assigned in order of first appearance", p.mapping == {"alice": "A1", "bob": "A2"})
check("a missing author is marked, not invented", p(None) == "[deleted]" and p("") == "[deleted]")
check("the mapping is exposed for the key file", p.count == 2)

off = Pseudonymiser(enabled=False)
check("--real-names passes handles through", off("alice") == "alice" and off.count == 0)


# --------------------------------------------------------------------------
# thread structure from a fixture tree
# --------------------------------------------------------------------------

FIXTURE = {
    "story": {"objectID": "100", "title": "Alert fatigue is real", "author": "op",
              "created_at": "2025-01-05T10:00:00.000Z", "points": 42, "story_text": "<p>Some text</p>"},
    "tree": {
        "id": 100, "children": [
            {"id": 101, "parent_id": 100, "author": "alice", "type": "comment",
             "created_at": "2025-01-05T11:00:00.000Z", "points": 5,
             "text": "We see this &amp; it is bad", "children": [
                 {"id": 102, "parent_id": 101, "author": "bob", "type": "comment",
                  "created_at": "2025-01-05T12:00:00.000Z", "points": 2,
                  "text": "Agreed", "children": []}]},
            {"id": 103, "parent_id": 100, "author": None, "type": "comment",
             "created_at": "2025-01-05T13:00:00.000Z", "points": 0,
             "text": "", "children": []},
        ]
    }
}

rows = sources.hn_rows([FIXTURE])
by_id = {r.item_id: r for r in rows}

check("the opening post becomes one row at depth 0",
      by_id["100"].kind == "post" and by_id["100"].depth == 0)
check("a reply records its parent", by_id["101"].parent_id == "100")
check("nesting depth is preserved", by_id["102"].depth == 2, by_id["102"].depth)
check("every row carries its thread id",
      all(r.thread_id == "100" for r in rows))
check("comment text is cleaned on the way in",
      by_id["101"].text == "We see this & it is bad", by_id["101"].text)
check("permalinks are reconstructed",
      by_id["102"].permalink.endswith("id=102"), by_id["102"].permalink)
check("timestamps are normalised to ISO/UTC",
      by_id["101"].created_utc == "2025-01-05T11:00:00Z", by_id["101"].created_utc)


# --------------------------------------------------------------------------
# the filter funnel
# --------------------------------------------------------------------------

class Args:
    since = until = None
    max_depth = None
    min_score = None
    min_length = 0


import fieldnotes

log = SamplingLog("hn", "alert fatigue", {}, "test")
args = Args()
args.min_length = 10
kept = fieldnotes._filter(list(rows), args, log)

check("an empty/deleted comment is dropped",
      not any(r.item_id == "103" for r in kept))
check("a short comment is dropped by --min-length",
      not any(r.item_id == "102" for r in kept))
check("posts are never dropped by comment-only filters",
      any(r.item_id == "100" for r in kept))

reasons = {e["reason"]: e["count"] for e in log.exclusions}
check("each filter reports its own drop separately",
      any("empty, deleted" in r for r in reasons) and any("shorter than" in r for r in reasons),
      reasons)
check("an exclusion reason states the threshold used",
      any("10 characters" in r for r in reasons), reasons)

log2 = SamplingLog("hn", "q", {}, "test")
args2 = Args()
args2.max_depth = 1
kept2 = fieldnotes._filter(list(rows), args2, log2)
check("--max-depth drops deeper replies",
      not any(r.depth > 1 for r in kept2))

log3 = SamplingLog("hn", "q", {}, "test")
dupes = list(rows) + list(rows)
kept3 = fieldnotes._filter(dupes, Args(), log3)
keys3 = [(r.thread_id, r.item_id) for r in kept3]
check("no duplicate survives dedup",
      len(keys3) == len(set(keys3)), keys3)
check("dedup counts what it removed",
      any(e["reason"] == "duplicate item" and e["count"] == len(keys3)
          for e in log3.exclusions),
      log3.exclusions)



# --------------------------------------------------------------------------
# the coverage check — the tool diagnosing its own sampling bias
# --------------------------------------------------------------------------

class CovArgs(Args):
    limit = 2
    since = "2023-01-01"

log5 = SamplingLog("hn", "q", {}, "test")
fieldnotes._check_coverage(list(rows), CovArgs(), [1, 2], log5, "q")
check("hitting --limit is reported as truncation",
      any("truncated" in c for c in log5.caveats), log5.caveats[-2:])
check("a since/observed gap is named as an artefact, not a finding",
      any("truncation artefact" in c and "2023-01-01" in c for c in log5.caveats),
      log5.caveats[-1])

class RoomArgs(Args):
    limit = 50
    since = "2023-01-01"

log6 = SamplingLog("hn", "q", {}, "test")
fieldnotes._check_coverage(list(rows), RoomArgs(), [1, 2], log6, "q")
check("a corpus under the limit is not flagged as truncated",
      not any("truncated" in c for c in log6.caveats))

log7 = SamplingLog("hn", "q", {}, "test")
fieldnotes._check_coverage([], CovArgs(), [], log7, "q")
check("an empty result is reported as a result, not silence",
      any("returned nothing" in c for c in log7.caveats), log7.caveats[-1])


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------

import random
shuffled = list(rows)
random.Random(1).shuffle(shuffled)
check("sort order does not depend on input order",
      [r.item_id for r in sort_rows(shuffled)] == [r.item_id for r in sort_rows(list(rows))])


# --------------------------------------------------------------------------
# the sampling log
# --------------------------------------------------------------------------

log4 = SamplingLog("hn", "alert fatigue",
                   {"since": "2024-01-01", "thread_limit": 25}, "python3 fieldnotes.py hn ...")
log4.endpoint("https://hn.algolia.com/api/v1/search_by_date?query=x&page=0")
log4.endpoint("https://hn.algolia.com/api/v1/search_by_date?query=x&page=1")
log4.stage("stories matched by search", 12)
log4.stage("items retrieved (posts + comments)", 400)
log4.excluded("comment shorter than 80 characters", 120)
log4.excluded("comment shorter than 80 characters", 30)
log4.stage("items included in corpus", 250)
log4.error("could not fetch comments for story 999: HTTP 503")

rec = log4.record(rows)
check("the log records the query and parameters",
      rec["queries"] == ["alert fatigue"] and rec["parameters"]["since"] == "2024-01-01")
check("query-string noise is stripped from endpoints",
      rec["endpoints"] == ["https://hn.algolia.com/api/v1/search_by_date"], rec["endpoints"])
check("the funnel keeps its stages in order",
      [s["stage"] for s in rec["funnel"]][0] == "stories matched by search")
check("repeated exclusions accumulate instead of duplicating",
      len(rec["exclusions"]) == 1 and rec["exclusions"][0]["count"] == 150, rec["exclusions"])
check("universal caveats are always present",
      all(c in rec["caveats"] for c in UNIVERSAL_CAVEATS))
check("the observed date range comes from the data, not the request",
      rec["result"]["observed_date_range"]["earliest"].startswith("2025-01-05"),
      rec["result"]["observed_date_range"])
check("fetch errors are recorded, not swallowed", len(rec["errors"]) == 1)
check("posts and comments are counted separately",
      rec["result"]["posts"] == 1 and rec["result"]["comments"] == 3, rec["result"])


# --------------------------------------------------------------------------
# files on disk
# --------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    csv_path = os.path.join(tmp, "corpus.csv")
    n = write_csv(csv_path, sort_rows(list(rows)))
    body = open(csv_path, encoding="utf-8").read()
    check("csv is written with a header row", body.startswith("thread_id,item_id,parent_id,depth,kind"))
    check("csv row count matches the corpus", n == len(rows))

    md_path = os.path.join(tmp, "SAMPLING.md")
    log4.write_markdown(md_path, rows)
    md = open(md_path, encoding="utf-8").read()
    check("the markdown log names the source and query",
          "alert fatigue" in md and "hn" in md)
    check("the markdown log shows the funnel as a table", "| Stage | Items |" in md)
    check("the markdown log states what the sample cannot support",
          "cannot support" in md and UNIVERSAL_CAVEATS[0][:40] in md)
    check("the markdown log carries the reproducing command",
          "python3 fieldnotes.py hn" in md)
    check("the markdown log surfaces fetch errors", "HTTP 503" in md)

    json_path = os.path.join(tmp, "sampling-log.json")
    log4.write_json(json_path, rows)
    loaded = json.load(open(json_path, encoding="utf-8"))
    check("the json log parses and matches the record",
          loaded["queries"] == ["alert fatigue"] and loaded["tool"] == "fieldnotes")


# --------------------------------------------------------------------------
# several queries, one corpus
#
# A study needs more than one search term, and merging separate runs by hand
# would leave the corpus with no single log that accounts for it. So the merge
# happens inside the tool, and the funnel has to stay arithmetically honest
# across it.
# --------------------------------------------------------------------------

def _fake_source(by_query):
    """A fetcher that returns canned rows per query, in the SOURCES shape."""
    def fetch(query, since, until, limit, with_comments, log):
        log.endpoint("https://example.test/search?q=" + query)
        return [{"q": query}]

    def to_rows(threads):
        out = []
        for thread in threads:
            out.extend(by_query[thread["q"]])
        return out
    return fetch, to_rows


def _row(item_id, text="a comment long enough to survive the length filter"):
    return Row(thread_id="t" + item_id, item_id=item_id, parent_id="", depth=1,
               kind="comment", author="someone", created_utc="2025-06-01T00:00:00Z",
               score=5, title="", text=text, permalink="https://example.test/" + item_id)


class MultiArgs(Args):
    source = "fake"
    subreddit = None
    limit = 50
    no_comments = False
    real_names = False
    query = ["warning", "certificate"]


shared = _row("200")
fieldnotes.SOURCES = dict(fieldnotes.SOURCES, fake=_fake_source({
    "warning": [_row("100"), shared],
    "certificate": [shared, _row("300")],       # 200 is returned by both
}))

with tempfile.TemporaryDirectory() as tmp:
    margs = MultiArgs()
    margs.out = tmp
    code = fieldnotes.build(margs, "python3 fieldnotes.py fake warning certificate")
    check("a multi-query run completes", code == 0)

    record = json.load(open(os.path.join(tmp, "sampling-log.json"), encoding="utf-8"))
    check("the log keeps every query searched, not just the first",
          record["queries"] == ["warning", "certificate"], record["queries"])

    funnel = {s["stage"]: s["items"] for s in record["funnel"]}
    check("each query's own yield is recorded",
          funnel["retrieved for query 'warning'"] == 2
          and funnel["retrieved for query 'certificate'"] == 2, funnel)

    dupes = {e["reason"]: e["count"] for e in record["exclusions"]}
    check("an item returned by two queries is counted once and the repeat logged",
          dupes.get("already retrieved under an earlier query") == 1, dupes)

    # The check a hostile reader runs first: does the funnel add up, in order?
    per_query = sum(v for k, v in funnel.items() if k.startswith("retrieved for query"))
    check("the per-query yields sum to items retrieved",
          per_query == funnel["items retrieved (posts + comments)"], funnel)
    check("items retrieved minus every exclusion equals items included",
          funnel["items retrieved (posts + comments)"] - sum(dupes.values())
          == funnel["items included in corpus"], (funnel, dupes))

    md = open(os.path.join(tmp, "SAMPLING.md"), encoding="utf-8").read()
    check("the markdown log lists all the queries, including in the merged case",
          "`warning`" in md and "`certificate`" in md, md[:400])

# the relevance screen: the thread is retrieved, the item is analysed
class ScreenArgs(Args):
    query = ["certificate warning"]
    items_must_match = True


screen_rows = [
    Row(thread_id="t1", item_id="s1", parent_id="", depth=0, kind="post",
        author="a", created_utc="2025-01-01T00:00:00Z", score=1,
        title="Launch HN: something entirely unrelated", text="a" * 200,
        permalink="p"),
    Row(thread_id="t1", item_id="c1", parent_id="s1", depth=1, kind="comment",
        author="b", created_utc="2025-01-01T00:00:00Z", score=1, title="",
        text="I clicked past the certificate warning because the deploy was blocked.",
        permalink="p"),
    Row(thread_id="t1", item_id="c2", parent_id="s1", depth=1, kind="comment",
        author="c", created_utc="2025-01-01T00:00:00Z", score=1, title="",
        text="Unrelated reply about the pricing page and nothing else at all here.",
        permalink="p"),
]
log9 = SamplingLog("hn", ["certificate warning"], {}, "test")
screened = fieldnotes._filter(list(screen_rows), ScreenArgs(), log9)
kept_ids = {r.item_id for r in screened}
check("an item that mentions no query term is screened out",
      kept_ids == {"c1"}, kept_ids)
check("the screen is named in the log rather than applied silently",
      any(e["reason"] == "does not mention any query term" and e["count"] == 2
          for e in log9.exclusions), log9.exclusions)
check("the screen is off unless asked for",
      len(fieldnotes._filter(list(screen_rows), Args(), SamplingLog("hn", "q", {}, "t"))) == 3)

log8 = SamplingLog("hn", ["a", "b"], {}, "test")
check("a list of queries and a bare string are both accepted",
      log8.queries == ["a", "b"] and SamplingLog("hn", "a", {}, "t").queries == ["a"])


# --------------------------------------------------------------------------
# Reddit needs credentials now, and has to say so
#
# The public .json endpoints started returning 403 to every unauthenticated
# client. A bare "HTTP 403" reads as a transient block and invites a retry that
# will never succeed, so the error has to name the cause and the fix.
# --------------------------------------------------------------------------

import urllib.error


def _raise_403(*_a, **_k):
    raise urllib.error.HTTPError("https://oauth.reddit.com/search", 403, "Forbidden", {}, None)


_real_urlopen = sources.urllib.request.urlopen
sources.urllib.request.urlopen = _raise_403
try:
    sources._get("https://oauth.reddit.com/search?q=x")
    check("a Reddit 403 is raised, not swallowed", False)
except sources.FetchError as exc:
    message = str(exc)
    check("a Reddit 403 explains the cause rather than repeating the status code",
          "OAuth" in message and "prefs/apps" in message, message[:120])
    check("it says the condition is permanent, so nobody retries it forever",
          "permanent" in message, message[:120])
except Exception as exc:
    check("a Reddit 403 is raised, not swallowed", False, repr(exc))
finally:
    sources.urllib.request.urlopen = _real_urlopen

_saved = {k: os.environ.pop(k, None) for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")}
try:
    sources.reddit_token()
    check("missing Reddit credentials fail before any request is made", False)
except sources.FetchError as exc:
    check("missing Reddit credentials fail before any request is made",
          "REDDIT_CLIENT_ID" in str(exc))
finally:
    for k, v in _saved.items():
        if v is not None:
            os.environ[k] = v

sources.urllib.request.urlopen = _raise_403
try:
    sources._get("https://hn.algolia.com/api/v1/search?query=x")
    check("a non-Reddit 403 still fails", False)
except sources.FetchError as exc:
    check("the Reddit advice is not attached to unrelated 403s",
          str(exc).startswith("HTTP 403 for") and "prefs/apps" not in str(exc), str(exc))
finally:
    sources.urllib.request.urlopen = _real_urlopen


# --------------------------------------------------------------------------
# the CLI contract
# --------------------------------------------------------------------------

try:
    fieldnotes.main(["nosuchsource", "q"])
    check("an unknown source is rejected", False)
except SystemExit as exc:
    check("an unknown source is rejected", exc.code != 0)

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
