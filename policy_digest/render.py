"""Builds the YFIP legal & policy watch web page (docs/index.html) from history.json.

The page has an English/French toggle for its own text (headings, topics,
dates, buttons). News titles and summaries stay in the original French.
"""
import datetime as dt
import html

MONTHS = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
}
TOPIC_ORDER = ["Nouveaux textes au Journal officiel", "Droit des étrangers / CESEDA", "Ressortissants étrangers",
               "Droit du travail", "Sécurité sociale", "Immigration et séjour", "Santé et Assurance maladie", "Impôts", "Logement",
               "Travail et entreprise", "Famille et vie quotidienne", "Autre"]
TOPIC_EN = {
    "Nouveaux textes au Journal officiel": "New laws & decrees (Official Journal)",
    "Droit des étrangers / CESEDA": "Immigration law / CESEDA",
    "Ressortissants étrangers": "Foreign nationals",
    "Droit du travail": "Employment law",
    "Sécurité sociale": "Social security",
    "Immigration et séjour": "Immigration & residence",
    "Santé et Assurance maladie": "Health & health insurance",
    "Impôts": "Taxes",
    "Logement": "Housing",
    "Travail et entreprise": "Work & business",
    "Famille et vie quotidienne": "Family & daily life",
    "Autre": "Other",
}


# Official pages that block automatic reading (Cloudflare); shown as quick links instead.
ALSO_CHECK = [
    ("Ministère de l'Intérieur – actualités immigration", "Interior Ministry – immigration news",
     "https://www.immigration.interieur.gouv.fr/Info-ressources/Actualites/L-actu-immigration"),
    ("ANEF – démarches en ligne pour les étrangers", "ANEF – online procedures for foreign nationals",
     "https://administration-etrangers-en-france.interieur.gouv.fr/particuliers/"),
    ("ameli – actualités des assurés", "ameli – news for insured people",
     "https://www.ameli.fr/paris/assure/actualites"),
]


def e(text):
    return html.escape(text or "", quote=True)


def bi(fr, en):
    """A piece of text in both languages; CSS shows the one that's selected."""
    return f'<span lang="fr">{e(fr)}</span><span lang="en">{e(en)}</span>'


def date_bi(iso):
    d = dt.date.fromisoformat(iso)
    return bi(f"{d.day} {MONTHS['fr'][d.month - 1]} {d.year}", f"{d.day} {MONTHS['en'][d.month - 1]} {d.year}")


def topic_bi(t):
    return bi(t, TOPIC_EN.get(t, t))


LABELS = {
    "changes": ("Ce qui change", "What changes"),
    "who": ("Qui est concerné", "Who it affects"),
    "when": ("Quand", "When it applies"),
    "for_us": ("Ce que ça change pour nous", "How this affects us"),
}


def details_block(details):
    """The structured summary of a Journal officiel text, in FR and EN."""
    out = []
    for lang, i in (("fr", 0), ("en", 1)):
        d = details.get(lang) or {}
        html_ = [f'<p class="gist">{e(d.get("gist"))}</p>'] if d.get("gist") else []
        for key in ("changes", "who", "when", "for_us"):
            val = d.get(key)
            if not val:
                continue
            html_.append(f'<div class="lbl">{e(LABELS[key][i])}</div>')
            if isinstance(val, list):
                html_.append("<ul>" + "".join(f"<li>{e(v)}</li>" for v in val if v) + "</ul>")
            else:
                html_.append(f"<p>{e(val)}</p>")
        out.append(f'<div class="bl details" lang="{lang}">{"".join(html_)}</div>')
    return "".join(out)


def card(item):
    topics = "".join(f'<span class="tag">{topic_bi(t)}</span>' for t in item.get("topics", []))
    date = f' · {date_bi(item["date"])}' if item.get("date") else ""
    if item.get("details"):
        summary = details_block(item["details"])
    else:
        summary = f'<p class="sum">{e(item["summary"])}</p>' if item.get("summary") else ""
    data_topics = e("|".join(item.get("topics", [])))
    return (f'<article class="item" data-topics="{data_topics}">'
            f'<a class="title" lang="fr" href="{e(item["link"])}" target="_blank" rel="noopener">{e(item["title"])}</a>'
            f'<div class="meta">{e(item.get("source"))}{date}</div>{summary}'
            f'<div class="tags">{topics}</div></article>')


