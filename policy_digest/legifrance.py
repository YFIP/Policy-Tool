"""
Reads new texts published in the Journal officiel (JORF) through the
official Légifrance API on PISTE (https://piste.gouv.fr).

Needs two GitHub secrets (see README):
  LEGIFRANCE_CLIENT_ID, LEGIFRANCE_CLIENT_SECRET
Optional: LEGIFRANCE_ENV=sandbox to use the PISTE sandbox instead of production.

Two ways of listing the week's texts are tried, in order:
  1. /search on the JORF collection, filtered by publication date and nature
  2. /consult/lastNJo + /consult/jorfCont (the tables of contents of the last
     Journaux officiels), used if the search is refused or returns nothing.
Results are read defensively (any object carrying a JORFTEXT id is a text),
so small changes in the API's JSON layout don't break the weekly run.
"""
import datetime as dt
import os
import re
import unicodedata

import requests

import summarize

ENDPOINTS = {
    "production": ("https://oauth.piste.gouv.fr/api/oauth/token",
                   "https://api.piste.gouv.fr/dila/legifrance/lf-engine-app"),
    "sandbox": ("https://sandbox-oauth.piste.gouv.fr/api/oauth/token",
                "https://sandbox-api.piste.gouv.fr/dila/legifrance/lf-engine-app"),
}
TIMEOUT = 40
PAGE_SIZE = 100
MAX_PAGES = 10

NATURE_LABEL = {
    "LOI": "Loi", "LOI_CONSTIT": "Loi constitutionnelle", "LOI_ORGANIQUE": "Loi organique",
    "ORDONNANCE": "Ordonnance", "DECRET": "Décret", "DECRET_LOI": "Décret-loi",
    "ARRETE": "Arrêté", "DECISION": "Décision", "CIRCULAIRE": "Circulaire", "AVIS": "Avis",
}


def _plain(text):
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def nature_from_title(title):
    """'LOI n° 2026-12 du …' -> LOI, 'Décret n° …' -> DECRET, etc."""
    t = _plain(title).strip()
    for prefix, nature in (("loi constitutionnelle", "LOI_CONSTIT"), ("loi organique", "LOI_ORGANIQUE"),
                           ("loi", "LOI"), ("ordonnance", "ORDONNANCE"), ("decret", "DECRET"),
                           ("arrete", "ARRETE"), ("decision", "DECISION"), ("circulaire", "CIRCULAIRE"),
                           ("avis", "AVIS")):
        if re.match(prefix + r"\b", t):
            return nature
    return None


def _to_date(value):
    """API dates come as epoch milliseconds or ISO strings."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).lstrip("-").isdigit():
            return dt.datetime.fromtimestamp(int(value) / 1000, dt.timezone.utc).date().isoformat()
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _walk_texts(node, out, context=None):
    """Collect every object that looks like a JORF text: an id starting with
    JORFTEXT plus a title. `context` carries the nearest section heading
    (usually the issuing ministry) from JO tables of contents."""
    if isinstance(node, dict):
        heading = node.get("titre") if isinstance(node.get("titre"), str) and "tms" in node else None
        ctx = heading or context
        tid = next((node.get(k) for k in ("id", "cid", "textCid", "idTexte")
                    if isinstance(node.get(k), str) and node.get(k).startswith("JORFTEXT")), None)
        title = node.get("titre") or node.get("title") or node.get("titreTxt")
        if tid and isinstance(title, str) and title.strip():
            out.setdefault(tid, {
                "id": tid,
                "title": re.sub(r"<[^>]+>", "", title).strip(),
                "nature": (node.get("nature") or "").upper() or None,
                "date": next((_to_date(node.get(k)) for k in ("datePubli", "datePublication", "date_publi",
                                                             "publicationDate") if node.get(k)), None),
                "section": context,
            })
        for v in node.values():
            _walk_texts(v, out, ctx)
    elif isinstance(node, list):
        for v in node:
            _walk_texts(v, out, context)
    return out


def _search_results(data):
    """A /search result row has `titles` (with the JORFTEXT id) and the row's
    own nature/date; merge them so each text gets all its metadata."""
    texts = {}
    for row in data.get("results") or []:
        found = _walk_texts(row, {})
        for t in found.values():
            t["nature"] = t["nature"] or (row.get("nature") or "").upper() or None
            t["date"] = t["date"] or next((_to_date(row.get(k)) for k in
                                           ("datePublication", "datePubli", "date", "dateTexte") if row.get(k)), None)
            texts.setdefault(t["id"], t)
    return texts


def _strip(html_text):
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", html_text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _articles(node, out):
    """Every article's text, in order, from a /consult/jorf reply."""
    if isinstance(node, dict):
        for art in node.get("articles") or []:
            body = _strip(art.get("content") or art.get("texte") or "")
            if body:
                out.append((f"Article {art['num']}\n" if art.get("num") else "") + body)
        for sec in node.get("sections") or []:
            if isinstance(sec, dict) and sec.get("title"):
                out.append(_strip(sec["title"]))
            _articles(sec, out)
    return out


