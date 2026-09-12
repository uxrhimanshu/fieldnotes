"""Source adapters. Standard library only — no requests, no praw, no auth dance.

Each adapter yields raw thread dicts and records what it hit in the sampling log.
Adapters are deliberately thin: filtering and pseudonymisation happen once, in the
pipeline, so every source is treated identically and the funnel in the log means
the same thing regardless of where the data came from.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from corpus import Row, clean_text

USER_AGENT = "fieldnotes/0.1 (research corpus builder; +https://github.com/uxrhimanshu/fieldnotes)"

# Deliberately unhurried. This tool exists to build a few hundred rows for a study,
# not to mirror a site, and a polite pace is the difference between a tool that
# keeps working and one that gets its user blocked.
PAUSE_SECONDS = 0.7


class FetchError(Exception):
    pass


REDDIT_AUTH_HELP = """Reddit's public .json endpoints no longer serve
unauthenticated clients, so this is a permanent condition rather than a
transient one: retrying, waiting, or changing the User-Agent will not help.
Reading public posts now requires OAuth.

To collect from Reddit:

  1. Create an app at https://www.reddit.com/prefs/apps — choose type "script".
  2. Export the credentials it gives you:

       export REDDIT_CLIENT_ID=...        # under the app name
       export REDDIT_CLIENT_SECRET=...    # the "secret" field

The free tier allows roughly 100 requests per minute, which is far more than this
tool will ever use. Hacker News needs no credentials and is unaffected."""


def _get(url: str, log=None, retries: int = 3, headers: dict | None = None):
    if log is not None:
        log.endpoint(url)
    sent = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    sent.update(headers or {})
    request = urllib.request.Request(url, headers=sent)
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            # 429 and 5xx are worth waiting out; a 403 or 404 will not improve.
            if exc.code not in (429, 500, 502, 503, 504):
                if exc.code == 403 and "reddit.com" in url:
                    raise FetchError(
                        "Reddit refused the request (HTTP 403).\n\n"
                        + REDDIT_AUTH_HELP) from exc
                raise FetchError("HTTP %s for %s" % (exc.code, url)) from exc
            time.sleep(2 ** attempt)
        except Exception as exc:                      # network-level failure
            last = exc
            time.sleep(2 ** attempt)
    raise FetchError("gave up on %s (%s)" % (url, last))


# --------------------------------------------------------------------------
# Hacker News, via the Algolia search API
# --------------------------------------------------------------------------

HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM = "https://hn.algolia.com/api/v1/items/%s"


def hn_threads(query: str, since: str | None, until: str | None, limit: int,
               with_comments: bool, log) -> list[dict]:
    """Search stories, then pull each matching story's full comment tree."""
    log.caveat(
        "Hacker News is a single community with a specific demographic — largely "
        "English-speaking, technical and US-weighted. Nothing here generalises "
        "beyond it."
    )

    filters = []
    if since:
        filters.append("created_at_i>=%d" % _epoch(since))
    if until:
        filters.append("created_at_i<%d" % _epoch(until))

    stories, page = [], 0
    while len(stories) < limit:
        params = {
            "query": query, "tags": "story",
            "hitsPerPage": str(min(100, limit - len(stories))), "page": str(page),
        }
        if filters:
            params["numericFilters"] = ",".join(filters)
        data = _get(HN_SEARCH + "?" + urllib.parse.urlencode(params), log)
        hits = data.get("hits", [])
        if not hits:
            break
        stories.extend(hits)
        page += 1
        if page >= data.get("nbPages", 1):
            break
        time.sleep(PAUSE_SECONDS)

    stories = stories[:limit]
    log.stage("stories matched by %r" % query, len(stories))

    threads = []
    for story in stories:
        thread = {"story": story, "tree": None}
        if with_comments:
            try:
                thread["tree"] = _get(HN_ITEM % story["objectID"], log)
            except FetchError as exc:
                log.error("could not fetch comments for story %s: %s" % (story["objectID"], exc))
            time.sleep(PAUSE_SECONDS)
        threads.append(thread)
    return threads


def hn_rows(threads: list[dict]) -> list[Row]:
    rows: list[Row] = []
    for thread in threads:
        story = thread["story"]
        sid = str(story["objectID"])
        rows.append(Row(
            thread_id=sid, item_id=sid, parent_id="", depth=0, kind="post",
            author=story.get("author") or "", created_utc=_iso(story.get("created_at")),
            score=int(story.get("points") or 0), title=story.get("title") or "",
            text=clean_text(story.get("story_text")),
            permalink="https://news.ycombinator.com/item?id=%s" % sid,
        ))
        tree = thread.get("tree")
        if tree:
            _walk_hn(tree, sid, 0, rows)
    return rows


def _walk_hn(node: dict, thread_id: str, depth: int, out: list[Row]) -> None:
    for child in node.get("children") or []:
        cid = str(child.get("id"))
        out.append(Row(
            thread_id=thread_id, item_id=cid, parent_id=str(child.get("parent_id") or ""),
            depth=depth + 1, kind="comment",
            author=child.get("author") or "", created_utc=_iso(child.get("created_at")),
            score=int(child.get("points") or 0), title="",
            text=clean_text(child.get("text")),
            permalink="https://news.ycombinator.com/item?id=%s" % cid,
        ))
        _walk_hn(child, thread_id, depth + 1, out)