def week_block(week, open_):
    items = week["items"]
    by_topic = {}
    for it in items:
        by_topic.setdefault((it.get("topics") or ["Autre"])[0], []).append(it)
    body = []
    for topic in TOPIC_ORDER + sorted(set(by_topic) - set(TOPIC_ORDER)):
        if topic in by_topic:
            body.append(f'<h3>{topic_bi(topic)} <span class="count">{len(by_topic[topic])}</span></h3>')
            body += [card(it) for it in by_topic[topic]]
    if not items:
        body.append('<p class="empty">' + bi("Aucune nouveauté pertinente cette semaine.",
                                              "Nothing new and relevant this week.") + "</p>")
    if week.get("problems"):
        body.append('<div class="warn"><strong>' + bi("Sources non lues cette semaine :",
                                                       "Sources that couldn't be read this week:")
                    + "</strong><ul>" + "".join(f"<li>{e(p)}</li>" for p in week["problems"]) + "</ul></div>")
    label = (bi("Semaine du ", "Week of ") + date_bi(week["start"]) + bi(" au ", " to ") + date_bi(week["end"]))
    n = len(items)
    count = bi(f'{n} élément{"s" if n != 1 else ""}', f'{n} item{"s" if n != 1 else ""}')
    return (f'<details class="week"{" open" if open_ else ""}><summary><span>{label}</span>'
            f'<span class="count">{count}</span></summary>{"".join(body)}</details>')


def watch_block(watch):
    """'What we watch on Légifrance' - so the team can see exactly what is searched."""
    if not watch or not watch.get("area_keywords"):
        return ""
    areas = {k: v for k, v in watch["area_keywords"].items() if not k.startswith("_")}
    nat = {"LOI": ("lois", "laws"), "ORDONNANCE": ("ordonnances", "ordinances"),
           "DECRET": ("décrets", "decrees"), "ARRETE": ("arrêtés", "arrêtés")}
    codes = watch.get("natures_always", []) + watch.get("natures_if_keyword", [])
    nat_fr = ", ".join(nat.get(c, (c.lower(),))[0] for c in codes)
    nat_en = ", ".join(nat.get(c, (c.lower(), c.lower()))[1] for c in codes)
    rows = "".join(f'<li><strong>{topic_bi(a)}</strong> : <span class="kw">{e(", ".join(kw))}</span></li>'
                   for a, kw in areas.items())
    excl = e(", ".join(watch.get("exclude_title_words", [])))
    return ('<section class="also"><h2>' + bi("Ce que nous suivons sur Légifrance", "What we watch on Légifrance")
            + "</h2><p>" + bi("Chaque lundi : les textes publiés au Journal officiel des 8 derniers jours ("
                              + nat_fr + "), gardés seulement s'ils touchent un de ces domaines "
                              "(titre ou notice officielle) :",
                              "Every Monday: texts published in the Journal officiel over the last 8 days ("
                              + nat_en + "), kept only if they touch one of these areas "
                              "(title or official notice):")
            + f"</p><ul>{rows}</ul><p>"
            + bi("Écartés d'office (titres contenant) : ", "Always left out (titles containing): ")
            + f'<span class="kw">{excl}</span></p><p>'
            + bi("Les résumés sont rédigés par IA à partir du texte officiel, qui seul fait foi.",
                 "Summaries are written by AI from the official text, which is the only authoritative version.")
            + "</p></section>")


