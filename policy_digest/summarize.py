"""
Plain-language summaries of new Journal officiel texts, for the YFIP team.

For each text kept by legifrance.py, the full text is sent to Claude, which
returns a short structured summary in French and English:
  gist      - what the text does, in one sentence
  changes   - what is new or different (numbers, dates, articles)
  who       - which clients / employers are affected
  when      - entry into force and any transition period
  for_us    - what YFIP should do differently
It also says whether the text actually matters to YFIP; texts that don't are
dropped from the page (and listed in the run log).

Needs the ANTHROPIC_API_KEY secret. Without it, the page falls back to the
official "notice" (objet / entrée en vigueur) published with the decree.
"""
import json
import os
import re

MODEL = os.environ.get("SUMMARY_MODEL", "claude-sonnet-5-5")
MAX_TEXT_CHARS = 60_000

PROMPT = """You are summarising a new French legal text for Your Friend in Paris (YFIP),
a relocation and immigration consultancy helping foreigners (mostly Americans)
move to, live in and work in France.

YFIP only cares about texts touching:
- droit des étrangers / CESEDA (visas, titres de séjour, asile, naturalisation rules...)
- ressortissants étrangers (rights and obligations of foreign nationals)
- droit du travail (employment rules affecting employees or employers in France)
- Code de la sécurité sociale (health cover, CPAM/PUMa, contributions, benefits)

Reply with ONE JSON object and nothing else:
{{
  "relevant": true or false,   // false if it has no practical effect on YFIP clients
                               // (individual measures, lists of names, drug price lists,
                               // a single sector's collective agreement, internal admin...)
  "fr": {{
    "gist": "one sentence: what this text does, in plain French",
    "changes": ["2-5 short bullets: what is new or different - name the numbers, amounts, deadlines, documents, eligibility rules and articles"],
    "who": "which kinds of clients or employers are affected",
    "when": "entry into force date and any transition period, or 'Non précisé'",
    "for_us": ["1-3 bullets: what YFIP should do - advise clients, update a guide or checklist, change timelines"]
  }},
  "en": {{ same keys, same content, in plain English }}
}}
If "relevant" is false, the "fr" and "en" objects may be short.
Be specific and practical. Don't pad, but don't cut real content just to be short.

TITLE: {title}
PUBLISHED: {date}

TEXT:
{body}
"""

_client = None


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def _parse(text):
    """Pull the JSON object out of the reply, even if wrapped in ```json fences."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    data = json.loads(m.group(0))
    for lang in ("fr", "en"):
        block = data.get(lang) or {}
        for key in ("changes", "for_us"):
            if isinstance(block.get(key), str):
                block[key] = [block[key]]
        data[lang] = block
    data["relevant"] = bool(data.get("relevant", True))
    return data


def summarise(title, date, body):
    """Returns the parsed dict, or raises on API/format errors."""
    msg = _get_client().messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": PROMPT.format(
            title=title, date=date or "n/a", body=(body or title)[:MAX_TEXT_CHARS])}],
    )
    reply = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return _parse(reply)
