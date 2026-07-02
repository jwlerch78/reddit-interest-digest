"""Tests for the wiki -> AI -> email pipeline (pure parts; no network/LLM/email)."""

import json

import pytest

from reddit_digest.wiki_pipeline import (
    build_user_prompt,
    parse_wiki_payload,
    _md_to_html,
    _serialize_posts,
)


def _reddit_wiki_json(payload: dict) -> str:
    """Wrap our payload the way Reddit's wiki .json endpoint returns it."""
    return json.dumps({"kind": "wikipage", "data": {"content_md": json.dumps(payload)}})


def test_parse_wiki_payload_extracts_inner_json():
    payload = {
        "subreddit": "homeassistant",
        "generatedAt": "2026-07-02T13:00:00Z",
        "posts": [{"id": "a", "title": "Zigbee help", "body": "", "url": "u", "comments": []}],
    }
    parsed = parse_wiki_payload(_reddit_wiki_json(payload))
    assert parsed["subreddit"] == "homeassistant"
    assert len(parsed["posts"]) == 1
    assert parsed["posts"][0]["title"] == "Zigbee help"


def test_parse_wiki_payload_errors_without_content_md():
    with pytest.raises(ValueError):
        parse_wiki_payload(json.dumps({"kind": "wikipage", "data": {}}))


def test_parse_wiki_payload_errors_without_posts():
    raw = json.dumps({"data": {"content_md": json.dumps({"subreddit": "x"})}})
    with pytest.raises(ValueError):
        parse_wiki_payload(raw)


def test_serialize_posts_includes_title_body_comments_link():
    posts = [{"title": "T1", "body": "some body", "url": "http://x", "comments": ["c1", "c2"]}]
    text = _serialize_posts(posts)
    assert "T1" in text
    assert "some body" in text
    assert "top comments: c1 | c2" in text
    assert "http://x" in text


def test_serialize_posts_caps_count():
    posts = [{"title": f"T{i}"} for i in range(200)]
    text = _serialize_posts(posts, max_posts=10)
    assert text.count("\n- ") + text.startswith("- ") <= 10 + 1  # ~10 blocks


def test_build_user_prompt_has_all_sections():
    prompt = build_user_prompt("homeassistant", [{"title": "T"}])
    for section in [
        "## Overview",
        "## Top themes",
        "## Recurring questions",
        "## Feature requests & wishes",
        "## Complaints & problems",
        "## Notable / emerging",
    ]:
        assert section in prompt
    assert "r/homeassistant" in prompt


def test_md_to_html_renders_headings_and_lists():
    html = _md_to_html("## Overview\nStuff happened.\n## Top themes\n- one\n- two")
    assert "<h3" in html
    assert "<ul" in html
    assert "<li>one</li>" in html
