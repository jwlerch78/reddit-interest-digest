"""Daily "Dashie Listening Brief" — RSS reader edition (no Reddit API creds).

Pipeline: fetch the subreddit's public RSS feed -> Claude writes the listening
brief -> email it via Resend. Runs anywhere (GitHub Actions) because RSS needs no
auth. Configured entirely from environment variables (no config.yaml / PRAW).

Env:
  ANTHROPIC_API_KEY   (required)  Claude key for the brief
  RESEND_API_KEY      (required)  Resend key for email
  DIGEST_FROM         (required)  e.g. "John from Dashie <john@dashieapp.com>"
  DIGEST_TO           (required)  recipient email
  BRIEF_SUBREDDIT     (optional)  default "homeassistant"
  BRIEF_LISTING       (optional)  "top-day" (default) | "new" | "hot"
  BRIEF_MAX_POSTS     (optional)  default 25
  SUMMARY_MODEL       (optional)  default "claude-sonnet-4-6"
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

from .brief import generate_activity_note, generate_brief, generate_community_brief
from .resend_mailer import send_email
from .rss_client import RssPost, fetch_posts

log = logging.getLogger("reddit_digest.run_rss")

# The ONE consolidated "Dashie Listening Brief" email is composed of these sources, each
# rendered with its own lens ("ha" = full HA brief; "community" = non-HA families lens:
# participation-first + tactful Skylight/voice plug). Add a source here to include it.
# Fetches run sequentially with ~a minute of LLM time between them, which also spaces out
# the Reddit requests. Use a multireddit ("a+b") to add subs without extra requests.
LISTENING_SOURCES = [
    {
        "feed": "homeassistant",
        "listing": "top-day",
        "lens": "ha",
        "heading": "🏠 Home Assistant — r/homeassistant",
    },
    {
        "feed": "smarthome+amazonecho",
        "listing": "top-day",
        "lens": "community",
        "label": "r/smarthome + r/amazonecho",
        "heading": "👨‍👩‍👧 Families — r/smarthome + r/amazonecho",
    },
]


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _posts_since(posts: list[RssPost], hours: int) -> list[RssPost]:
    """Keep posts whose feed timestamp is within the last `hours`. Undated posts are
    kept (better to over-notify than silently drop)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    fresh: list[RssPost] = []
    for p in posts:
        if not p.created:
            fresh.append(p)
            continue
        try:
            when = datetime.fromisoformat(p.created.replace("Z", "+00:00"))
            if when >= cutoff:
                fresh.append(p)
        except ValueError:
            fresh.append(p)
    return fresh


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    subreddit = os.environ.get("BRIEF_SUBREDDIT", "homeassistant").strip() or "homeassistant"
    mode = os.environ.get("BRIEF_MODE", "brief").strip() or "brief"
    listing = os.environ.get("BRIEF_LISTING", "top-day").strip() or "top-day"
    max_posts = int(os.environ.get("BRIEF_MAX_POSTS", "25") or "25")
    lookback = int(os.environ.get("BRIEF_LOOKBACK_HOURS", "24") or "24")
    model = os.environ.get("SUMMARY_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"

    anthropic_key = _require("ANTHROPIC_API_KEY")
    resend_key = _require("RESEND_API_KEY")
    sender = _require("DIGEST_FROM")
    recipient = _require("DIGEST_TO")
    today = date.today().isoformat()

    def _send(subject: str, markdown: str) -> None:
        send_email(api_key=resend_key, sender=sender, to=recipient, subject=subject, markdown=markdown)

    # --- Consolidated listening brief: several sources -> ONE email ---
    if mode == "listening":
        sections: list[str] = []
        ok = 0
        for src in LISTENING_SOURCES:
            try:
                posts = fetch_posts(src["feed"], listing=src["listing"], limit=max_posts)
                if src["lens"] == "community":
                    body = generate_community_brief(
                        posts, src.get("label", src["feed"]),
                        api_key=anthropic_key, model=model, max_posts=max_posts,
                    )
                else:
                    body = generate_brief(
                        posts, src["feed"], api_key=anthropic_key, model=model, max_posts=max_posts,
                    )
                sections.append(f"# {src['heading']}\n_{len(posts)} posts_\n\n{body}")
                ok += 1
            except Exception as exc:  # one bad source must not sink the whole email
                log.error("Source %s failed: %s", src["feed"], exc)
                sections.append(f"# {src['heading']}\n\n_Couldn't fetch this source today._")
        if ok == 0:
            log.error("All listening sources failed — not sending.")
            return 1
        _send(
            f"Dashie Listening Brief — {today}",
            f"# Dashie Listening Brief — {today}\n\n" + "\n\n---\n\n".join(sections),
        )
        log.info("Done.")
        return 0

    # --- Single-source modes (activity monitor / single-sub brief) ---
    posts = fetch_posts(subreddit, listing=listing, limit=max_posts)
    if not posts:
        log.error("No posts fetched from r/%s — aborting without sending.", subreddit)
        return 1

    if mode == "activity":
        # Monitor our own sub: only email when there are genuinely NEW posts.
        fresh = _posts_since(posts, lookback)
        if not fresh:
            log.info("r/%s: no new posts in last %dh — no email sent.", subreddit, lookback)
            return 0
        note = generate_activity_note(fresh, subreddit, api_key=anthropic_key, model=model)
        _send(
            f"r/{subreddit} activity — {len(fresh)} new — {today}",
            f"# r/{subreddit} activity — {today}\n\n{note}",
        )
        log.info("Done.")
        return 0

    brief_md = generate_brief(
        posts, subreddit, api_key=anthropic_key, model=model, max_posts=max_posts
    )
    _send(
        f"Dashie Listening Brief — r/{subreddit} — {today}",
        f"# Dashie Listening Brief — r/{subreddit}\n\n_{today} • {len(posts)} posts_\n\n{brief_md}",
    )
    log.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
