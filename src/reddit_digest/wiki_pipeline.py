"""Wiki -> AI -> email pipeline.

Reads the public JSON page that the ModRadarAI Devvit app publishes (the recent
posts from a subreddit it monitors), summarizes the themes with Claude, and emails
the result. Needs NO Reddit API credentials — it just fetches a public wiki page
over plain HTTP — so it works without Reddit Data API access.

Env vars:
  WIKI_DATA_URL   - public reddit wiki .json URL, e.g.
                    https://www.reddit.com/r/Dashieapp/wiki/modradarai-data.json
  ANTHROPIC_API_KEY
  SUMMARY_MODEL   - optional, default claude-sonnet-4-6
  SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, DIGEST_FROM, DIGEST_TO

Run:
  python -m reddit_digest.wiki_pipeline            # fetch, summarize, email
  python -m reddit_digest.wiki_pipeline --dry-run  # fetch + summarize, print only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import sys
import urllib.request
from datetime import date
from email.message import EmailMessage

import anthropic

log = logging.getLogger("wiki_pipeline")

_USER_AGENT = "reddit-interest-digest/1.0 (personal wiki reader)"
_DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are an analyst who reads a subreddit and briefs someone who follows that "
    "community but can't read every thread. You summarize what people are discussing "
    "right now: recurring questions, feature requests, complaints and problems, and "
    "notable or emerging topics. Be concise, concrete, and skimmable. Base everything "
    "ONLY on the provided posts — do not invent anything."
)


# ---------------------------------------------------------------------------
# Fetch + parse (pure parse for testability)
# ---------------------------------------------------------------------------

def parse_wiki_payload(raw_json: str) -> dict:
    """Extract our JSON payload from a Reddit wiki .json response body."""
    outer = json.loads(raw_json)
    content_md = (outer.get("data") or {}).get("content_md")
    if not content_md:
        raise ValueError("wiki page response has no data.content_md")
    payload = json.loads(content_md)
    if "posts" not in payload:
        raise ValueError("wiki payload has no 'posts' field")
    return payload


def fetch_wiki_payload(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return parse_wiki_payload(raw)


# ---------------------------------------------------------------------------
# Summarize with Claude
# ---------------------------------------------------------------------------

def _serialize_posts(posts: list[dict], max_posts: int = 60) -> str:
    lines: list[str] = []
    for p in posts[:max_posts]:
        title = str(p.get("title", "")).strip()
        body = str(p.get("body", "")).strip().replace("\n", " ")[:600]
        comments = " | ".join(str(c).replace("\n", " ") for c in (p.get("comments") or []))[:400]
        url = str(p.get("url", ""))
        block = f"- {title}\n  {body}" if body else f"- {title}"
        if comments:
            block += f"\n  top comments: {comments}"
        if url:
            block += f"\n  link: {url}"
        lines.append(block)
    return "\n".join(lines)


def build_user_prompt(subreddit: str, posts: list[dict]) -> str:
    return (
        f"These are the most recent posts from r/{subreddit} (title, body, and a few "
        f"top comments). Write a short Markdown briefing with these sections, in order:\n\n"
        f"## Overview  (2-3 sentences on the mood/what's dominating)\n"
        f"## Top themes\n"
        f"## Recurring questions\n"
        f"## Feature requests & wishes\n"
        f"## Complaints & problems\n"
        f"## Notable / emerging\n\n"
        f"Use short bullets. Reference specific posts by title where useful. If a section "
        f"has nothing, write '_None this time._'. Do not add sections beyond those listed.\n\n"
        f"POSTS:\n{_serialize_posts(posts)}"
    )


def summarize(posts: list[dict], subreddit: str, api_key: str, model: str) -> str:
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(subreddit, posts)}],
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _md_to_html(md: str) -> str:
    """Very small Markdown -> HTML for headings and bullets (email-safe)."""
    out: list[str] = ["<div style='font-family:Arial,Helvetica,sans-serif;line-height:1.5;'>"]
    in_list = False
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3 style='margin:16px 0 4px;'>{s[3:]}</h3>")
        elif s.startswith("- "):
            if not in_list:
                out.append("<ul style='margin:0 0 8px 20px;padding:0;'>")
                in_list = True
            out.append(f"<li>{s[2:]}</li>")
        elif not s:
            if in_list:
                out.append("</ul>")
                in_list = False
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p style='margin:4px 0;'>{s}</p>")
    if in_list:
        out.append("</ul>")
    out.append("</div>")
    return "\n".join(out)


def send_email(subject: str, text_body: str, html_body: str) -> None:
    host = _require_env("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = _require_env("SMTP_USERNAME")
    password = _require_env("SMTP_PASSWORD")
    sender = _require_env("DIGEST_FROM")
    recipient = _require_env("DIGEST_TO")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    log.info("Sending summary to %s via %s:%d", recipient, host, port)
    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)
    log.info("Sent.")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def run(dry_run: bool) -> int:
    url = _require_env("WIKI_DATA_URL")
    api_key = _require_env("ANTHROPIC_API_KEY")
    model = os.environ.get("SUMMARY_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL

    log.info("Fetching %s", url)
    payload = fetch_wiki_payload(url)
    subreddit = str(payload.get("subreddit", "unknown"))
    posts = payload.get("posts", [])
    log.info("Got %d posts from r/%s (generated %s)", len(posts), subreddit, payload.get("generatedAt"))

    if not posts:
        log.info("No posts in the data page; nothing to summarize.")
        return 0

    summary = summarize(posts, subreddit, api_key, model)
    subject = f"r/{subreddit} — what's being discussed ({date.today().isoformat()})"

    if dry_run:
        print(f"\n===== DRY RUN — {subject} =====\n")
        print(summary)
        print("\n===== end (not emailed) =====")
        return 0

    send_email(subject, summary, _md_to_html(summary))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="reddit_digest.wiki_pipeline",
        description="Summarize a ModRadarAI wiki data page with Claude and email it.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Fetch + summarize, print to console, do not email."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
