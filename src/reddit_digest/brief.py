"""Generate the "Dashie Listening Brief" from a batch of Reddit posts via Claude.

This is the community-listening brief format (help-and-tactful-plug threads with
draft replies, share-expertise, and competitor/feature signals). Ported from the
Devvit app's prompt so the output matches what we already validated.
"""

from __future__ import annotations

import logging

import anthropic

from .rss_client import RssPost

log = logging.getLogger(__name__)

DASHIE_CONTEXT = (
    "CONTEXT — what Dashie is: Dashie (https://dashieapp.com) is a native Android app that "
    "turns a Fire tablet, Echo Show, or Android tablet into a family dashboard + Home Assistant "
    "kiosk. It shows an HA dashboard plus a shared family calendar, photo slideshow, weather, and "
    "chores — with no Lovelace CSS to maintain. Its differentiators vs. DIY / Fully-Kiosk setups: "
    "a native kiosk shell (no sidebar/header CSS to fight), Fire-tablet lock-screen bypass, true "
    "hardware screen-off sleep with motion wake, offline kiosk mode with no self-hosting, and "
    "smart-charging automation to protect tablet batteries. Competitors/alternatives to watch "
    "include Fully Kiosk Browser, Magic Frame, DashVoice, HA Studio Pro, Skylight, and various "
    "Lovelace wall-dashboard cards."
)

SYSTEM_PROMPT = (
    "You are a community-listening analyst for Dashie, a family-dashboard / Home-Assistant-kiosk "
    "app. You read recent posts from a subreddit and produce a concise, actionable 'listening "
    "brief' for the founder (John): where he can genuinely help people (and tactfully mention "
    "Dashie only when truly relevant), where he can share expertise, and what competitor/feature "
    "signals matter for the roadmap. Be helpful and honest — every draft reply must solve the "
    "person's actual problem FIRST and must never read as an advertisement. Base everything ONLY "
    "on the provided posts; never invent posts or facts."
)


def _serialize(posts: list[RssPost], max_posts: int) -> str:
    lines: list[str] = []
    for p in posts[:max_posts]:
        body = (p.body or "").strip()
        block = f"- {p.title}"
        if body:
            block += f"\n  {body[:800]}"
        block += f"\n  link: {p.url}"
        lines.append(block)
    return "\n".join(lines)


def _user_prompt(subreddit: str, posts: list[RssPost], max_posts: int) -> str:
    return (
        f"{DASHIE_CONTEXT}\n\n"
        f"Below are recent posts from r/{subreddit}. Produce a Markdown \"listening brief\" with "
        f"the three sections below. Only include posts that genuinely fit a section; if a section "
        f"has none, write \"_None this time._\". Reference each post by its exact title, and "
        f"include its link.\n\n"
        f"## 🟢 Help & tactful plug\n"
        f"Posts where someone has a problem or question that Dashie is genuinely relevant to (wall "
        f"tablets, kiosk displays, HA-on-a-tablet, family dashboards, screen sleep/wake, Fire "
        f"tablet, Echo Show, photo frames). For each such post:\n"
        f"- **[exact title]** ([link]) — one line on their problem.\n"
        f"  - DRAFT REPLY: first, a genuinely useful answer to their actual HA problem. Then, ONLY "
        f"if truly relevant, a brief and honest Dashie mention (e.g. \"Side note — I've been "
        f"running Dashie (https://dashieapp.com) on my Fire tablets…\"). Never salesy; skip the "
        f"mention if it wouldn't genuinely help them.\n\n"
        f"## 🔵 Share expertise\n"
        f"Posts where John can add helpful expertise (family dashboards, HA tips, hardware) with "
        f"NO product plug at all. For each: **[exact title]** ([link]) + a short DRAFT REPLY.\n\n"
        f"## 🟡 Feature ideas / pain points / competitors\n"
        f"Signals for the roadmap and competitive awareness — competitor launches or alternatives, "
        f"recurring pain points, notable feature requests. NO draft replies here. For each: "
        f"**[exact title]** ([link]) — 1-2 lines on the signal and why it matters for Dashie.\n\n"
        f"Remember: these are drafts for John to review and post MANUALLY, if at all — never "
        f"auto-posted.\n\n"
        f"POSTS:\n{_serialize(posts, max_posts)}"
    )


def generate_brief(
    posts: list[RssPost],
    subreddit: str,
    *,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_posts: int = 25,
    max_tokens: int = 3500,
) -> str:
    """Call Claude and return the Markdown listening brief. Raises on API failure."""
    client = anthropic.Anthropic(api_key=api_key)
    prompt = _user_prompt(subreddit, posts, max_posts)
    log.info(
        "Requesting brief from %s — %d posts, %d prompt chars",
        model,
        min(len(posts), max_posts),
        len(prompt),
    )
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    text = text.strip()
    if not text:
        raise RuntimeError("Claude returned an empty brief")
    log.info("Brief received — %d chars", len(text))
    return text
