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
    "Dashie only when truly relevant), where he can share expertise, where he and his family can "
    "simply participate in the community to build genuine karma, and what competitor/feature "
    "signals matter for the roadmap. Be helpful and honest — every draft reply must solve the "
    "person's actual problem FIRST and must never read as an advertisement. Draft replies should "
    "sound like a helpful PEER sharing what's worked for them — friendly and a little hedged, "
    "never a know-it-all. Base everything ONLY on the provided posts; never invent posts or facts."
)

# The draft replies are copy-paste-ready comments John posts himself, so they must
# sound like HIM — concise, plain, human — not like an AI or a blog post.
REPLY_STYLE = (
    "DRAFT REPLY STYLE — write every draft reply the way John actually talks on Reddit:\n"
    "- Short: 1-3 sentences. Two is ideal. Never a paragraph, never a wall of options.\n"
    "- Plain and human. Use contractions. Sound like a real person typing a quick, helpful "
    "comment — NOT an assistant. No 'Great question', no 'Hope this helps', no sign-off, no "
    "numbered lists, no bolded headers inside the reply.\n"
    "- Lead with the most useful idea, but frame it as a SUGGESTION, not a verdict. HEDGE — "
    "you're a helpful peer sharing what's worked for you, not an authority. Use 'I think', 'might "
    "be worth trying', 'in my experience', 'maybe', 'could be', 'not sure but…'. Don't state "
    "things as flat fact or come across as a know-it-all; it's fine to be unsure.\n"
    "- Still concise, not wishy-washy: hedge the TONE, don't pad with filler or list every option.\n"
    "- If a Dashie mention genuinely fits, it's ONE short casual aside in first person "
    "(e.g. 'fwiw I've been running Dashie on a couple Fire tablets for this'), never a pitch, and "
    "drop it entirely if it wouldn't actually help them.\n\n"
    "EXAMPLE — someone asks what to run for a wall-mounted HA dashboard:\n"
    "  TOO KNOW-IT-ALL (do NOT write like this): \"For a wall-mounted HA dashboard, a Raspberry "
    "Pi 4 with 4GB is the recommended choice. Boot off an SSD — microSD cards will fail with HA's "
    "constant writes.\"\n"
    "  IN JOHN'S VOICE (write like this): \"I think a Pi 4 would handle it, but honestly a cheap "
    "Android tablet might be simpler for a wall display — no separate screen to wire up. If you do "
    "go the Pi route, it might be worth booting off an SSD; I've heard microSD cards can die pretty "
    "fast with HA's constant writes.\""
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
        f"{REPLY_STYLE}\n\n"
        f"Below are recent posts from r/{subreddit}. Produce a Markdown \"listening brief\" with "
        f"the four sections below. Only include posts that genuinely fit a section; if a section "
        f"has none, write \"_None this time._\". Reference each post by its exact title, and "
        f"include its link. A post can appear in at most one section — put it where it fits best.\n\n"
        f"## 🟢 Help & tactful plug\n"
        f"Posts where someone has a problem or question that Dashie is genuinely relevant to (wall "
        f"tablets, kiosk displays, HA-on-a-tablet, family dashboards, screen sleep/wake, Fire "
        f"tablet, Echo Show, photo frames). For each such post:\n"
        f"- **[exact title]** ([link]) — one line on their problem.\n"
        f"  - DRAFT REPLY: the answer to their actual problem in 1-3 short sentences, in John's "
        f"voice (see DRAFT REPLY STYLE). Fold any Dashie mention into that same short reply as a "
        f"casual aside — do not add a separate sentence for it. Skip the mention if it wouldn't "
        f"genuinely help.\n\n"
        f"## 🔵 Share expertise\n"
        f"Posts where John can add helpful expertise (family dashboards, HA tips, hardware) with "
        f"NO product plug at all. For each: **[exact title]** ([link]) + a DRAFT REPLY of 1-3 "
        f"short sentences in John's voice.\n\n"
        f"## 🟣 General participation (karma-building)\n"
        f"Approachable, low-stakes posts where a casual, friendly comment is natural — "
        f"show-and-tell, opinions, 'what do you all use', polls, wins, general discussion. The goal "
        f"is genuine community participation so John AND his family can build karma (so they can "
        f"later post about Dashie without being auto-flagged as spam). Prefer posts a non-expert "
        f"family member could comfortably comment on. NO expertise required, NO Dashie mention. For "
        f"each: **[exact title]** ([link]) — one line on why it's approachable, + a SHORT, friendly "
        f"DRAFT COMMENT (hedged, casual, postable as-is by a family member).\n\n"
        f"## 🟡 Feature ideas / pain points / competitors\n"
        f"Signals for the roadmap and competitive awareness — competitor launches or alternatives, "
        f"recurring pain points, notable feature requests. NO draft replies here. For each: "
        f"**[exact title]** ([link]) — 1-2 lines on the signal and why it matters for Dashie.\n\n"
        f"Remember: these are drafts for John to review and post MANUALLY, if at all — never "
        f"auto-posted.\n\n"
        f"POSTS:\n{_serialize(posts, max_posts)}"
    )


ACTIVITY_SYSTEM = (
    "You monitor Dashie's OWN subreddit (r/Dashieapp) for the founder, John. Your job is a "
    "quick heads-up: what got posted, and which posts look like they need John's attention "
    "(a support question, a bug report, or notable feedback). This is NOT marketing analysis — "
    "no draft replies, no plugs. Be brief and factual. Base everything ONLY on the posts given."
)


def generate_activity_note(
    posts: list[RssPost],
    subreddit: str,
    *,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 1500,
) -> str:
    """Short 'what's new on our sub' note. Assumes `posts` is already the new ones."""
    client = anthropic.Anthropic(api_key=api_key)
    listing = "\n".join(
        f"- {p.title}"
        + (f"\n  {p.body[:400]}" if p.body else "")
        + f"\n  by {p.author or 'unknown'} — {p.url}"
        for p in posts
    )
    prompt = (
        f"These are the new posts in r/{subreddit} since yesterday. Write a short Markdown note:\n"
        f"- One-line summary at top (e.g. \"3 new posts — 1 looks like a bug report\").\n"
        f"- Then one bullet per post: **[exact title]** ([link]) — a ≤12-word gist, and tag it "
        f"with ONE of ❓question · 🐛 bug · 💬 feedback · 📣 other so John can triage at a glance.\n"
        f"No draft replies, no analysis beyond the gist.\n\nPOSTS:\n{listing}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=ACTIVITY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise RuntimeError("Claude returned an empty activity note")
    return text


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
