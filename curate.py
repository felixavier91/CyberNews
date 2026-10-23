"""Ask Claude to drop clickbait, duplicate and low-quality headlines.

Needs ANTHROPIC_API_KEY. If the key is missing or the call fails for any
reason, the original list is returned unchanged so the email still goes out.
"""
import json
import os

import anthropic

from main import clean_title

MODEL = "claude-opus-5-5"            # filtering: judgment calls on the whole list
SUMMARY_MODEL = "claude-haiku-5-5"   # summaries: simple per-article job, much cheaper

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


SUMMARY_INSTRUCTIONS = """\
Write a short summary of one news article for a corporate enterprise risk \
management team's daily cyber incident digest.

Headline: {headline}
Source: {source}
Published: {published}
Google News link: {link}

Find and read this article: search for the headline and source, and fetch the \
article from the publisher's site. Then write 2-3 plain sentences covering what \
happened, who was affected, and the impact or current status. Use only facts \
stated in the article. Don't speculate, editorialize or add advice.

If you can't read the article (paywall, blocked, not found), write one \
sentence based only on the headline and start your reply with the exact text \
"[headline only] ".

Reply with the summary only: no title, links, citations, bullet points or preamble."""

HEADLINE_ONLY = "[headline only] "


def _summarize_one(client, entry) -> str:
    prompt = SUMMARY_INSTRUCTIONS.format(
        headline=clean_title(entry), source=_source(entry),
        published=entry.get("published", ""), link=entry.get("link", ""),
    )
    messages = [{"role": "user", "content": prompt}]
    # One search and one capped fetch per article keeps each summary to a few cents
    tools = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 1},
        {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 1,
         "max_content_tokens": 3000},
    ]
    for _ in range(3):  # resume if the server-side tool loop pauses
        response = client.messages.create(
            model=SUMMARY_MODEL,
            max_tokens=4000,
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

    # The summary is the text written after the last tool result
    text = []
    for block in response.content:
        if block.type == "text":
            text.append(block.text)
        elif block.type.endswith("tool_result"):
            text = []
    summary = " ".join("".join(text).split())
    if not summary:
        raise RuntimeError("empty summary")
    return summary


def summarize(entries):
    """Add an 'ai_summary' to each entry; entries that fail just go without one."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set; skipping summaries.")
        return entries
    client = anthropic.Anthropic()
    for entry in entries:
        try:
            entry["ai_summary"] = _summarize_one(client, entry)
            print(f"  SUMMARY [{_source(entry)}] {clean_title(entry)[:60]}: {entry['ai_summary']}")
        except Exception as e:  # a failed summary never blocks the site or email
            print(f"  NO SUMMARY [{_source(entry)}] {clean_title(entry)[:60]} "
                  f"({type(e).__name__}: {e})")
    COSTS.report("Summaries")
    return entries


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
