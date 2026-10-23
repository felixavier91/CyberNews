"""Ask Claude to drop clickbait, duplicate and low-quality headlines.

Needs ANTHROPIC_API_KEY. If the key is missing or the call fails for any
reason, the original list is returned unchanged so the email still goes out.
"""
import json
import os

import anthropic

from main import clean_title

MODEL = "claude-opus-5-5"

INSTRUCTIONS = """\
You are curating a daily cybersecurity news digest read by a corporate \
enterprise risk management (ERM) team at an insurance company. Below are this \
week's headlines from Google News, each with an id, source and publish time.

Keep headlines that report real, substantive cybersecurity news: breaches, \
attacks, ransomware, vulnerabilities, threat actors, regulation and policy, \
enforcement, major industry developments, and analysis from credible outlets.

Remove:
- Clickbait and vague teasers ("You won't believe...", "This one trick...", listicles).
- Duplicates: when several headlines cover the same story, keep only the one \
from the most credible or most informative source.
- Low-quality, SEO-spam or content-farm sources, press-release mills, and \
sponsored or promotional pieces (product launches, webinars, awards, "top 10 tools").
- Items that aren't really cybersecurity news (stock picks, "cyber" used for \
something unrelated, such as Cyber Monday or Cybertruck).

Judge from the headline and source only. When unsure, keep the item. Give a \
short reason for every removal."""

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
