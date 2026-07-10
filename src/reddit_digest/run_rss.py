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

from .brief import generate_activity_note, generate_brief
from .resend_mailer import send_email
from .rss_client import RssPost, fetch_posts

log = logging.getLogger("reddit_digest.run_rss")


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

    # 1. Read Reddit via RSS (no creds).
    posts = fetch_posts(subreddit, listing=listing, limit=max_posts)
    if not posts:
        log.error("No posts fetched from r/%s — aborting without sending.", subreddit)
        return 1

    today = date.today().isoformat()

    # 2/3. Generate + email, per mode.
    if mode == "activity":
        # Monitor our own sub: only email when there are genuinely NEW posts.
        fresh = _posts_since(posts, lookback)
        if not fresh:
            log.info("r/%s: no new posts in last %dh — no email sent.", subreddit, lookback)
            return 0
        note = generate_activity_note(fresh, subreddit, api_key=anthropic_key, model=model)
        full_md = f"# r/{subreddit} activity — {today}\n\n{note}"
        subject = f"r/{subreddit} activity — {len(fresh)} new — {today}"
    else:
        brief_md = generate_brief(
            posts, subreddit, api_key=anthropic_key, model=model, max_posts=max_posts
        )
        full_md = (
            f"# Dashie Listening Brief — r/{subreddit}\n\n"
            f"_{today} • {len(posts)} posts • top of last 24h_\n\n{brief_md}"
        )
        subject = f"Dashie Listening Brief — r/{subreddit} — {today}"

    send_email(
        api_key=resend_key, sender=sender, to=recipient, subject=subject, markdown=full_md
    )
    log.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
