"""Send the brief via the Resend HTTP API (works from a GitHub Actions runner).

Resend sits behind Cloudflare, which 403s some default UAs — so we set a normal
User-Agent. Sending domain (dashieapp.com) must be verified in Resend.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.request

log = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_LINK_RE = re.compile(r"\[(.+?)\]\((https?://[^\s)]+)\)")


def _inline(s: str) -> str:
    out = html.escape(s)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _LINK_RE.sub(r'<a href="\2">\1</a>', out)
    return out


def md_to_html(md: str) -> str:
    """Light Markdown -> HTML: headers, bold, links, bullets, rules."""
    out: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in md.splitlines():
        line = raw.rstrip()
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            close_list()
            level = len(heading.group(1)) + 1  # h2..h4
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif re.match(r"^\s*[-*]\s+", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            item = re.sub(r"^\s*[-*]\s+", "", line)
            out.append(f"<li>{_inline(item)}</li>")
        elif line.strip() == "---":
            close_list()
            out.append("<hr>")
        elif line == "":
            close_list()
        else:
            close_list()
            out.append(f"<p>{_inline(line)}</p>")
    close_list()
    body = "".join(out)
    return (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;line-height:1.5;"
        f"max-width:720px;\">{body}</div>"
    )


def send_email(*, api_key: str, sender: str, to: str, subject: str, markdown: str) -> str:
    """POST the brief to Resend. Returns the message id. Raises on failure."""
    payload = json.dumps(
        {
            "from": sender,
            "to": [to],
            "subject": subject,
            "text": markdown,
            "html": md_to_html(markdown),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        _RESEND_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Dashie-ListeningBrief/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    msg_id = data.get("id", "")
    log.info("Sent brief to %s via Resend (id=%s)", to, msg_id)
    return msg_id
