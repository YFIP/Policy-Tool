#!/usr/bin/env python3
"""
YFIP Policy Watch - weekly Legifrance (JORF) digest
====================================================
Pulls new texts from the Journal Officiel via the Legifrance API (PISTE),
keeps ONLY the 4 areas YFIP cares about, and writes useful summaries:
what changed, who it affects, when it applies, and how it affects us.
The report ends with a "Sources" section listing exactly what was searched.

Setup
-----
1. pip install requests anthropic python-dotenv --break-system-packages
2. Create a .env file next to this script:
       PISTE_CLIENT_ID=...
       PISTE_CLIENT_SECRET=...
       ANTHROPIC_API_KEY=...
       PISTE_SANDBOX=false        # true to use the PISTE sandbox
3. Run:  python3 yfip_policy_watch.py            (last 7 days)
         python3 yfip_policy_watch.py --days 14
         python3 yfip_policy_watch.py --debug    (saves raw API replies to ./debug/)
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

try:
    import anthropic
except ImportError:
    anthropic = None

load_dotenv()

# ---------------------------------------------------------------------------
# 1. WHAT WE WATCH  (Seville's list - edit here to add or remove topics)
# ---------------------------------------------------------------------------
AREAS = {
    "Droit des étrangers / CESEDA": [
        "code de l'entrée et du séjour des étrangers",
        "titre de séjour",
        "carte de séjour",
        "droit d'asile",
    ],
    "Ressortissants étrangers": [
        "ressortissants étrangers",
        "ressortissant étranger",
        "travailleur étranger",
        "autorisation de travail",
    ],
    "Droit du travail": [
        "code du travail",
    ],
    "Sécurité sociale": [
        "code de la sécurité sociale",
    ],
}

# Types of text to include. ARRETE is off by default because "code du travail"
# matches dozens of collective-agreement arrêtés every week. Add "ARRETE" if
# Seville wants them.
NATURES = ["LOI", "ORDONNANCE", "DECRET"]

MODEL = "claude-sonnet-5-5"
MAX_TEXT_CHARS = 60_000  # long decrees get trimmed before summarising

# ---------------------------------------------------------------------------
# 2. SUMMARY PROMPT
# ---------------------------------------------------------------------------
SUMMARY_PROMPT = """You are summarising a new French legal text for Your Friend in Paris (YFIP),
a relocation and immigration consultancy that helps foreigners (mostly Americans)
move to, live in and work in France.

Only texts touching these areas matter: ressortissants étrangers, droit des
étrangers / CESEDA, droit du travail, Code de la sécurité sociale.
If this text has no practical relevance to YFIP or its clients, reply with the
single word SKIP and nothing else.

Otherwise reply in English, in exactly this markdown format:

**Area:** <one or more of the 4 areas>
**In one line:** <what this text does, plainly>

**What changes**
- <2-5 bullets: what is new or different vs. before - thresholds, amounts,
  deadlines, documents, eligibility, procedures. Name the numbers, dates and
  articles.>

**Who it affects**
- <which kinds of clients or employers, e.g. talent passport holders,
  employees on a salarié visa, family reunification, students, employers of
  foreign staff, people registering with CPAM>

**When it applies**
- <entry into force date and any transition period; say "not stated" if absent>

**How this affects us**
- <1-3 bullets: what YFIP should do differently - advise clients, update a
  checklist or guide, change timelines, warn people mid-procedure>

Be specific and practical. Do not pad, but do not cut real content to be short.

TEXT TITLE: {title}
PUBLISHED: {date}

