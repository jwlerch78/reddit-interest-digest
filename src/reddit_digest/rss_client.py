"""Read a subreddit via its public Atom/RSS feed — no Reddit API credentials.

Reddit bot-walls the unauthenticated `.json` endpoints but still serves the `.rss`
feeds to normal clients (verified from both residential and GitHub-datacenter IPs).
This lets the daily brief run anywhere (GitHub Actions) with no PRAW creds.

Stdlib only (urllib + xml.etree) so there are no extra dependencies to install.
"""

from __future__ import annotations

import html
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

# A normal browser UA — Reddit throttles obvious bot UAs harder. A custom feed UA
# also works, but a browser UA is the safest.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class RssPost:
    id: str
    title: str
    body: str  # plain-text post body (HTML stripped), may be empty for link posts
    url: str
    author: str


def _feed_url(subreddit: str, listing: str) -> str:
    sub = subreddit.strip().lstrip("r/").strip("/")
    if listing == "top-day":
        return f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit=50"
    if listing == "hot":
        return f"https://www.reddit.com/r/{sub}/hot/.rss?limit=50"
    return f"https://www.reddit.com/r/{sub}/new/.rss?limit=50"


def _fetch(url: str, *, attempts: int = 4, backoff: float = 15.0) -> str:
    """GET the feed with a browser UA, retrying on 429/5xx with linear backoff."""
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", "replace")
                log.info("RSS %s -> HTTP %s, %d bytes", url, resp.status, len(body))
                return body
        except urllib.error.HTTPError as exc:
            last_err = exc
            log.warning("RSS %s -> HTTP %s (attempt %d/%d)", url, exc.code, attempt, attempts)
            if exc.code not in (429, 500, 502, 503, 504):
                raise
        except Exception as exc:  # noqa: BLE001 - network hiccups are retryable
            last_err = exc
            log.warning("RSS %s failed: %s (attempt %d/%d)", url, exc, attempt, attempts)
        if attempt < attempts:
            time.sleep(backoff * attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_err}")


def _text(content_html: str, limit: int = 900) -> str:
    """Strip tags from a feed <content> blob and collapse whitespace."""
    unescaped = html.unescape(content_html or "")
    stripped = _TAG_RE.sub(" ", unescaped)
    collapsed = _WS_RE.sub(" ", stripped).strip()
    # Reddit wraps every entry's content with a "submitted by /u/… [link][comments]"
    # boilerplate footer — trim it so it doesn't pollute the prompt.
    collapsed = re.split(r"submitted by\s+/u/", collapsed)[0].strip()
    return collapsed[:limit]


def _parse(feed_xml: str) -> list[RssPost]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(feed_xml)
    posts: list[RssPost] = []
    for entry in root.iter(f"{_ATOM}entry"):
        title_el = entry.find(f"{_ATOM}title")
        link_el = entry.find(f"{_ATOM}link")
        content_el = entry.find(f"{_ATOM}content")
        id_el = entry.find(f"{_ATOM}id")
        author_el = entry.find(f"{_ATOM}author/{_ATOM}name")
        title = html.unescape((title_el.text or "").strip()) if title_el is not None else ""
        url = link_el.get("href", "") if link_el is not None else ""
        body = _text(content_el.text or "") if content_el is not None else ""
        pid = (id_el.text or "").strip() if id_el is not None else url
        author = (author_el.text or "").strip() if author_el is not None else ""
        if title and url:
            posts.append(RssPost(id=pid, title=title, body=body, url=url, author=author))
    return posts


def fetch_posts(subreddit: str, listing: str = "top-day", limit: int = 25) -> list[RssPost]:
    """Fetch and parse recent posts for `subreddit` from its public RSS feed.

    `listing` is one of: "top-day" (top of the last 24h — best for a daily brief),
    "new", or "hot". Returns up to `limit` posts.
    """
    feed_xml = _fetch(_feed_url(subreddit, listing))
    posts = _parse(feed_xml)
    log.info("Parsed %d posts from r/%s (%s)", len(posts), subreddit, listing)
    return posts[:limit]