def render_page(history, now, watch=None):
    weeks = history.get("weeks", [])
    topics = [t for t in TOPIC_ORDER if any(t in it.get("topics", []) for w in weeks for it in w["items"])]
    chips = '<button class="chip on" data-t="">' + bi("Tout", "All") + "</button>" + "".join(
        f'<button class="chip" data-t="{e(t)}">{topic_bi(t)}</button>' for t in topics)
    blocks = "".join(week_block(w, i == 0) for i, w in enumerate(weeks)) or (
        '<p class="empty">' + bi("Première mise à jour lundi prochain.", "First update next Monday.") + "</p>")
    updated = date_bi(now.date().isoformat())
    also = ('<section class="also"><h2>' + bi("À consulter aussi", "Also check") + "</h2><p>"
            + bi("Ces sites officiels bloquent la lecture automatique : ouvrez-les directement.",
                 "These official sites block automatic reading: open them directly.") + "</p><ul>"
            + "".join(f'<li><a href="{e(u)}" target="_blank" rel="noopener">{bi(fr, en)}</a></li>'
                      for fr, en, u in ALSO_CHECK) + "</ul></section>")
    return f"""<!DOCTYPE html>
<html lang="fr" data-lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Veille juridique / Legal &amp; policy watch – Your Friend in Paris</title>
<style>
:root{{--ink:#1f2937;--quiet:#6b7280;--line:#e5e7eb;--bg:#f8fafc;--accent:#1f3a5f;--tint:#eef2f7}}
*{{box-sizing:border-box}} body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
html[data-lang="fr"] span[lang="en"], html[data-lang="en"] span[lang="fr"],
html[data-lang="fr"] .bl[lang="en"], html[data-lang="en"] .bl[lang="fr"]{{display:none}}
header{{background:var(--accent);color:#fff;padding:28px 20px}} .wrap{{max-width:860px;margin:auto}}
.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}}
h1{{margin:0;font-size:24px}} .sub{{opacity:.85;margin-top:4px;font-size:14px}}
.lang{{display:flex;border:1px solid rgba(255,255,255,.5);border-radius:999px;overflow:hidden;flex-shrink:0}}
.lang button{{background:none;border:0;color:#fff;padding:6px 14px;font-size:13px;font-weight:600;cursor:pointer}}
.lang button.on{{background:#fff;color:var(--accent)}}
main{{max-width:860px;margin:auto;padding:20px}}
.note{{font-size:13px;color:var(--quiet);margin:0 0 14px}}
.chips{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px}}
.chip{{border:1px solid var(--line);background:#fff;border-radius:999px;padding:6px 12px;font-size:13px;cursor:pointer;color:var(--ink)}}
.chip.on{{background:var(--accent);color:#fff;border-color:var(--accent)}}
.week{{background:#fff;border:1px solid var(--line);border-radius:10px;margin-bottom:14px;padding:4px 18px}}
.week summary{{cursor:pointer;display:flex;justify-content:space-between;padding:12px 0;font-weight:600}}
h3{{font-size:15px;margin:18px 0 8px;color:var(--accent)}}
.count{{font-weight:400;color:var(--quiet);font-size:13px;margin-left:6px}}
.item{{border-top:1px solid var(--line);padding:12px 0}} .item.hide{{display:none}}
.title{{color:var(--ink);font-weight:600;text-decoration:none}} .title:hover{{text-decoration:underline}}
.meta{{color:var(--quiet);font-size:13px;margin-top:2px}} .sum{{margin:6px 0 0;font-size:14px}}
.details{{margin:8px 0 0;font-size:14px;background:var(--tint);border-radius:8px;padding:10px 14px}}
.details .gist{{margin:0 0 6px;font-weight:600}} .details .lbl{{font-size:12px;font-weight:700;color:var(--accent);text-transform:uppercase;letter-spacing:.03em;margin-top:8px}}
.details p{{margin:2px 0}} .details ul{{margin:2px 0;padding-left:18px}} .kw{{color:var(--quiet);font-size:13px}}
.also + .also{{margin-top:14px}}
.tags{{margin-top:6px}} .tag{{display:inline-block;background:var(--tint);border-radius:4px;padding:1px 7px;font-size:12px;margin-right:4px;color:var(--accent)}}
.empty{{color:var(--quiet);padding:8px 0 14px}}
.warn{{background:#fff7e6;border:1px solid #f5d9a8;border-radius:8px;padding:10px 14px;margin:14px 0;font-size:13px}} .warn ul{{margin:4px 0 0;padding-left:18px}}
.also{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin-top:6px}}
.also h2{{font-size:15px;margin:0 0 4px;color:var(--accent)}} .also p{{margin:0 0 6px;font-size:13px;color:var(--quiet)}}
.also ul{{margin:0;padding-left:18px}} .also a{{color:var(--accent)}}
footer{{max-width:860px;margin:auto;padding:0 20px 30px;color:var(--quiet);font-size:12px}}
</style></head><body>
<header><div class="wrap top"><div>
<h1>{bi("Veille juridique et administrative", "Legal & policy watch")}</h1>
<div class="sub">Your Friend in Paris · {bi("mise à jour chaque lundi", "updated every Monday")} · {bi("dernière mise à jour le ", "last updated ")}{updated}</div></div>
<div class="lang" role="group" aria-label="Language / Langue"><button data-l="fr">FR</button><button data-l="en">EN</button></div>
</div></header>
<main>
<p class="note"><span lang="en">News titles are shown in the original French. Journal officiel summaries are available in English.</span></p>
<div class="chips">{chips}</div>{blocks}{watch_block(watch)}{also}</main>
<footer>{bi("Sources suivies automatiquement : Service-Public.gouv.fr et le Journal officiel (API Légifrance). Chaque titre renvoie à la source officielle, qui seule fait foi. Généré automatiquement ; vérifiez toujours la source avant de conseiller un client.",
            "Sources followed automatically: Service-Public.gouv.fr and the Journal officiel (Légifrance API). Each title links to the official source, which is the only authoritative version. Generated automatically; always check the source before advising a client.")}</footer>
<script>
(function(){{
 var root=document.documentElement, saved=null;
 try{{saved=localStorage.getItem('veille-lang')}}catch(e){{}}
 function setLang(l){{root.setAttribute('data-lang',l);root.lang=l;
  document.querySelectorAll('.lang button').forEach(function(b){{b.classList.toggle('on',b.getAttribute('data-l')===l)}});
  try{{localStorage.setItem('veille-lang',l)}}catch(e){{}}}}
 setLang(saved==='en'?'en':'fr');
 document.querySelectorAll('.lang button').forEach(function(b){{b.addEventListener('click',function(){{setLang(b.getAttribute('data-l'))}})}});
 document.querySelectorAll('.chip').forEach(function(b){{b.addEventListener('click',function(){{
  document.querySelectorAll('.chip').forEach(function(c){{c.classList.remove('on')}}); b.classList.add('on');
  var t=b.getAttribute('data-t');
  document.querySelectorAll('.item').forEach(function(i){{
   i.classList.toggle('hide', !!t && i.getAttribute('data-topics').split('|').indexOf(t)<0);}});
 }});}});
}})();
</script></body></html>
"""