TEXT:
{body}
"""

# ---------------------------------------------------------------------------
# 3. LEGIFRANCE API CLIENT
# ---------------------------------------------------------------------------
SANDBOX = os.getenv("PISTE_SANDBOX", "false").lower() == "true"
OAUTH_URL = ("https://sandbox-oauth.piste.gouv.fr" if SANDBOX
             else "https://oauth.piste.gouv.fr") + "/api/oauth/token"
API_URL = ("https://sandbox-api.piste.gouv.fr" if SANDBOX
           else "https://api.piste.gouv.fr") + "/dila/legifrance/lf-engine-app"


class Legifrance:
    def __init__(self, debug=False):
        self.client_id = os.getenv("PISTE_CLIENT_ID")
        self.client_secret = os.getenv("PISTE_CLIENT_SECRET")
        if not (self.client_id and self.client_secret):
            sys.exit("Missing PISTE_CLIENT_ID / PISTE_CLIENT_SECRET in .env")
        self.token = None
        self.token_expiry = 0
        self.debug = debug
        if debug:
            Path("debug").mkdir(exist_ok=True)

    def _auth(self):
        if self.token and time.time() < self.token_expiry - 60:
            return
        r = requests.post(OAUTH_URL, data={
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "openid",
        }, timeout=30)
        r.raise_for_status()
        data = r.json()
        self.token = data["access_token"]
        self.token_expiry = time.time() + int(data.get("expires_in", 3600))

    def post(self, path, body, debug_name=None):
        self._auth()
        for attempt in range(3):
            r = requests.post(API_URL + path, json=body, timeout=60, headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
            if r.status_code == 401:          # token expired - refresh once
                self.token = None
                self._auth()
                continue
            if r.status_code == 429:          # rate limited - wait and retry
                time.sleep(5 * (attempt + 1))
                continue
            if not r.ok:
                raise RuntimeError(f"{path} -> {r.status_code}: {r.text[:500]}")
            data = r.json()
            if self.debug and debug_name:
                Path("debug", f"{debug_name}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2))
            return data
        raise RuntimeError(f"{path} failed after retries")

    def search_jorf(self, term, start, end):
        """All JORF texts published between start and end containing `term`."""
        results, page = [], 1
        while True:
            body = {
                "fond": "JORF",
                "recherche": {
                    "champs": [{
                        "typeChamp": "ALL",
                        "criteres": [{"typeRecherche": "EXACTE",
                                      "valeur": term, "operateur": "ET"}],
                        "operateur": "ET",
                    }],
                    "filtres": [
                        {"facette": "DATE_PUBLICATION",
                         "dates": {"start": start, "end": end}},
                        {"facette": "NATURE", "valeurs": NATURES},
                    ],
                    "pageNumber": page,
                    "pageSize": 50,
                    "operateur": "ET",
                    "sort": "DATE_PUBLI_DESC",
                    "typePagination": "DEFAUT",
                },
            }
            data = self.post("/search", body,
                             debug_name=f"search_{slug(term)}_p{page}")
            batch = data.get("results") or []
            results.extend(batch)
            total = data.get("totalResultNumber") or 0
            if not batch or page * 50 >= total or page >= 5:
                return results
            page += 1

    def get_text(self, cid):
        return self.post("/consult/jorf", {"textCid": cid}, debug_name=f"text_{cid}")


# ---------------------------------------------------------------------------
# 4. HELPERS
# ---------------------------------------------------------------------------
def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower())[:40]


def strip_html(s):
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


def result_id_and_title(r):
    """Search results put id/title under `titles`; be tolerant of variations."""
    titles = r.get("titles") or [{}]
    t = titles[0] if titles else {}
    cid = t.get("cid") or t.get("id") or r.get("cid") or r.get("id")
    title = t.get("title") or r.get("title") or "(untitled)"
    return cid, strip_html(title)


def collect_articles(node, out):
    """Walk the consult response and pull every article's text, in order."""
    if isinstance(node, dict):
        for art in node.get("articles") or []:
            num = art.get("num") or ""
            content = strip_html(art.get("content") or art.get("texte") or "")
            if content:
                out.append(f"Article {num}\n{content}" if num else content)
        for sec in node.get("sections") or []:
            if sec.get("title"):
                out.append(f"## {strip_html(sec['title'])}")
            collect_articles(sec, out)
    return out


def text_body(doc):
    parts = []
    for key in ("notice", "visa"):          # decrees carry a handy "notice"
        if doc.get(key):                    # (objet / entrée en vigueur)
            parts.append(strip_html(doc[key]))
    parts += collect_articles(doc, [])
    body = "\n\n".join(p for p in parts if p)
    return body[:MAX_TEXT_CHARS]


