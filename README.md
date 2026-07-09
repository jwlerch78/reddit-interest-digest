# Dashie Listening Brief

A hands-off daily **community-listening brief** for Dashie. Every morning it reads a
subreddit (default **r/homeassistant**), has Claude write a short, actionable brief —
where John can genuinely help people (with copy-paste-ready draft replies in his voice),
where to share expertise, and what competitor/feature signals matter — and emails it.

It runs entirely on **GitHub Actions** on a daily cron. No servers, no Reddit API
credentials, no Devvit, nothing running on your Mac.

```
GitHub Actions (daily 13:00 UTC)
  1. fetch r/<sub> public RSS      (no auth — bypasses Reddit's bot wall)
  2. parse ~25 posts (top of 24h)
  3. Claude writes the brief       (Dashie Listening Brief format)
  4. email it via Resend
```

> **This is the active mechanism.** The older PRAW/relevance-scoring pipeline
> (`reddit_client.py`, `scorer.py`, `digest.py`, `wiki_pipeline.py`) is **deprecated** —
> it needed Reddit API creds we don't have. See [Deprecated paths](#deprecated-paths).

---

## Why RSS + GitHub Actions (the load-bearing decision)

We tried several ways to read Reddit for a daily brief. Only one works for us:

| Approach | Result |
|---|---|
| Reddit **Data API** (PRAW) | ❌ No self-serve access for our use case |
| **Devvit** app (reads Reddit natively) | ❌ Deployed scheduler hard-kills any non-trivial LLM fetch; wiki writes 403 |
| Reddit **`.json`** endpoints, unauthenticated | ❌ Bot-walled (403) |
| Reddit **`.rss`** feeds, unauthenticated | ✅ **HTTP 200** — the bot wall only blocks `.json` |

Two facts pin the architecture:

1. **RSS works unauthenticated** — `https://www.reddit.com/r/<sub>/top/.rss?t=day` returns
   200 with a normal browser User-Agent. No API creds needed.
2. **It must run from a non-Anthropic IP.** Reddit blocks Anthropic's infra, so a Claude
   cloud routine / `WebFetch` **cannot** fetch it. Verified that a **GitHub Actions runner**
   (datacenter IP) *can*. So the reader lives in GitHub Actions, not a Claude routine.

Reddit rate-limits rapid repeats (429); once-a-day + backoff (built into the reader) is fine.

---

## Repo layout (the RSS pipeline)

| File | Role |
|---|---|
| [`src/reddit_digest/rss_client.py`](src/reddit_digest/rss_client.py) | Fetch + parse the RSS feed (stdlib only; browser UA; 429 backoff) |
| [`src/reddit_digest/brief.py`](src/reddit_digest/brief.py) | The **Dashie Listening Brief prompt** + the reply-voice style + the Claude call |
| [`src/reddit_digest/resend_mailer.py`](src/reddit_digest/resend_mailer.py) | Send via Resend + light Markdown→HTML |
| [`src/reddit_digest/run_rss.py`](src/reddit_digest/run_rss.py) | Orchestrator: `python -m reddit_digest.run_rss` |
| [`.github/workflows/rss-digest.yml`](.github/workflows/rss-digest.yml) | Daily cron + the subreddit matrix |

---

## Configuration

Set via environment variables (in the workflow they come from repo secrets):

| Var | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | — | Claude key (writes the brief) |
| `RESEND_API_KEY` | ✅ | — | Resend key (sends the email) |
| `DIGEST_FROM` | ✅ | — | `John from Dashie <john@dashieapp.com>` (must be a verified Resend domain) |
| `DIGEST_TO` | ✅ | — | Recipient email |
| `BRIEF_SUBREDDIT` | — | `homeassistant` | Subreddit to read (no `r/`) |
| `BRIEF_LISTING` | — | `top-day` | `top-day` (top of last 24h) \| `new` \| `hot` |
| `BRIEF_MAX_POSTS` | — | `25` | How many posts to feed Claude |
| `SUMMARY_MODEL` | — | `claude-sonnet-4-6` | Claude model for the brief |

**Secrets** live in GitHub → repo Settings → Secrets and variables → Actions. Set/rotate them:

