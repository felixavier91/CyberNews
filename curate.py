"""Ask Claude to drop clickbait, duplicate and low-quality headlines.

Needs ANTHROPIC_API_KEY. If the key is missing or the call fails for any
reason, the original list is returned unchanged so the email still goes out.
"""
import json
import os
import re
import urllib.parse
import urllib.request

import anthropic

from main import clean_title

MODEL = "claude-haiku-5-5"           # filtering: one call over the whole list
SUMMARY_MODEL = "claude-haiku-5-5"   # article checks: simple per-article job

# List prices in $ per million tokens (Haiku 5.5's rate applies to prompts up to 100K)
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}
WEB_SEARCH_PRICE = 0.01  # $10 per 1,000 searches


class CostTracker:
    """Adds up token use and estimated $ cost of the API calls in this run."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.dollars = 0.0
        self.calls = self.input = self.output = self.searches = 0

    def add(self, model, usage):
        p = PRICES[model]
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        server = getattr(usage, "server_tool_use", None)
        searches = (getattr(server, "web_search_requests", 0) or 0) if server else 0
        self.calls += 1
        self.input += usage.input_tokens + cache_read + cache_write
        self.output += usage.output_tokens
        self.searches += searches
        self.dollars += (usage.input_tokens * p["input"] + usage.output_tokens * p["output"]
                         + cache_read * p["cache_read"] + cache_write * p["cache_write"]) / 1e6
        self.dollars += searches * WEB_SEARCH_PRICE

    def report(self, label):
        print(f"COST {label}: ${self.dollars:.3f} ({self.calls} calls, {self.input} in / "
              f"{self.output} out tokens, {self.searches} web searches)")
        self.reset()


COSTS = CostTracker()

INSTRUCTIONS = """\
You are curating a daily cybersecurity news digest read by a corporate \
enterprise risk management (ERM) team at an insurance company. Below are this \
week's headlines from Google News, each with an id, source and publish time.

The readers only want tangible incidents: something specific that actually \
happened to a named organization, sector, system or group of people. Keep a \
headline only if it reports one of these:
- A specific cyber attack, breach, data leak, ransomware or extortion incident, \
or outage caused by an attack.
- A specific named vulnerability that is being exploited or that puts named \
products or organizations at concrete risk.
- A concrete action taken over an attack or attackers: arrests, indictments, \
sanctions, takedowns, fines or lawsuits.
- A specific attributed campaign by a named threat actor or nation state.

Remove everything else, including:
- Predictions, trends, forecasts and "the future of cyber" pieces.
- Opinion, commentary, interviews, think pieces and talking points about the \
cyber ecosystem, AI and security, the skills gap, budgets, and the like.
- Listicles, tips and how-to guides ("5 things to watch", "how to protect yourself").
- Surveys, reports and statistics about the threat landscape in general.
- General policy or regulation talk that isn't tied to a specific incident.
- Product launches, funding rounds, acquisitions, awards, webinars, events and \
other promotional pieces.
- Stock or market commentary, and items where "cyber" means something else \
(Cyber Monday, Cybertruck).
- Clickbait and vague teasers that don't say what actually happened.
- Duplicates: when several headlines cover the same incident, keep only the \
one from the most credible or most informative source.

Judge from the headline and source only. If a headline doesn't make clear that \
a specific incident happened, remove it. Give a short reason for every removal."""

