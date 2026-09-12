"""The sampling log.

This is the point of the tool. Scrapers are common; what makes a corpus usable as
evidence is being able to say precisely how it was drawn — what was asked for, what
came back, what was thrown away and why, and what the method could not see at all.

Every run writes two files: sampling-log.json for a machine, SAMPLING.md for a
methods section. They are generated from the same record so they cannot disagree.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

VERSION = "0.1.0"

# Caveats that apply to any public-forum corpus, stated in every log so that no
# write-up can quietly omit them. Source adapters add their own on top.
UNIVERSAL_CAVEATS = [
    "Deleted and moderator-removed content is invisible to this method. A thread's "
    "most contested contributions are the most likely to be missing.",
    "Posters are not users. The overwhelming majority of people who read a forum "
    "never post, and nothing here represents them.",
    "Visibility is ranked, not neutral. What a search returns is shaped by the "
    "platform's own relevance and score ordering, which correlates with age and "
    "engagement rather than with representativeness.",
    "Self-selection: people post about an experience when it is unusual, unresolved "
    "or annoying. Base rates cannot be estimated from this data.",
    "Identity is unverified. Claimed roles, employers and seniority are claims.",
]


class SamplingLog:
    def __init__(self, source, queries, parameters: dict, command: str) -> None:
        self.source = source
        # A run may search several terms and merge the results into one corpus.
        # The log keeps them as a list so a reader can see the whole search, not
        # just the term that happened to be typed first.
        self.queries = [queries] if isinstance(queries, str) else list(queries)
        self.parameters = parameters
        self.command = command
        self.started = datetime.now(timezone.utc)
        self.endpoints: list[str] = []
        self.stages: list[tuple[str, int]] = []
        self.exclusions: list[dict] = []
        self.caveats: list[str] = list(UNIVERSAL_CAVEATS)
        self.errors: list[str] = []

    def endpoint(self, url: str) -> None:
        """Record the API surface used, not every call.

        Per-item URLs differ only by id; listing all of them buries the two or
        three endpoints a reader actually needs to know were hit.
        """
        base = re.sub(r"/\d+(?=/?$)", "/{id}", url.split("?")[0])
        # Reddit's ids are base36, so the numeric rule above misses them.
        base = re.sub(r"(/comments)/[^/]+$", r"\1/{id}", base)
        if base not in self.endpoints:
            self.endpoints.append(base)

    def stage(self, name: str, count: int) -> None:
        """Record how many items survived a step. Order matters — this is the funnel."""
        self.stages.append((name, count))

    def excluded(self, reason: str, count: int) -> None:
        if count <= 0:
            return
        for entry in self.exclusions:
            if entry["reason"] == reason:
                entry["count"] += count
                return
        self.exclusions.append({"reason": reason, "count": count})

    def caveat(self, text: str) -> None:
        if text not in self.caveats:
            self.caveats.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)

    # -- output ----------------------------------------------------------

    def record(self, rows) -> dict:
        dates = sorted(r.created_utc for r in rows if r.created_utc)
        threads = {r.thread_id for r in rows}
        return {
            "tool": "fieldnotes",
            "version": VERSION,
            "run_at": self.started.isoformat(),
            "command": self.command,
            "source": self.source,
            "queries": self.queries,
            "parameters": self.parameters,
            "endpoints": self.endpoints,
            "funnel": [{"stage": n, "items": c} for n, c in self.stages],
            "exclusions": self.exclusions,
            "result": {
                "threads": len(threads),
                "items": len(rows),
                "posts": sum(1 for r in rows if r.kind == "post"),
                "comments": sum(1 for r in rows if r.kind == "comment"),
                "observed_date_range": {
                    "earliest": dates[0] if dates else None,
                    "latest": dates[-1] if dates else None,
                },
            },
            "caveats": self.caveats,
            "errors": self.errors,
        }

    def write_json(self, path, rows) -> dict:
        data = self.record(rows)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return data

    def write_markdown(self, path, rows) -> None:
        d = self.record(rows)
        p = d["parameters"]
        out = []
        out.append("# Sampling log\n")
        out.append(
            "Generated by fieldnotes %s on %s. This file describes how the "
            "accompanying corpus was drawn; it is meant to be read alongside any "
            "finding taken from it.\n" % (d["version"], d["run_at"][:16].replace("T", " ") + " UTC")
        )

        out.append("\n## What was asked for\n")
        out.append("- **Source:** %s" % d["source"])
        queries = d["queries"]
        if len(queries) == 1:
            out.append("- **Query:** `%s`" % (queries[0] or "(none)"))
        else:
            out.append("- **Queries:** %s" % ", ".join("`%s`" % q for q in queries))
            out.append(
                "  <br>Searched separately and merged into one corpus. A term that "
                "matched nothing still appears above, because the terms that failed "
                "are part of the search."
            )
        for key in sorted(p):
            if p[key] not in (None, "", False):
                out.append("- **%s:** %s" % (key.replace("_", " ").capitalize(), p[key]))
        out.append("\nEndpoints used:\n")
        for e in d["endpoints"]:
            out.append("- `%s`" % e)

        out.append("\n## What came back\n")
        out.append("| Stage | Items |")
        out.append("| --- | ---: |")
        for row in d["funnel"]:
            out.append("| %s | %s |" % (row["stage"], format(row["items"], ",")))

        if d["exclusions"]:
            out.append("\n### What was excluded, and why\n")
            out.append("| Reason | Items |")
            out.append("| --- | ---: |")
            for e in d["exclusions"]:
                out.append("| %s | %s |" % (e["reason"], format(e["count"], ",")))

        r = d["result"]
        out.append("\n## The corpus\n")
        out.append("%s items across %s threads — %s posts and %s comments."
                   % (format(r["items"], ","), format(r["threads"], ","),
                      format(r["posts"], ","), format(r["comments"], ",")))
        rng = r["observed_date_range"]
        if rng["earliest"]:
            out.append("\nObserved date range: **%s** to **%s**." % (rng["earliest"][:10], rng["latest"][:10]))
            out.append(
                "\nNote that this is the range of what was *returned*, which is not "
                "the same as the range that was *requested*. If they differ, the "
                "difference is itself a finding about coverage."
            )

        out.append("\n## What this sample cannot support\n")
        for c in d["caveats"]:
            out.append("- %s" % c)

        if d["errors"]:
            out.append("\n## Errors during collection\n")
            out.append("These items are missing from the corpus and their absence is "
                       "not random — a fetch usually fails for a reason connected to "
                       "the content.\n")
            for e in d["errors"]:
                out.append("- %s" % e)

        out.append("\n## Reproducing it\n")
        out.append("```sh\n%s\n```\n" % d["command"])
        out.append(
            "Re-running will not necessarily return the same corpus. Threads are "
            "edited, deleted and re-ranked, so a later run is a new sample, not a "
            "verification of this one. Treat the CSV as the artifact of record.\n"
        )

        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(out))
