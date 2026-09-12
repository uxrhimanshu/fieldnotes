# fieldnotes

Build a forum corpus you can defend in a methods section.

```sh
python3 fieldnotes.py hn "alert fatigue" --since 2024-01-01 --min-length 80 --out corpus/
```

Every run writes the corpus **and a sampling log** describing exactly how it was
drawn — what was asked for, what came back, what was thrown away and why, and what
the method could not see at all.

No dependencies. Python 3.9+, standard library only.

---

## Why another scraper

There are plenty of scrapers. What there isn't much of is a scraper that leaves
behind the paperwork that turns a pile of comments into evidence.

If you quote a forum post in a findings deck, someone can reasonably ask: how did
you choose that thread? How many did you look at? What did you exclude? Would you
have found the opposite view if it were there? Most netnographic work answers those
questions from memory, weeks later, if at all.

So the sampling log here is not a feature you can switch on. It is written on every
run, from the same record as the data, and it cannot be suppressed — because the
moment a corpus can be separated from its provenance, it will be.

## What the log contains

A real one, from the example in this repo:

```
## What came back

| Stage                              | Items |
| ---------------------------------- | ----: |
| stories matched by search          |     6 |
| items retrieved (posts + comments) |    61 |
| items included in corpus           |    54 |

### What was excluded, and why

| Reason                             | Items |
| ---------------------------------- | ----: |
| comment shorter than 80 characters |     7 |
```

Plus the query and every parameter, the endpoints hit, the observed date range, any
fetch errors, the exact command to re-run it, and a standing list of what a
public-forum sample cannot support.

### It reports its own sampling bias

This is the part that earns the tool. That example asked for everything since
2023 — and the log said so itself:

> Requested items from 2023-01-01 onward, but the earliest item returned is
> 2025-12-25. The gap is a truncation artefact, not evidence that nothing was
> posted in between.

Search endpoints return newest-first. Ask for three years with a limit of six and
you get the six most recent threads, not six spread across three years. The corpus
*looks* like a date-bounded sample and is actually a recency sample. You cannot see
that from the CSV. The log catches it and names it.

It also flags when a query returned nothing — which is a result about your wording
and that community, not about the topic.

## The standing caveats

Written into every log, so no write-up can quietly omit them:

- Deleted and moderator-removed content is invisible. A thread's most contested
  contributions are the most likely to be missing.
- **Posters are not users.** The overwhelming majority of people who read a forum
  never post, and nothing here represents them.
- Visibility is ranked, not neutral — what search returns is shaped by the
  platform's own relevance ordering, which tracks engagement, not representativeness.
- Self-selection: people post when an experience is unusual, unresolved or
  annoying. Base rates cannot be estimated from this data.
- Identity is unverified. Claimed roles, employers and seniority are claims.

Source adapters add their own. The Hacker News one notes that it is a single
community — largely English-speaking, technical, US-weighted — and generalises
nowhere.

## What you get

```
corpus/
  corpus.csv           one row per post or comment
  sampling-log.json    the machine-readable record
  SAMPLING.md          the same record, written for a methods section
  authors.key.json     pseudonym → real handle  (do not commit this)
```

`corpus.csv` keeps the thread structure — `thread_id`, `parent_id`, `depth`, `kind`
— so you can reconstruct who was replying to whom. Flattening a conversation into a
bag of text throws away the thing that makes forum data worth reading.

### Authors are pseudonymised by default

Handles become `A1`, `A2`, stable across the corpus. A public handle is an
identifier: it links a quote in your findings back to a real person's entire
posting history, which is usually far more exposure than they imagined when they
replied to a thread. `--real-names` turns it off if your protocol calls for it.

The mapping goes in a separate key file, for the same reason a de-identification key
does: it is the one artifact that can undo the protection. Keep it apart from the
corpus, and delete it when the study closes. It is gitignored here.

## Sources

| | |
|---|---|
| `hn` | Hacker News via the Algolia API. Open, no auth, reliable. |
| `reddit` | The OAuth API. **Needs credentials** — see below. |

### Reddit needs credentials now

This adapter used to read Reddit's public `.json` endpoints. Those endpoints now
return **HTTP 403 to every unauthenticated client**, browser User-Agent or not. It
is not a rate limit and it does not recover.

So reading Reddit means registering a script app at
[reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) and exporting two
variables:

```sh
export REDDIT_CLIENT_ID=...
export REDDIT_CLIENT_SECRET=...
```

Without them the tool says exactly that, rather than reporting a bare `HTTP 403`
that reads like a transient block and invites a retry that can never succeed.
Hacker News is unaffected and needs nothing.

Requests to either source are deliberately unhurried. This tool is for building a
few hundred rows for a study, not for mirroring a site.

## Options

```
--subreddit NAME     restrict a reddit search to one subreddit
--since / --until    date bounds (applied locally, and reported)
--limit N            maximum threads (default 25)
--no-comments        opening posts only
--min-length CHARS   drop comments shorter than this
--min-score N        drop comments below this score
--max-depth N        drop replies nested deeper than this
--items-must-match   keep only items that mention a query term
--real-names         keep real handles
--out DIR            output directory (default ./corpus)
```

Every filter reports its own drop separately. "487 items excluded" is not a method;
"412 below 80 characters, 75 below score 2" is.

### Several queries, one corpus

Give more than one search term and each is searched separately and merged:

```sh
python3 fieldnotes.py hn "alert fatigue" "warning fatigue" --out corpus/
```

The log records each query's own yield and counts anything the second query
already collected as `already retrieved under an earlier query`. This exists
because the alternative — running the tool several times and concatenating the
CSVs — leaves a corpus that no single log accounts for, which is the one thing
this tool is for.

### The thread is retrieved; the item is analysed

Search matches whole threads, so a thread that mentions your topic once arrives
with every reply attached, including the ones about pricing. In a real run on
security-warning talk that was most of the corpus: Launch HN posts matched on a
phrase buried in a long blurb, and dragged ninety off-topic comments in with them.

`--items-must-match` keeps only items that mention one of your query terms. It is
a blunt screen and it is deliberately blunt — it is mechanical, reproducible from
the command line alone, and counted in the log as
`does not mention any query term`, which is not true of deciding item by item
afterwards which ones felt relevant.

A thread's title is copied onto its comments *after* the screen runs, so a quote
keeps its context without one on-topic headline re-admitting the whole thread.

## What this deliberately will not do

- **Nothing that isn't public.** It reads what any visitor can read. Private
  subreddits, DMs and members-only forums are out of scope by design. This used to
  be stated as "no authentication, ever" — Reddit ended that by closing its
  logged-out endpoints, so the rule is now about *what* is read rather than about
  credentials.
- **No mirroring.** The rate limiting is not configurable upward.
- **No sentiment scores, no topic models, no LLM summarisation.** It builds the
  corpus; the interpretation is yours, and it should be visible in your codebook
  rather than buried in a model you didn't inspect.

## Ethics, briefly

Public does not mean consenting to be studied. Before you publish quotes, consider
whether a verbatim string will lead straight back to the author through a search
engine — pseudonymising the handle does nothing about that. Paraphrasing, or asking,
is often the right call. Check what your institution or employer requires; this tool
does not know your obligations.

## Running the tests

```sh
python3 test_fieldnotes.py
```

62 cases, no framework, no network — everything runs against fixtures. A test suite
that needs a live API fails for reasons unrelated to the code.

---

Built by [Himanshu Kalra](https://uxrhimanshu.com). MIT licensed. Pairs with
[research-deid](https://github.com/uxrhimanshu/research-deid), which does the same
job for interview transcripts.