SCHEMA = {
    "type": "object",
    "properties": {
        "removed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["removed"],
    "additionalProperties": False,
}


CHECK_INSTRUCTIONS = """\
You are checking one article for a corporate enterprise risk management \
team's daily cyber incident digest. Its headline already passed a filter.

Headline: {headline}
Source: {source}

Fetch and read the article at this URL: {url}

Decide whether the article itself reports a tangible incident: a specific \
cyber attack, breach, data leak, ransomware or extortion incident, outage \
caused by an attack, actively exploited vulnerability, arrest, sanction, fine, \
lawsuit or attributed campaign that happened to a named organization, sector, \
system or group of people.

Drop it if the body turns out to be clickbait or filler: a listicle, tips or \
how-to, opinion, trend or prediction piece, survey, promotion, or a vague \
teaser that never says what actually happened.

Reply with exactly KEEP or DROP, then a dash and a short reason. If you can't \
read the article (paywall, blocked, not found, video only), reply \
"UNREAD - " and the reason."""

HEADLINE_ONLY = "[headline only] "

UA = {"User-Agent": "Mozilla/5.0 (compatible; ThisWeekInCyber/1.0)"}


def _http(url, data=None, headers=None) -> str:
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode()


def publisher_url(link):
    """The publisher's URL behind a Google News link, or None if it can't be decoded.

    Google News links redirect with JavaScript, so ask Google's own decoder endpoint.
    """
    try:
        article_id = urllib.parse.urlparse(link).path.split("/")[-1]
        page = _http(f"https://news.google.com/rss/articles/{article_id}")
        sig = re.search(r'data-n-a-sg="([^"]+)"', page).group(1)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page).group(1)
        req = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
               f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{article_id}",{ts},"{sig}"]')
        body = urllib.parse.urlencode({"f.req": json.dumps([[["Fbv4je", req, None, "generic"]]])}).encode()
        resp = _http("https://news.google.com/_/DotsSplashUi/data/batchexecute", body,
                     {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        url = json.loads(json.loads(resp.split("\n\n")[1])[0][2])[1]
        return url if url.startswith("http") else None
    except Exception:
        return None


def _check_one(client, entry, url) -> str:
    """Claude's verdict on one article: 'KEEP - ...', 'DROP - ...' or 'UNREAD - ...'."""
    prompt = CHECK_INSTRUCTIONS.format(headline=clean_title(entry), source=_source(entry), url=url)
    messages = [{"role": "user", "content": prompt}]
    # One capped fetch and no web search keeps each check to a fraction of a cent
    tools = [{"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 1,
              "max_content_tokens": 3000}]
    for _ in range(3):  # resume if the server-side tool loop pauses
        response = client.messages.create(
            model=SUMMARY_MODEL,
            max_tokens=2000,
            output_config={"effort": "low"},
            tools=tools,
            messages=messages,
        )
        COSTS.add(SUMMARY_MODEL, response.usage)
        if response.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"stopped with {response.stop_reason}")

    # The verdict is the text written after the last tool result
    text = []
    for block in response.content:
        if block.type == "text":
            text.append(block.text)
        elif block.type.endswith("tool_result"):
            text = []
    return " ".join("".join(text).split())


def check_articles(entries):
    """Read each article and drop the ones whose body turns out to be clickbait.

    Articles Claude can't read stay in on the strength of their headline.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set; skipping article checks.")
        return entries
    client = anthropic.Anthropic()
    kept = []
    for entry in entries:
        try:
            url = publisher_url(entry.get("link", ""))
            verdict = _check_one(client, entry, url) if url else "UNREAD - link not decoded"
        except Exception as e:  # a failed check never blocks the site or email
            verdict = f"UNREAD - {type(e).__name__}: {e}"
        print(f"  {verdict[:200]} [{_source(entry)}] {clean_title(entry)[:60]}")
        if not verdict.startswith("DROP"):
            kept.append(entry)
    COSTS.report("Article checks")
    return kept or entries


def summary_parts(entry):
    """(summary text, from_headline_only) for display, or (None, False)."""
    summary = entry.get("ai_summary")
    if not summary:
        return None, False
    if summary.startswith(HEADLINE_ONLY.strip()):
        return summary[len(HEADLINE_ONLY.strip()):].strip(), True
    return summary, False


def _source(entry) -> str:
    if isinstance(entry.get("source"), dict):
        return entry["source"].get("title", "")
    return ""


def curate(entries):
    """Return (kept_entries, removed) where removed is [(entry, reason), ...]."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set; skipping curation.")
        return entries, []

    listing = "\n".join(
        json.dumps({"id": i, "headline": clean_title(e), "source": _source(e),
                    "published": e.get("published", "")})
        for i, e in enumerate(entries)
    )

    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            messages=[{"role": "user", "content": f"{INSTRUCTIONS}\n\n<headlines>\n{listing}\n</headlines>"}],
        )
        COSTS.add(MODEL, response.usage)
        COSTS.report("Filtering")
        if response.stop_reason != "end_turn":
            print(f"Curation stopped early ({response.stop_reason}); sending unfiltered.")
            return entries, []
        text = next(b.text for b in response.content if b.type == "text")
        removed_ids = {r["id"]: r["reason"] for r in json.loads(text)["removed"]}
    except Exception as e:  # never let curation block the email
        print(f"Curation failed ({type(e).__name__}: {e}); sending unfiltered.")
        return entries, []

    kept = [e for i, e in enumerate(entries) if i not in removed_ids]
    removed = [(entries[i], reason) for i, reason in removed_ids.items() if 0 <= i < len(entries)]
    if not kept:
        print("Curation removed everything; sending unfiltered.")
        return entries, []

    print(f"Curation kept {len(kept)} of {len(entries)} headlines "
          f"({response.usage.input_tokens} in / {response.usage.output_tokens} out tokens).")
    for entry, reason in removed:
        print(f"  REMOVED [{_source(entry)}] {clean_title(entry)} -- {reason}")
    return kept, removed
