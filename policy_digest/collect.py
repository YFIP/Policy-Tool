"""
Weekly legal & policy watch for YFIP.

Collects the past week's news from French government sources (see
sources.json), keeps what is relevant to YFIP's clients (keywords.json),
and writes:
  - docs/index.html : the web page the team opens (published by GitHub Pages)
  - history.json    : every item reported, by week (the page shows 12 weeks)
  - state.json      : links already reported, so nothing appears twice

Only public government news is read. No client data is involved.

Usage: python collect.py
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata
from urllib.parse import urljoin, urldefrag

import feedparser
import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; YFIP-policy-watch/1.0)"
TIMEOUT = 30
LOOKBACK_DAYS = 8          # a little over a week, so nothing falls between runs
MAX_PER_SOURCE = 15
FIRST_RUN_PER_PAGE = 5     # first time a news page is read, report only its newest few
MAX_SUMMARY_CHARS = 300
KEEP_SEEN_DAYS = 180


def norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


def clean(text, limit=MAX_SUMMARY_CHARS):
    text = BeautifulSoup(text or "", "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def match_topics(text, keywords):
    t = norm(text)
    hits = []
    for topic, words in keywords.items():
        if topic.startswith("_"):
            continue
        if any(re.search(r"(^|[^a-z])" + re.escape(norm(w)), t) for w in words):
            hits.append(topic)
    return hits


def short_error(url, exc):
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        why = f"erreur HTTP {exc.response.status_code}"
    elif isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        why = "site injoignable"
    else:
        why = str(exc)[:120]
    return f"{url} : {why}"


def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def read_rss(source, since):
    last_error = None
    for url in source["urls"]:
        try:
            feed = feedparser.parse(fetch(url).content)
            if feed.bozo and not feed.entries:
                raise ValueError(f"not a valid feed ({feed.bozo_exception})")
            items = []
            for e in feed.entries:
                published = None
                for key in ("published_parsed", "updated_parsed"):
                    if e.get(key):
                        published = dt.datetime(*e[key][:6], tzinfo=dt.timezone.utc)
                        break
                if published and published < since:
                    continue
                items.append({
                    "title": clean(e.get("title"), 200),
                    "link": urldefrag(e.get("link", ""))[0],
                    "date": published.date().isoformat() if published else None,
                    "summary": clean(e.get("summary") or e.get("description")),
                })
            return items, url, None
        except Exception as exc:  # try the next URL for this source
            last_error = short_error(url, exc)
    return [], None, last_error


def read_page(source):
    last_error = None
    for url in source["urls"]:
        try:
            soup = BeautifulSoup(fetch(url).content, "html.parser")  # bytes: lets the page's own charset decide
            for tag in soup(["nav", "header", "footer", "script", "style"]):
                tag.decompose()
            base_path = re.sub(r"https?://[^/]+", "", url).rstrip("/")
            items, seen = [], set()
            for a in soup.find_all("a", href=True):
                link = urldefrag(urljoin(url, a["href"]))[0]
                path = re.sub(r"https?://[^/]+", "", link).rstrip("/")
                title = clean(a.get_text(" "), 200)
                if (source.get("link_contains", "") not in path or path == base_path
                        or path in seen or len(title) < 20
                        or re.search(r"[?&](page|p)=\d|/page/\d", link)):
                    continue
                seen.add(path)
                # A short teaser often sits next to the link inside its card.
                card = a.find_parent(["article", "li", "div"])
                summary = clean(card.get_text(" ")) if card else ""
                if summary.startswith(title):
                    summary = summary[len(title):].strip(" -–:")
                items.append({"title": title, "link": link, "date": None, "summary": summary})
            if not items:
                raise ValueError("no news links found on the page (layout may have changed)")
            return items, url, None
        except Exception as exc:
            last_error = short_error(url, exc)
    return [], None, last_error


from render import render_page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=os.path.join(HERE, "state.json"))
    ap.add_argument("--sources", default=os.path.join(HERE, "sources.json"))
    ap.add_argument("--keywords", default=os.path.join(HERE, "keywords.json"))
    ap.add_argument("--history", default=os.path.join(HERE, "history.json"))
    ap.add_argument("--page", default=os.path.join(HERE, "..", "docs", "index.html"))
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=LOOKBACK_DAYS)
    sources = load_json(args.sources, {})["sources"]
    keywords = load_json(args.keywords, {})
    state = load_json(args.state, {"seen": {}, "initialised": []})

    all_items, problems, report = [], [], []
    for src in sources:
        name = src["name"]
        if src["type"] == "rss":
            items, used_url, error = read_rss(src, since)
        else:
            items, used_url, error = read_page(src)
        if error:
            problems.append(f"{name} ({error})")
            report.append(f"✗ {name}: {error}")
            continue

        first_time = src["type"] == "page" and name not in state["initialised"]
        kept = []
        for it in items:
            if not it["link"] or it["link"] in state["seen"]:
                continue
            topics = match_topics(it["title"] + " " + it["summary"], keywords)
            if src.get("keyword_filter") and not topics:
                continue
            it["source"] = name
            it["topics"] = topics or [src.get("default_topic", "Autre")]
            kept.append(it)

        new_links = [it["link"] for it in items if it["link"]]
        if first_time:
            # Don't report a page's whole back catalogue on the first run.
            kept = kept[:FIRST_RUN_PER_PAGE]
            state["initialised"].append(name)
            for link in new_links:
                state["seen"].setdefault(link, now.date().isoformat())
        kept = kept[:MAX_PER_SOURCE]
        for it in kept:
            state["seen"][it["link"]] = now.date().isoformat()
        all_items += kept
        report.append(f"✓ {name}: {len(items)} read, {len(kept)} new & relevant ({used_url})")

    # Forget links older than KEEP_SEEN_DAYS so the file doesn't grow forever.
    cutoff = (now - dt.timedelta(days=KEEP_SEEN_DAYS)).date().isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}

    history = load_json(args.history, {"weeks": []})
    week = {
        "start": (now - dt.timedelta(days=7)).date().isoformat(),
        "end": now.date().isoformat(),
        "items": all_items,
        "problems": problems,
    }
    # A second run on the same day adds to that day's entry instead of replacing it.
    same_day = next((w for w in history["weeks"] if w["end"] == week["end"]), None)
    if same_day:
        week["items"] = same_day["items"] + all_items
    history["weeks"] = [week] + [w for w in history["weeks"] if w["end"] != week["end"]]
    history["weeks"] = history["weeks"][:12]

    os.makedirs(os.path.dirname(os.path.abspath(args.page)), exist_ok=True)
    with open(args.page, "w", encoding="utf-8") as f:
        f.write(render_page(history, now))
    with open(args.history, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)

    print("\n".join(report))
    print(f"\nTotal: {len(all_items)} new item(s) added to the page; {len(problems)} source problem(s).")
    # Fail only if every source failed: then something is wrong with the run itself.
    if sources and len(problems) == len(sources):
        sys.exit(1)


if __name__ == "__main__":
    main()