def text_parts(doc):
    """(notice, full_text) from a /consult/jorf reply. The notice is the short
    official summary decrees carry (Publics concernés / Objet / Entrée en vigueur)."""
    notice = _strip(doc.get("notice") or "")
    parts = [notice] + _articles(doc, [])
    return notice, "\n\n".join(p for p in parts if p)


class LegifranceClient:
    def __init__(self, client_id, client_secret, env="production"):
        self.token_url, self.api = ENDPOINTS.get(env, ENDPOINTS["production"])
        self.client_id, self.client_secret = client_id, client_secret
        self.session = requests.Session()
        self._token = None

    def token(self):
        if not self._token:
            r = self.session.post(self.token_url, timeout=TIMEOUT, data={
                "grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.client_secret, "scope": "openid"})
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return self._token

    def post(self, path, body):
        r = self.session.post(self.api + path, json=body, timeout=TIMEOUT, headers={
            "Authorization": f"Bearer {self.token()}", "Accept": "application/json"})
        r.raise_for_status()
        return r.json()

    def search_jorf(self, start, end, natures):
        """All JORF texts of the given natures published between start and end."""
        texts = {}
        for page in range(1, MAX_PAGES + 1):
            body = {"fond": "JORF", "recherche": {
                "champs": [{"typeChamp": "ALL", "operateur": "ET", "criteres": [
                    {"typeRecherche": "TOUS_LES_MOTS_DANS_UN_CHAMP", "valeur": "*", "operateur": "ET"}]}],
                "filtres": [{"facette": "NATURE", "valeurs": natures},
                            {"facette": "DATE_PUBLICATION", "dates": {"start": start, "end": end}}],
                "operateur": "ET", "sort": "DATE_PUBLI_DESC", "typePagination": "DEFAULT",
                "pageNumber": page, "pageSize": PAGE_SIZE}}
            data = self.post("/search", body)
            found = _search_results(data)
            texts.update(found)
            total = data.get("totalResultNumber") or 0
            if len(found) < PAGE_SIZE or page * PAGE_SIZE >= total:
                break
        return texts

    def get_text(self, text_id):
        """Full content of one JORF text (notice, visas, articles)."""
        return self.post("/consult/jorf", {"textCid": text_id})

    def recent_jo_texts(self, start):
        """Every text in each Journal officiel published since `start`, read
        from the JO tables of contents."""
        data = self.post("/consult/lastNJo", {"nbElement": 10})
        texts = {}
        for jo in data.get("containers") or []:
            jo_date = _to_date(jo.get("datePubli") or jo.get("date"))
            if not jo.get("id") or (jo_date and jo_date < start):
                continue
            cont = self.post("/consult/jorfCont", {"id": jo["id"], "pageNumber": 1, "pageSize": 100})
            for t in _walk_texts(cont, {}).values():
                t["date"] = t["date"] or jo_date
                texts.setdefault(t["id"], t)
        return texts


def read_legifrance(source, since, match_topics, keywords, norm):
    """Returns (items, used_url, error) like the other readers in collect.py.
    Items not wanted (wrong nature, no keyword match, excluded) are dropped here."""
    cid = os.environ.get("LEGIFRANCE_CLIENT_ID", "").strip()
    secret = os.environ.get("LEGIFRANCE_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        return [], None, "SKIP"
    env = os.environ.get("LEGIFRANCE_ENV", source.get("environment", "production")).strip().lower()
    client = LegifranceClient(cid, secret, env)

    always = [n.upper() for n in source.get("natures_always", ["LOI", "ORDONNANCE"])]
    if_kw = [n.upper() for n in source.get("natures_if_keyword", ["DECRET"])]
    exclude = [norm(w) for w in source.get("exclude_title_words", [])]
    ignore = [norm(p) for p in source.get("ignore_phrases", [])]
    start, end = since.date().isoformat(), dt.date.today().isoformat()

    method, texts = "search", {}
    try:
        texts = client.search_jorf(start, end, always + if_kw)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (401, 403):
            raise  # bad credentials or API not subscribed: say so, don't hide it
        texts = {}
    if not texts:
        method, texts = "jorfCont", client.recent_jo_texts(start)

    # Seville's areas (sources.json "area_keywords"). When set, EVERY text -
    # laws included - must match one of them; client-topic keywords are not used.
    areas = source.get("area_keywords") or None
    area_kw = {k: v for k, v in (areas or {}).items() if not k.startswith("_")}
    check_text_natures = [n.upper() for n in source.get("check_full_text_natures", ["LOI", "ORDONNANCE", "DECRET"])]
    max_summaries = int(source.get("max_summaries", 25))
    use_ai = summarize.available()
    cache = {}

    def full_text(tid):
        if tid not in cache:
            try:
                cache[tid] = text_parts(client.get_text(tid))
            except Exception as exc:  # one unreadable text shouldn't stop the run
                print(f"   [legifrance] could not read {tid}: {exc}")
                cache[tid] = ("", "")
        return cache[tid]

    candidates = []
    for t in texts.values():
        nature = (t["nature"] or nature_from_title(t["title"]) or "").upper()
        if nature.startswith("LOI"):
            nature_key = "LOI"
        else:
            nature_key = nature
        if nature_key not in always and nature_key not in if_kw:
            continue
        if t["date"] and t["date"] < start:
            continue
        ntitle = norm(t["title"])
        # Exclusions screen every nature when areas are set; otherwise only decrees/arrêtés.
        if (area_kw or nature_key not in always) and any(x and x in ntitle for x in exclude):
            continue
        match_text = t["title"]
        for p in ignore:
            match_text = re.sub(re.escape(p), " ", norm(match_text))
        if area_kw:
            topics = match_topics(match_text, area_kw)
            if not topics and nature_key in check_text_natures:
                # Title too vague? Check the official notice (objet, publics concernés).
                notice, _ = full_text(t["id"])
                topics = match_topics(notice, area_kw)
            if not topics:
                continue
        else:
            topics = match_topics(match_text, keywords)
            if nature_key in if_kw and nature_key not in always and not topics:
                continue
        candidates.append((t, nature, nature_key, topics))

    items, dropped = [], []
    for n, (t, nature, nature_key, topics) in enumerate(candidates):
        label = NATURE_LABEL.get(nature, nature.title() if nature else "Texte")
        feminine = nature_key in ("LOI", "ORDONNANCE", "DECISION", "CIRCULAIRE")
        summary = f"{label} {'publiée' if feminine else 'publié'} au Journal officiel"
        if t["date"]:
            d = dt.date.fromisoformat(t["date"])
            summary += f" du {d.day:02d}/{d.month:02d}/{d.year}"
        if t.get("section") and not _plain(t["section"]).startswith(("lois", "decrets", "arretes", "ordonnances")):
            summary += f" · {t['section']}"
        item = {
            "title": t["title"],
            "link": f"https://www.legifrance.gouv.fr/jorf/id/{t['id']}",
            "date": t["date"],
            "summary": summary + ".",
            "nature": nature_key,
            "matched_topics": topics,
        }
        if area_kw and n < max_summaries:
            notice, body = full_text(t["id"])
            if use_ai and body:
                try:
                    details = summarize.summarise(t["title"], t["date"], body)
                    if not details["relevant"]:
                        dropped.append(t["title"])
                        continue
                    item["details"] = {"fr": details["fr"], "en": details["en"]}
                except Exception as exc:
                    print(f"   [summary] {t['id']}: {exc}")
            if "details" not in item and notice:
                item["summary"] += " " + notice[:900]
        items.append(item)
    for title in dropped:
        print(f"   [summary] dropped as not relevant: {title[:110]}")

    # Laws first, then ordinances, then the rest; newest first within each.
    rank = {"LOI": 0, "ORDONNANCE": 1, "DECRET": 2, "ARRETE": 3}
    items.sort(key=lambda i: (rank.get(i["nature"], 9), -(int((i["date"] or "0").replace("-", "")))))
    used = (f"API Légifrance ({env}, {method}, {len(texts)} textes lus, "
            f"{len(candidates)} dans les domaines suivis, {len(dropped)} écartés par le résumé"
            f"{'' if use_ai else ', résumés IA désactivés: ANTHROPIC_API_KEY absent'})")
    return items, used, None