def summarise(client, title, date, body):
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": SUMMARY_PROMPT.format(
            title=title, date=date, body=body)}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# 5. MAIN
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--out", default="policy_report")
    args = ap.parse_args()

    end = dt.date.today()
    start = end - dt.timedelta(days=args.days)
    s, e = start.isoformat(), end.isoformat()

    lf = Legifrance(debug=args.debug)
    if anthropic is None or not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("Install `anthropic` and set ANTHROPIC_API_KEY in .env")
    ai = anthropic.Anthropic()

    # --- search every term, de-duplicate, remember which area(s) matched ---
    found, searches = {}, []
    for area, terms in AREAS.items():
        for term in terms:
            hits = lf.search_jorf(term, s, e)
            searches.append((area, term, len(hits)))
            for r in hits:
                cid, title = result_id_and_title(r)
                if not cid:
                    continue
                entry = found.setdefault(cid, {"title": title, "areas": set(),
                                               "date": r.get("datePublication")
                                               or r.get("date") or ""})
                entry["areas"].add(area)
            print(f"  {area:32} '{term}': {len(hits)} hit(s)")

    # --- fetch + summarise each text ---
    kept, skipped = [], []
    for cid, info in found.items():
        print(f"Summarising {cid} - {info['title'][:70]}")
        try:
            doc = lf.get_text(cid)
            body = text_body(doc) or info["title"]
            date = (doc.get("dateParution") or doc.get("datePubli")
                    or info["date"] or "")
            summary = summarise(ai, info["title"], date, body)
        except Exception as ex:                     # keep going on errors
            summary = f"_Could not process this text: {ex}_"
        link = f"https://www.legifrance.gouv.fr/jorf/id/{cid}"
        item = {**info, "cid": cid, "link": link, "summary": summary,
                "areas": sorted(info["areas"])}
        (skipped if summary.strip().upper() == "SKIP" else kept).append(item)

    write_report(args.out, s, e, kept, skipped, searches)


def write_report(out, start, end, kept, skipped, searches):
    md = [f"# YFIP Policy Watch: {start} to {end}", ""]
    if not kept:
        md += ["No new texts relevant to YFIP this period.", ""]
    for i, k in enumerate(kept, 1):
        md += [f"## {i}. {k['title']}",
               f"[Read on Legifrance]({k['link']}) · Published {k['date'] or 'n/a'}",
               "", k["summary"], "", "---", ""]

    md += ["## Sources", "",
           f"- **Database:** Journal Officiel (JORF) via the official Legifrance API",
           f"- **Period:** texts published {start} to {end}",
           f"- **Types of text:** {', '.join(NATURES)}",
           "- **Areas and search terms:**"]
    for area, terms in AREAS.items():
        md.append(f"  - *{area}:* " + "; ".join(f"\"{t}\"" for t in terms))
    md += ["", f"- **Found:** {len(kept) + len(skipped)} texts · "
           f"**summarised:** {len(kept)} · "
           f"**skipped as not relevant:** {len(skipped)}"]
    if skipped:
        md += ["", "<details><summary>Skipped texts</summary>", ""]
        md += [f"- [{s['title']}]({s['link']})" for s in skipped]
        md += ["", "</details>"]

    text = "\n".join(md)
    Path(f"{out}.md").write_text(text, encoding="utf-8")

    try:                                    # optional nice HTML version
        import markdown
        body = markdown.markdown(text, extensions=["extra"])
    except ImportError:
        body = "<pre>" + html.escape(text) + "</pre>"
    Path(f"{out}.html").write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>YFIP Policy Watch {start} to {end}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:820px;"
        "margin:40px auto;padding:0 20px;line-height:1.55;color:#1f2933}"
        "h1{color:#1F3A5F}h2{color:#1F3A5F;margin-top:2em}"
        "a{color:#2563eb}hr{border:0;border-top:1px solid #ddd}</style>"
        f"</head><body>{body}</body></html>", encoding="utf-8")
    print(f"\nDone: {out}.md and {out}.html "
          f"({len(kept)} summarised, {len(skipped)} skipped)")


if __name__ == "__main__":
    main()
