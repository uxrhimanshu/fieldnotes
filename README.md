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
| `reddit` | The public `.json` endpoints. No auth, but increasingly rate-limited — a run returning nothing may be a block rather than an empty result, so check the errors section of the log before concluding anything. |

Requests are deliberately unhurried. This tool is for building a few hundred rows
for a study, not for mirroring a site.

## Options

```
--subreddit NAME     restrict a reddit search to one subreddit
--since / --until    date bounds (applied locally, and reported)
--limit N            maximum threads (default 25)
--no-comments        opening posts only
--min-length CHARS   drop comments shorter than this
--min-score N        drop comments below this score
--max-depth N        drop replies nested deeper than this
--real-names         keep real handles
--out DIR            output directory (default ./corpus)
```

Every filter reports its own drop separately. "487 items excluded" is not a method;
"412 below 80 characters, 75 below score 2" is.

## What this deliberately will not do

- **No authentication, ever.** It reads what a logged-out visitor can read. Private
  subreddits, DMs and members-only forums are out of scope by design.
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

47 cases, no framework, no network — everything runs against fixtures. A test suite
that needs a live API fails for reasons unrelated to the code.

---

Built by [Himanshu Kalra](https://uxrhimanshu.com). MIT licensed. Pairs with
[research-deid](https://github.com/uxrhimanshu/research-deid), which does the same
job for interview transcripts.