```bash
REPO=jwlerch78/reddit-interest-digest
gh secret set ANTHROPIC_API_KEY --repo $REPO --body "<key>"
gh secret set RESEND_API_KEY    --repo $REPO --body "<key>"
gh secret set DIGEST_FROM       --repo $REPO --body "John from Dashie <john@dashieapp.com>"
gh secret set DIGEST_TO         --repo $REPO --body "you@example.com"
```

---

## Tweaking

- **Delivery time** → the `cron:` line in [`rss-digest.yml`](.github/workflows/rss-digest.yml)
  (UTC; e.g. `0 13 * * *` = 13:00 UTC). [crontab.guru](https://crontab.guru) helps.
- **The draft-reply voice** → `REPLY_STYLE` in [`brief.py`](src/reddit_digest/brief.py). It's a
  plain-English style guide + a before/after example. Edit the rules or swap the example to
  recalibrate. To anchor it to *your* voice even harder, paste a couple of your real Reddit
  comments into the example.
- **The brief structure / sections / what counts as "help"** → the section prompts in
  `_user_prompt()` in `brief.py`.
- **Dashie's product context** (differentiators, competitors to watch) → `DASHIE_CONTEXT` in
  `brief.py`. Keep this current as the product evolves.
- **Model** → `SUMMARY_MODEL` in the workflow (e.g. a newer/cheaper Claude model).
- **How many / which posts** → `BRIEF_MAX_POSTS` and `BRIEF_LISTING` in the workflow.

## Adding more subreddits

Open [`rss-digest.yml`](.github/workflows/rss-digest.yml) and add to the `matrix.subreddit`
list — **one line per subreddit**. Each produces its own brief + email. Nothing else changes:

```yaml
        subreddit:
          - homeassistant
          - smarthome        # ← add
          - homeautomation   # ← add
```

They run as parallel matrix jobs; `fail-fast: false` means one failing sub won't cancel the
others. (If you ever want per-subreddit recipients, split into separate workflows instead.)

---

## Run it locally (test / dry-run)

```bash
cd /Users/johnlerch/projects/dashie-reddit-digest
set -a; source .env; set +a                       # ANTHROPIC_API_KEY etc.
export RESEND_API_KEY="$(tr -d '[:space:]' < ~/.resend_key)"
export DIGEST_FROM="John from Dashie <john@dashieapp.com>"
export DIGEST_TO="jwlerch@gmail.com"
export BRIEF_SUBREDDIT="homeassistant"
.venv/bin/python -m reddit_digest.run_rss
```

Trigger the **cloud** run manually (no waiting for the cron):

```bash
gh workflow run "Daily Dashie Listening Brief (RSS)" --repo jwlerch78/reddit-interest-digest
gh run list --workflow=rss-digest.yml --limit 1     # find the run
gh run view <run-id> --log                          # watch it
```

---

## Troubleshooting

- **No email?** The Actions run can succeed while delivery lags a minute or two. Confirm what
  Resend did (accepted / delivered / bounced):
  ```bash
  RKEY="$(tr -d '[:space:]' < ~/.resend_key)"
  # get the message id from the run log ("Sent brief ... id=..."), then:
  curl -s -A "Dashie/1.0" -H "Authorization: Bearer $RKEY" \
    https://api.resend.com/emails/<id> | python3 -m json.tool
  ```
  Also check Gmail's Promotions / Spam tabs.
- **`No posts fetched` / HTTP 429** → Reddit rate-limited that run. The reader retries with
  backoff; a re-run usually clears it. Don't hammer it.
- **HTTP 403 on the feed** → Reddit tightened the bot wall for that IP/UA. Try a different
  User-Agent in `rss_client.py`, or a different listing.
- **Empty/odd brief** → check the run log for the prompt size and Claude's response; tune
  `BRIEF_MAX_POSTS` or the prompt.

---

## Deprecated paths

These predate the RSS approach and are **not used** by the daily brief:

- `reddit_client.py`, `scorer.py`, `digest.py`, `config.py` — the PRAW + relevance-scoring
  pipeline. Needs Reddit API creds (`REDDIT_*`) we can't get.
- `wiki_pipeline.py` — read a Devvit-written wiki data page. The Devvit app was abandoned
  (its scheduler can't run the LLM; wiki writes 403).

They're kept for reference; delete if they get in the way. The **Devvit** experiment (an app
named *ModRadarAI*) lives in a separate repo and should be uninstalled from r/Dashieapp.

---

## License

MIT — see [LICENSE](LICENSE).