# --------------------------------------------------------------------------
# Reddit, via the OAuth API
#
# The public www.reddit.com/*.json endpoints this adapter originally used now
# return 403 to every unauthenticated client, browser User-Agent or not. That is
# not a rate limit and it does not recover, so the adapter authenticates instead
# of pretending. Credentials come from the environment and are never written to
# the sampling log.
# --------------------------------------------------------------------------

REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"


def reddit_token() -> str:
    """Application-only OAuth token, or a FetchError explaining how to get one."""
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not client_id or not secret:
        raise FetchError(
            "Reddit needs credentials: REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET "
            "are not set.\n\n" + REDDIT_AUTH_HELP)

    import base64
    basic = base64.b64encode(("%s:%s" % (client_id, secret)).encode()).decode()
    request = urllib.request.Request(
        REDDIT_TOKEN_URL,
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={"User-Agent": USER_AGENT, "Authorization": "Basic " + basic},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            token = json.loads(response.read().decode("utf-8")).get("access_token")
    except urllib.error.HTTPError as exc:
        raise FetchError(
            "Reddit rejected those credentials (HTTP %s). Check that the app at "
            "https://www.reddit.com/prefs/apps is of type \"script\", and that "
            "REDDIT_CLIENT_ID is the string under the app name rather than its "
            "title." % exc.code) from exc
    if not token:
        raise FetchError("Reddit returned no access token.")
    return token


def reddit_threads(query: str, subreddit: str | None, since: str | None, until: str | None,
                   limit: int, with_comments: bool, log) -> list[dict]:
    log.caveat(
        "Reddit was read through the authenticated API. Private, quarantined and "
        "banned subreddits are invisible to it, as is anything removed before "
        "collection — so absence here is not evidence of absence."
    )
    log.caveat(
        "Reddit search does not reliably honour date bounds. Any date filtering "
        "here is applied locally, after fetching, so the funnel shows what was "
        "actually removed."
    )

    auth = {"Authorization": "Bearer " + reddit_token()}
    path = "/r/%s/search" % subreddit if subreddit else "/search"
    params = {"q": query, "limit": "100", "sort": "relevance", "raw_json": "1"}
    if subreddit:
        params["restrict_sr"] = "1"

    posts, after = [], None
    while len(posts) < limit:
        page = dict(params)
        if after:
            page["after"] = after
        data = _get(REDDIT_API + path + "?" + urllib.parse.urlencode(page), log, headers=auth)
        children = data.get("data", {}).get("children", [])
        if not children:
            break
        posts.extend(c["data"] for c in children)
        after = data.get("data", {}).get("after")
        if not after:
            break
        time.sleep(PAUSE_SECONDS)

    posts = posts[:limit]
    log.stage("posts matched by %r" % query, len(posts))

    threads = []
    for post in posts:
        thread = {"post": post, "comments": None}
        if with_comments:
            try:
                listing = _get(REDDIT_API + "/comments/%s?raw_json=1&limit=500" % post["id"],
                               log, headers=auth)
                thread["comments"] = listing[1] if len(listing) > 1 else None
            except FetchError as exc:
                log.error("could not fetch comments for post %s: %s" % (post["id"], exc))
            time.sleep(PAUSE_SECONDS)
        threads.append(thread)
    return threads


def reddit_rows(threads: list[dict]) -> list[Row]:
    rows: list[Row] = []
    for thread in threads:
        post = thread["post"]
        pid = str(post["id"])
        rows.append(Row(
            thread_id=pid, item_id=pid, parent_id="", depth=0, kind="post",
            author=post.get("author") or "", created_utc=_iso_epoch(post.get("created_utc")),
            score=int(post.get("score") or 0), title=post.get("title") or "",
            text=clean_text(post.get("selftext")),
            permalink="https://www.reddit.com" + (post.get("permalink") or ""),
        ))
        listing = thread.get("comments")
        if listing:
            _walk_reddit(listing, pid, 0, rows)
    return rows


def _walk_reddit(listing, thread_id: str, depth: int, out: list[Row]) -> None:
    for child in (listing.get("data", {}).get("children") or []):
        if child.get("kind") != "t1":          # "more" placeholders are not comments
            continue
        data = child.get("data", {})
        cid = str(data.get("id"))
        out.append(Row(
            thread_id=thread_id, item_id=cid,
            parent_id=str(data.get("parent_id") or "").split("_")[-1],
            depth=depth + 1, kind="comment",
            author=data.get("author") or "", created_utc=_iso_epoch(data.get("created_utc")),
            score=int(data.get("score") or 0), title="",
            text=clean_text(data.get("body")),
            permalink="https://www.reddit.com" + (data.get("permalink") or ""),
        ))
        replies = data.get("replies")
        if isinstance(replies, dict):
            _walk_reddit(replies, thread_id, depth + 1, out)


# --------------------------------------------------------------------------

def _epoch(date_string: str) -> int:
    from datetime import datetime, timezone
    return int(datetime.strptime(date_string, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _iso(value) -> str:
    return (value or "").replace(".000Z", "Z")[:20] if value else ""


def _iso_epoch(value) -> str:
    from datetime import datetime, timezone
    if not value:
        return ""
    return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SOURCES = {
    "hn": (hn_threads, hn_rows),
    "reddit": (reddit_threads, reddit_rows),
}
