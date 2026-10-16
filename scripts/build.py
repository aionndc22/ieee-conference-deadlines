#!/usr/bin/env python3
"""Validate data/conferences.yml and data/journals.yml, then generate README.md,
JOURNALS.md and the website in docs/.

Usage:
    python scripts/build.py            # validate + regenerate everything
    python scripts/build.py --check    # validate only (used on pull requests)
    python scripts/build.py --today 2026-11-01   # pretend it is another day
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import io
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "conferences.yml"
WATCHLIST_FILE = ROOT / "data" / "watchlist.yml"
JOURNALS_FILE = ROOT / "data" / "journals.yml"
JOURNALS_MD = ROOT / "JOURNALS.md"
README_FILE = ROOT / "README.md"
DOCS = ROOT / "docs"

REPO = "aionndc22/ieee-conference-deadlines"
REPO_URL = f"https://github.com/{REPO}"
SITE_URL = "https://aionndc22.github.io/ieee-conference-deadlines"

FORMATS = ("In person", "Hybrid", "Virtual", "Not stated")
DEADLINE_TYPES = ("abstract", "full paper", "extended abstract", "paper summary", "digest")
REQUIRED = ("id", "acronym", "url", "city", "country", "format", "sources", "verified")
OPTIONAL = (
    "name", "organizers", "aess", "independent", "venue", "start", "end", "dates_text",
    "deadline", "deadline_type", "deadline_text", "deadline_passed", "deadline_note", "notes",
)

# Pages of the website: slug -> (filter, <title>, <h1>, meta description)
PAGES = {
    "": (
        lambda c: True,
        "IEEE & AESS Conference Deadlines 2026–2027 | Call for Papers Tracker",
        "IEEE & AESS conference deadlines",
        "Upcoming IEEE and IEEE AESS conferences with paper submission deadlines, venue, "
        "country, dates and format (in person / hybrid). Bangladesh, India, USA and worldwide.",
    ),
    "bangladesh": (
        lambda c: c["country"] == "Bangladesh",
        "IEEE Conferences in Bangladesh 2026–2027 | Paper Submission Deadlines",
        "IEEE conferences in Bangladesh",
        "Upcoming IEEE conferences in Bangladesh (Dhaka, Chattogram, Cox's Bazar and more) "
        "with paper submission deadlines, dates and format.",
    ),
    "india": (
        lambda c: c["country"] == "India",
        "IEEE Conferences in India 2026–2027 | Paper Submission Deadlines",
        "IEEE conferences in India",
        "Upcoming IEEE conferences in India (Bengaluru, New Delhi, Chennai and more) "
        "with paper submission deadlines, dates and format.",
    ),
    "usa": (
        lambda c: c["country"] == "USA",
        "IEEE Conferences in the USA 2026–2027 | Paper Submission Deadlines",
        "IEEE conferences in the USA",
        "Upcoming IEEE conferences in the United States with paper submission deadlines, "
        "venue, dates and format.",
    ),
    "aess": (
        lambda c: c.get("aess"),
        "IEEE AESS Conferences 2026–2027 | Aerospace, Radar & Navigation Deadlines",
        "IEEE AESS conferences",
        "IEEE Aerospace and Electronic Systems Society (AESS) conferences — radar, aerospace, "
        "navigation and information fusion — with paper submission deadlines.",
    ),
}

STATUS_ORDER = {"open": 0, "tba": 1, "closed": 2, "past": 3}

NAV = (("", "All"), ("bangladesh", "Bangladesh"), ("india", "India"), ("usa", "USA"),
       ("aess", "AESS"), ("journals", "Journals"))


def nav_html(slug: str) -> str:
    depth = "../" if slug else ""
    current = ' aria-current="page"'
    return " ".join(
        f'<a href="{depth}{s + "/" if s else ""}"{current if s == slug else ""}>{label}</a>'
        for s, label in NAV
    )


# ───────────────────────────── loading & validation ─────────────────────────────

def as_date(value, field, cid, errors):
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        errors.append(f"{cid}: '{field}' must be a YYYY-MM-DD date, got {value!r}")
        return None


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or []
    if not isinstance(data, list):
        sys.exit(f"{path}: expected a list of entries")
    return data


def validate(confs: list[dict]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for i, c in enumerate(confs):
        cid = c.get("id") or f"entry #{i + 1}"
        for key in REQUIRED:
            if c.get(key) in (None, "", []):
                errors.append(f"{cid}: missing required field '{key}'")
        for key in c:
            if key not in REQUIRED and key not in OPTIONAL:
                errors.append(f"{cid}: unknown field '{key}' (typo?)")
        if c.get("id"):
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", c["id"]):
                errors.append(f"{cid}: id must be lowercase letters, digits and dashes")
            if c["id"] in seen:
                errors.append(f"{cid}: duplicate id")
            seen.add(c["id"])
        if c.get("format") and c["format"] not in FORMATS:
            errors.append(f"{cid}: format must be one of {', '.join(FORMATS)}")
        if c.get("deadline_type") and c["deadline_type"] not in DEADLINE_TYPES:
            errors.append(f"{cid}: deadline_type must be one of {', '.join(DEADLINE_TYPES)}")
        for key in ("start", "end", "deadline", "verified"):
            c[key] = as_date(c.get(key), key, cid, errors)
        if c.get("start") and not c.get("end"):
            c["end"] = c["start"]
        if c.get("start") and c["end"] < c["start"]:
            errors.append(f"{cid}: end is before start")
        if not c.get("start") and not c.get("dates_text"):
            errors.append(f"{cid}: give start/end dates or dates_text")
        if not c.get("deadline") and not c.get("deadline_text") and not c.get("deadline_passed"):
            errors.append(f"{cid}: give deadline, deadline_text or deadline_passed: true")
        for key in ("url",):
            if c.get(key) and not str(c[key]).startswith(("https://", "http://")):
                errors.append(f"{cid}: {key} must be a full http(s) URL")
        sources = c.get("sources") or []
        if not isinstance(sources, list) or not all(
            isinstance(s, str) and s.startswith(("https://", "http://")) for s in sources
        ):
            errors.append(f"{cid}: sources must be a list of http(s) URLs")
    return errors


# ───────────────────────────── status & formatting ─────────────────────────────

def status_of(c: dict, today: dt.date) -> str:
    if c.get("end") and c["end"] < today:
        return "past"
    if c.get("deadline"):
        return "open" if c["deadline"] >= today else "closed"
    if c.get("deadline_passed"):
        return "closed"
    return "tba"


def sort_key(c: dict, today: dt.date):
    st = status_of(c, today)
    far = dt.date.max
    if st == "open":
        return (STATUS_ORDER[st], c["deadline"], c["acronym"].lower())
    return (STATUS_ORDER[st], c.get("start") or far, c["acronym"].lower())


def fmt_day(d: dt.date) -> str:
    return f"{d.day} {d:%b %Y}"


def fmt_range(c: dict) -> str:
    s, e = c.get("start"), c.get("end")
    if not s:
        return c.get("dates_text", "TBA")
    if s == e:
        return fmt_day(s)
    if s.year != e.year:
        return f"{fmt_day(s)} – {fmt_day(e)}"
    if s.month != e.month:
        return f"{s.day} {s:%b} – {e.day} {e:%b %Y}"
    return f"{s.day}–{e.day} {e:%b %Y}"


def days_left(c: dict, today: dt.date) -> int | None:
    return (c["deadline"] - today).days if c.get("deadline") else None


def urgency(c: dict, today: dt.date) -> tuple[str, str]:
    """(emoji, label) for the deadline cell."""
    st = status_of(c, today)
    if st == "open":
        n = days_left(c, today)
        label = "closes today" if n == 0 else ("1 day left" if n == 1 else f"{n} days left")
        emoji = "🔴" if n <= 7 else ("🟠" if n <= 30 else "🟢")
        return emoji, label
    if st == "closed":
        return "🔒", "closed"
    return "⏳", "not announced"


def deadline_main(c: dict) -> str:
    if c.get("deadline"):
        return fmt_day(c["deadline"])
    if c.get("deadline_passed"):
        return "Closed"
    return c.get("deadline_text", "TBA")


def place(c: dict) -> str:
    return f"{c['city']} ({c['venue']})" if c.get("venue") else c["city"]


# ───────────────────────────── README ─────────────────────────────

def md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def md_row(c: dict, today: dt.date) -> str:
    name = f"[{md(c['acronym'])}]({c['url']})"
    if c.get("aess"):
        name += " **AESS**"
    if c.get("independent"):
        name += " †"
    if c.get("name"):
        name += f"<br><sub>{md(c['name'])}</sub>"

    emoji, label = urgency(c, today)
    dl = deadline_main(c)
    if c.get("deadline"):
        dl = f"**{dl}** {emoji} {label}"
    elif not c.get("deadline_passed"):
        dl = f"{emoji} {md(dl)}"
    extra = []
    if c.get("deadline_type"):
        extra.append(c["deadline_type"].capitalize())
    if c.get("deadline_note"):
        extra.append(c["deadline_note"])
    if extra:
        dl += f"<br><sub>{md(' · '.join(extra))}</sub>"

    return (
        f"| {name} | {dl} | {md(place(c))} | {md(c['country'])} | "
        f"{md(fmt_range(c))} | {md(c['format'])} |"
    )


def md_table(rows: list[dict], today: dt.date) -> str:
    if not rows:
        return "_Nothing here right now._\n"
    head = (
        "| Conference | Paper submission deadline | Place | Country | Conference dates | Format |\n"
        "|---|---|---|---|---|---|\n"
    )
    return head + "\n".join(md_row(c, today) for c in rows) + "\n"


def build_readme(confs: list[dict], watch: list[dict], today: dt.date, journal_count: int = 0) -> str:
    live = sorted((c for c in confs if status_of(c, today) != "past"), key=lambda c: sort_key(c, today))
    by = {s: [c for c in live if status_of(c, today) == s] for s in ("open", "tba", "closed")}
    soon = [c for c in by["open"] if days_left(c, today) <= 30]

    watch_md = "\n".join(
        f"- **[{md(w['name'])}]({w['url']})** — {md(w['expected'])}" for w in watch
    )
    soon_md = (
        "\n".join(
            f"- **{fmt_day(c['deadline'])}** — [{md(c['acronym'])}]({c['url']}), "
            f"{md(c['city'])}, {md(c['country'])} ({urgency(c, today)[1]})"
            for c in soon
        )
        or "_No deadlines in the next 30 days._"
    )
    countries = sorted({c["country"] for c in live})

    return f"""<!-- ⚠️ This file is generated by scripts/build.py from data/conferences.yml — edit the YAML, not this file. -->

# IEEE & AESS Conference Deadlines 2026–2027

**A community-maintained list of upcoming IEEE and IEEE AESS conferences with their paper submission deadlines** — venue, country, conference dates and format (in person or hybrid). It covers **Bangladesh, India and the USA** in depth, plus major IEEE conferences worldwide.

🌐 **Website:** [{SITE_URL.removeprefix('https://')}]({SITE_URL}/) &nbsp;·&nbsp; 📥 Data: [JSON](docs/conferences.json) · [CSV](docs/conferences.csv) · [YAML](data/conferences.yml) &nbsp;·&nbsp; ✍️ [Add or fix a conference](CONTRIBUTING.md)

**Browse:** [Bangladesh]({SITE_URL}/bangladesh/) · [India]({SITE_URL}/india/) · [USA]({SITE_URL}/usa/) · [AESS (aerospace, radar, navigation)]({SITE_URL}/aess/) · [All]({SITE_URL}/)

📚 **Also here:** [IEEE journals — open-access model, APC 2026 and time to first decision](JOURNALS.md) ({journal_count} journals and magazines, grouped by track · [website]({SITE_URL}/journals/) · [Excel](docs/IEEE_Journals_APC.xlsx))

> **{len(by['open'])}** open calls · **{len(by['tba'])}** deadlines not announced · **{len(by['closed'])}** closed but still upcoming — across {len(countries)} countries.
> Last updated **{fmt_day(today)}**. Statuses are recalculated every day. Always confirm dates on the official site before submitting.

**Legend:** **AESS** = IEEE Aerospace and Electronic Systems Society event · † = run by a college or private organizer; papers go to IEEE Xplore only if IEEE approves · 🔴 closes within 7 days · 🟠 within 30 days · 🟢 open · ⏳ not announced · 🔒 closed

## ⏰ Closing in the next 30 days

{soon_md}

## 📝 Open calls for papers (sorted by deadline)

{md_table(by['open'], today)}
## ⏳ Deadline not announced yet

{md_table(by['tba'], today)}
## 🔒 Deadline passed — conference still upcoming

{md_table(by['closed'], today)}
## 👀 Watchlist — expected but not announced

{watch_md}

## 🤝 Contributing

Anyone can help keep this list accurate — no coding needed.

- **Spotted a new call for papers or a changed deadline?** [Open an issue]({REPO_URL}/issues/new/choose) with the link, or edit [`data/conferences.yml`]({REPO_URL}/edit/main/data/conferences.yml) directly in your browser and send a pull request.
- Every entry needs at least one **official source link**.
- README, website, JSON and CSV are regenerated automatically after a merge.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the field reference.

## ⚠️ Disclaimer

Dates are collected from official conference sites, IEEE society calendars and, where noted, third-party listings. Deadlines are often extended or changed, so **always check the official website before submitting**. Listing a conference here is not an endorsement.

## 📄 License

[MIT](LICENSE) — free to use, share and build on.

<sub>Keywords: IEEE conference deadlines, call for papers 2027, CFP, paper submission deadline, IEEE AESS, aerospace conference, radar conference, navigation conference, IEEE conferences in Bangladesh, IEEE conferences in India, IEEE conferences in USA, upcoming IEEE conferences, conference list 2026 2027.</sub>
"""


# ───────────────────────────── website ─────────────────────────────

CSS = """\
:root {
  --bg: #f7f8fa; --surface: #ffffff; --text: #1b1f24; --muted: #5b6470; --line: #e3e6ea;
  --accent: #0b5cad; --accent-soft: #e6f0fa;
  --open: #1a7f37; --open-soft: #e6f4ea; --soon: #b35900; --soon-soft: #fdf0e1;
  --urgent: #c62828; --urgent-soft: #fde8e8; --closed: #6b7280; --closed-soft: #eef0f2;
  --tba: #6f42c1; --tba-soft: #f1ebfb;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1216; --surface: #171b21; --text: #e6e9ed; --muted: #9aa4b0; --line: #2a3038;
    --accent: #6cb2ff; --accent-soft: #15283d;
    --open: #5fd38a; --open-soft: #13291c; --soon: #ffb35c; --soon-soft: #33230f;
    --urgent: #ff7b7b; --urgent-soft: #3a1717; --closed: #9aa4b0; --closed-soft: #22272e;
    --tba: #c7a6ff; --tba-soft: #261c3a;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
a { color: var(--accent); }
.wrap { max-width: 1280px; margin: 0 auto; padding: 24px 16px 48px; }
header h1 { margin: 0 0 4px; font-size: 1.7rem; line-height: 1.25; }
header p { margin: 4px 0; color: var(--muted); max-width: 72ch; }
nav.pages { display: flex; flex-wrap: wrap; gap: 6px; margin: 16px 0 12px; }
nav.pages a { padding: 6px 12px; border-radius: 999px; border: 1px solid var(--line); background: var(--surface);
  text-decoration: none; color: var(--text); font-weight: 500; }
nav.pages a[aria-current] { background: var(--accent); border-color: var(--accent); color: var(--surface); }
.stats { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 16px; }
.stat { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 8px 14px; }
.stat b { font-size: 1.25rem; margin-right: 4px; }
.filters { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: center; background: var(--surface);
  border: 1px solid var(--line); border-radius: 10px; padding: 12px; margin-bottom: 12px; }
.filters label { display: flex; gap: 6px; align-items: center; color: var(--muted); font-size: .9rem; }
.filters input[type=search], .filters select { font: inherit; color: var(--text); background: var(--bg);
  border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; }
.filters input[type=search] { min-width: 220px; flex: 1; }
.table-wrap { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; vertical-align: top; padding: 10px 12px; border-bottom: 1px solid var(--line); }
th { font-size: .8rem; text-transform: uppercase; letter-spacing: .03em; color: var(--muted);
  background: var(--surface); position: sticky; top: 0; }
tbody tr:last-child td { border-bottom: 0; }
tr.hidden { display: none; }
.conf { font-weight: 650; text-decoration: none; }
.conf:hover { text-decoration: underline; }
.sub { color: var(--muted); font-size: .82rem; margin-top: 2px; }
.dl { font-weight: 600; white-space: nowrap; }
.tag { display: inline-block; margin-left: 6px; font-size: .7rem; font-weight: 700; padding: 1px 6px;
  border-radius: 4px; border: 1px solid var(--line); color: var(--muted); vertical-align: 2px; }
.tag-aess { background: var(--accent-soft); color: var(--accent); border-color: transparent; }
.badge { display: inline-block; font-size: .75rem; font-weight: 600; padding: 1px 8px; border-radius: 999px;
  white-space: nowrap; margin-top: 2px; }
.b-open { background: var(--open-soft); color: var(--open); }
.b-open.soon { background: var(--soon-soft); color: var(--soon); }
.b-open.urgent { background: var(--urgent-soft); color: var(--urgent); }
.b-closed { background: var(--closed-soft); color: var(--closed); }
.b-tba { background: var(--tba-soft); color: var(--tba); }
.fmt { white-space: nowrap; }
.fmt-not-stated { color: var(--muted); }
.src a { font-size: .8rem; text-decoration: none; margin-right: 2px; }
.src .sub { margin-bottom: 4px; }
.empty { padding: 24px; color: var(--muted); text-align: center; }
footer { margin-top: 24px; color: var(--muted); font-size: .9rem; }
footer p { margin: 6px 0; }
@media (max-width: 820px) {
  thead { display: none; }
  table, tbody, tr, td { display: block; width: 100%; }
  tr { border-bottom: 1px solid var(--line); padding: 8px 0; }
  td { border: 0; padding: 4px 14px; display: grid; grid-template-columns: 9.5rem 1fr; gap: 8px; }
  td::before { content: attr(data-label); color: var(--muted); font-size: .78rem; font-weight: 600; }
  td[data-label="Conference"] { display: block; font-size: 1.05rem; padding-top: 8px; }
  td[data-label="Conference"]::before { content: none; }
  .filters input[type=search] { min-width: 0; flex: 1 1 100%; }
  .filters label { max-width: 100%; }
  .filters select { min-width: 0; max-width: 100%; flex: 1 1 auto; }
}
"""

ATTENDANCE = {
    "In person": "https://schema.org/OfflineEventAttendanceMode",
    "Hybrid": "https://schema.org/MixedEventAttendanceMode",
    "Virtual": "https://schema.org/OnlineEventAttendanceMode",
}


def h(text) -> str:
    return html.escape(str(text), quote=True)


def json_ld(rows: list[dict], today: dt.date) -> str:
    events = []
    for c in rows:
        if not c.get("start"):
            continue
        desc = f"Paper submission deadline: {deadline_main(c)}"
        if c.get("deadline_type") and c.get("deadline"):
            desc += f" ({c['deadline_type']})"
        desc += f". Format: {c['format']}."
        ev = {
            "@type": "Event",
            "name": f"{c['acronym']}" + (f" — {c['name']}" if c.get("name") else ""),
            "startDate": c["start"].isoformat(),
            "endDate": c["end"].isoformat(),
            "eventStatus": "https://schema.org/EventScheduled",
            "url": c["url"],
            "description": desc,
            "location": {
                "@type": "Place",
                "name": c.get("venue") or c["city"],
                "address": {
                    "@type": "PostalAddress",
                    "addressLocality": c["city"],
                    "addressCountry": c["country"],
                },
            },
        }
        if c["format"] in ATTENDANCE:
            ev["eventAttendanceMode"] = ATTENDANCE[c["format"]]
        if c.get("organizers"):
            ev["organizer"] = {"@type": "Organization", "name": c["organizers"]}
        events.append(ev)
    payload = {"@context": "https://schema.org", "@graph": events}
    return json.dumps(payload, ensure_ascii=False, indent=1).replace("</", "<\\/")


def html_row(c: dict, today: dt.date) -> str:
    st = status_of(c, today)
    emoji, label = urgency(c, today)
    tags = ""
    if c.get("aess"):
        tags += '<span class="tag tag-aess" title="IEEE Aerospace and Electronic Systems Society">AESS</span>'
    if c.get("independent"):
        tags += ('<span class="tag" title="Run by a college or private organizer; '
                 'IEEE Xplore inclusion needs IEEE approval">†</span>')
    sub = f'<div class="sub">{h(c["name"])}</div>' if c.get("name") else ""
    org = f'<div class="sub">{h(c["organizers"])}</div>' if c.get("organizers") else ""

    dl_extra = []
    if c.get("deadline_type"):
        dl_extra.append(c["deadline_type"].capitalize())
    if c.get("deadline_note"):
        dl_extra.append(c["deadline_note"])
    dl_sub = f'<div class="sub">{h(" · ".join(dl_extra))}</div>' if dl_extra else ""
    notes = f'<div class="sub">{h(c["notes"])}</div>' if c.get("notes") else ""
    srcs = " ".join(
        f'<a href="{h(s)}" rel="nofollow noopener" target="_blank">[{i + 1}]</a>'
        for i, s in enumerate(c["sources"])
    )
    venue = f'<div class="sub">{h(c["venue"])}</div>' if c.get("venue") else ""
    search = " ".join(
        str(c.get(k, "")) for k in ("acronym", "name", "organizers", "city", "venue", "country")
    ).lower()

    return f"""<tr data-id="{h(c['id'])}" data-status="{st}" data-deadline="{c['deadline'].isoformat() if c.get('deadline') else ''}" data-passed="{'1' if c.get('deadline_passed') else ''}" data-start="{c['start'].isoformat() if c.get('start') else ''}" data-end="{c['end'].isoformat() if c.get('end') else ''}" data-country="{h(c['country'])}" data-format="{h(c['format'])}" data-aess="{'1' if c.get('aess') else ''}" data-search="{h(search)}">
<td data-label="Conference"><div><a class="conf" href="{h(c['url'])}" rel="noopener" target="_blank">{h(c['acronym'])}</a>{tags}{sub}{org}</div></td>
<td data-label="Paper submission deadline"><div><span class="dl">{h(deadline_main(c))}</span> <span class="badge b-{st}" data-badge>{emoji} {h(label)}</span>{dl_sub}</div></td>
<td data-label="Place"><div>{h(c['city'])}{venue}</div></td>
<td data-label="Country"><div>{h(c['country'])}</div></td>
<td data-label="Conference dates"><div>{h(fmt_range(c))}</div></td>
<td data-label="Format"><div><span class="fmt fmt-{h(c['format'].lower().replace(' ', '-'))}">{h(c['format'])}</span></div></td>
<td data-label="Notes &amp; sources" class="src"><div>{notes}{srcs}</div></td>
</tr>"""


def build_page(slug: str, confs: list[dict], today: dt.date) -> str:
    keep, title, h1, desc = PAGES[slug]
    rows = sorted(
        (c for c in confs if keep(c) and status_of(c, today) != "past"),
        key=lambda c: sort_key(c, today),
    )
    depth = "../" if slug else ""
    canonical = f"{SITE_URL}/{slug + '/' if slug else ''}"
    nav = nav_html(slug)
    countries = sorted({c["country"] for c in rows})
    country_opts = "".join(f'<option value="{h(x)}">{h(x)}</option>' for x in countries)
    country_filter = (
        f'<label>Country <select id="f-country"><option value="">All</option>{country_opts}</select></label>'
        if len(countries) > 1 else ""
    )
    counts = {s: sum(1 for c in rows if status_of(c, today) == s) for s in ("open", "tba", "closed")}
    body_rows = "\n".join(html_row(c, today) for c in rows)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)}</title>
<meta name="description" content="{h(desc)}">
<meta name="keywords" content="IEEE conference deadlines, call for papers, CFP 2027, paper submission deadline, IEEE AESS, aerospace, radar, navigation, IEEE conferences Bangladesh, IEEE conferences India, IEEE conferences USA">
<link rel="canonical" href="{h(canonical)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{h(title)}">
<meta property="og:description" content="{h(desc)}">
<meta property="og:url" content="{h(canonical)}">
<meta name="twitter:card" content="summary">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>📅</text></svg>">
<script type="application/ld+json">
{json_ld(rows, today)}
</script>
<style>
{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
<h1>{h(h1)} <span style="color:var(--muted);font-weight:500">2026–2027</span></h1>
<p>{h(desc)}</p>
<p>Community-maintained on <a href="{REPO_URL}">GitHub</a> — <a href="{REPO_URL}/blob/main/CONTRIBUTING.md">add or fix a conference</a>.
Last updated <time datetime="{today.isoformat()}">{fmt_day(today)}</time>. Always confirm dates on the official site.</p>
</header>
<nav class="pages" aria-label="Pages">{nav}</nav>
<div class="stats">
<div class="stat"><b id="n-open">{counts['open']}</b> open calls</div>
<div class="stat"><b id="n-tba">{counts['tba']}</b> not announced</div>
<div class="stat"><b id="n-closed">{counts['closed']}</b> closed, still upcoming</div>
</div>
<div class="filters" role="search">
<input type="search" id="f-q" placeholder="Search conference, city, society…" aria-label="Search">
{country_filter}
<label>Status <select id="f-status"><option value="">All</option><option value="open">Open</option><option value="tba">Not announced</option><option value="closed">Closed</option></select></label>
<label>Format <select id="f-format"><option value="">All</option>{''.join(f'<option>{x}</option>' for x in FORMATS)}</select></label>
<label><input type="checkbox" id="f-aess"> AESS only</label>
</div>
<div class="table-wrap">
<table>
<thead><tr><th>Conference</th><th>Paper submission deadline</th><th>Place</th><th>Country</th><th>Conference dates</th><th>Format</th><th>Notes &amp; sources</th></tr></thead>
<tbody id="rows">
{body_rows}
</tbody>
</table>
<div class="empty" id="empty" hidden>No conferences match these filters.</div>
</div>
<footer>
<p><b>AESS</b> = IEEE Aerospace and Electronic Systems Society event. <b>†</b> = run by a college or private organizer; papers go to IEEE Xplore only if IEEE approves.</p>
<p>Looking for a journal instead? See <a href="{depth}journals/">IEEE journals — APC &amp; decision time</a>.</p>
<p>Download the data: <a href="{depth}conferences.json">JSON</a> · <a href="{depth}conferences.csv">CSV</a> · <a href="{REPO_URL}/blob/main/data/conferences.yml">YAML</a>. Licensed MIT. Not affiliated with IEEE.</p>
</footer>
</div>
<script>
(function () {{
  var DAY = 86400000;
  var now = new Date();
  var today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  var t = function (s) {{ return s ? Date.parse(s + "T00:00:00Z") : null; }};
  var order = {{ open: 0, tba: 1, closed: 2, past: 3 }};
  var tbody = document.getElementById("rows");
  var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr[data-id]"));

  // Recalculate statuses in the browser so the page stays right between rebuilds.
  rows.forEach(function (r) {{
    var dl = t(r.dataset.deadline), end = t(r.dataset.end), st;
    if (end !== null && end < today) st = "past";
    else if (dl !== null) st = dl >= today ? "open" : "closed";
    else st = r.dataset.passed ? "closed" : "tba";
    r.dataset.status = st;
    var b = r.querySelector("[data-badge]");
    b.className = "badge b-" + st;
    if (st === "open") {{
      var n = Math.round((dl - today) / DAY);
      b.textContent = (n <= 7 ? "🔴 " : n <= 30 ? "🟠 " : "🟢 ") +
        (n === 0 ? "closes today" : n === 1 ? "1 day left" : n + " days left");
      if (n <= 7) b.classList.add("urgent"); else if (n <= 30) b.classList.add("soon");
    }} else if (st === "closed") b.textContent = "🔒 closed";
    else if (st === "tba") b.textContent = "⏳ not announced";
  }});
  rows.sort(function (a, b) {{
    var d = order[a.dataset.status] - order[b.dataset.status];
    if (d) return d;
    var ka = a.dataset.status === "open" ? a.dataset.deadline : (a.dataset.start || "9999");
    var kb = b.dataset.status === "open" ? b.dataset.deadline : (b.dataset.start || "9999");
    return ka < kb ? -1 : ka > kb ? 1 : 0;
  }});
  rows.forEach(function (r) {{ tbody.appendChild(r); }});

  var q = document.getElementById("f-q"), fc = document.getElementById("f-country"),
      fs = document.getElementById("f-status"), ff = document.getElementById("f-format"),
      fa = document.getElementById("f-aess"), empty = document.getElementById("empty");
  function apply() {{
    var text = q.value.trim().toLowerCase(), shown = 0, counts = {{ open: 0, tba: 0, closed: 0 }};
    rows.forEach(function (r) {{
      var ok = r.dataset.status !== "past" &&
        (!text || r.dataset.search.indexOf(text) !== -1) &&
        (!fc || !fc.value || r.dataset.country === fc.value) &&
        (!fs.value || r.dataset.status === fs.value) &&
        (!ff.value || r.dataset.format === ff.value) &&
        (!fa.checked || r.dataset.aess);
      r.classList.toggle("hidden", !ok);
      if (ok) {{ shown++; counts[r.dataset.status]++; }}
    }});
    ["open", "tba", "closed"].forEach(function (k) {{ document.getElementById("n-" + k).textContent = counts[k]; }});
    empty.hidden = shown > 0;
  }}
  [q, fc, fs, ff, fa].forEach(function (el) {{ if (el) el.addEventListener("input", apply); }});
  apply();
}})();
</script>
</body>
</html>
"""


# ───────────────────────────── data exports ─────────────────────────────

def export_record(c: dict, today: dt.date) -> dict:
    out = {}
    for key in REQUIRED + OPTIONAL:
        v = c.get(key)
        if v is None or v == "":
            continue
        out[key] = v.isoformat() if isinstance(v, dt.date) else v
    out["status"] = status_of(c, today)
    return out


def build_csv(records: list[dict]) -> str:
    cols = ["id", "acronym", "name", "status", "deadline", "deadline_type", "deadline_text",
            "deadline_note", "city", "venue", "country", "start", "end", "dates_text", "format",
            "aess", "independent", "organizers", "url", "notes", "sources", "verified"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in records:
        row = dict(r)
        row["sources"] = " ".join(r.get("sources", []))
        w.writerow(row)
    return buf.getvalue()


def build_sitemap(today: dt.date) -> str:
    urls = "\n".join(
        f"  <url><loc>{SITE_URL}/{slug + '/' if slug else ''}</loc><lastmod>{today.isoformat()}</lastmod>"
        f"<changefreq>daily</changefreq></url>"
        for slug in [*PAGES, "journals"]
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n"
    )


# ───────────────────────────── journals ─────────────────────────────

J_REQUIRED = ("acronym", "name", "track", "type", "oa_model")
J_OPTIONAL = ("aess", "apc", "decision_time", "decision_source", "decision_url",
              "model_unconfirmed", "notes", "url")


def load_journals() -> tuple[dict, list[dict]]:
    with JOURNALS_FILE.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("pricing") or {}, data.get("journals") or []


def validate_journals(pricing: dict, journals: list[dict]) -> list[str]:
    errors: list[str] = []
    models = pricing.get("models") or {}
    if not models:
        errors.append("journals.yml: pricing.models is missing")
    for m, spec in models.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("apc"), (int, float)):
            errors.append(f"journals.yml: pricing.models.{m} needs a numeric apc")
    for key in ("ieee_member", "society_member"):
        if not isinstance((pricing.get("discounts") or {}).get(key), (int, float)):
            errors.append(f"journals.yml: pricing.discounts.{key} must be a number like 0.2")
    seen: set[str] = set()
    for i, j in enumerate(journals):
        jid = j.get("acronym") or f"journal #{i + 1}"
        for key in J_REQUIRED:
            if not j.get(key):
                errors.append(f"{jid}: missing required field '{key}'")
        for key in j:
            if key not in J_REQUIRED and key not in J_OPTIONAL:
                errors.append(f"{jid}: unknown field '{key}' (typo?)")
        if j.get("acronym"):
            if j["acronym"] in seen:
                errors.append(f"{jid}: duplicate acronym")
            seen.add(j["acronym"])
        if j.get("oa_model") and j["oa_model"] not in models:
            errors.append(f"{jid}: oa_model must be one of {', '.join(models)}")
        if "apc" in j and not isinstance(j["apc"], (int, float)):
            errors.append(f"{jid}: apc must be a number (US$)")
        for key in ("decision_url", "url"):
            if j.get(key) and not str(j[key]).startswith(("https://", "http://")):
                errors.append(f"{jid}: {key} must be a full http(s) URL")
        if j.get("decision_time") and not j.get("decision_source"):
            errors.append(f"{jid}: decision_time needs a decision_source")
    return errors


def j_apc(j: dict, pricing: dict) -> int:
    return int(j.get("apc", pricing["models"][j["oa_model"]]["apc"]))


def j_member(j: dict, pricing: dict) -> int:
    return round(j_apc(j, pricing) * (1 - pricing["discounts"]["society_member"]))


def money(v: int) -> str:
    return f"${v:,}"


def anchor(text: str) -> str:
    """GitHub-style heading anchor."""
    return re.sub(r"[^a-z0-9 -]", "", text.lower()).replace(" ", "-")


def j_tracks(journals: list[dict]) -> list[str]:
    return list(dict.fromkeys(j["track"] for j in journals))


def j_note(j: dict) -> str:
    parts = []
    if j.get("model_unconfirmed"):
        parts.append("⚠️ Open-access model not confirmed for 2026 — check the journal's author page.")
    if j.get("notes"):
        parts.append(j["notes"])
    return " ".join(parts)


def build_journals_md(pricing: dict, journals: list[dict]) -> str:
    year, models, disc = pricing["year"], pricing["models"], pricing["discounts"]
    regular = [j for j in journals if j["type"] != "Magazine"]
    mags = [j for j in journals if j["type"] == "Magazine"]
    tracks = j_tracks(regular)
    with_time = sum(1 for j in regular if j.get("decision_time"))

    rules = "\n".join(
        f"| **{md(m)}** | {money(spec['apc'])} | {money(round(spec['apc'] * (1 - disc['society_member'])))} | "
        f"{md(spec.get('without_oa', ''))} | {md(spec.get('meaning', ''))} |"
        for m, spec in models.items()
    )
    toc = "\n".join(
        f"- [{t}](#{anchor(t)}) ({sum(1 for j in regular if j['track'] == t)})" for t in tracks
    )

    def row(j: dict, with_track: bool = False) -> str:
        name = f"**{md(j['acronym'])}**"
        if j.get("aess"):
            name += " `AESS`"
        name += f"<br><sub>{md(j['name'])}</sub>"
        note = j_note(j)
        if note:
            name += f"<br><sub>{md(note)}</sub>"
        if j.get("decision_time"):
            dt_cell = f"[{md(j['decision_time'])}]({j['decision_url']})" if j.get("decision_url") else md(j["decision_time"])
            dt_cell += f"<br><sub>{md(j['decision_source'])}</sub>"
        else:
            dt_cell = "Not published"
        cells = [name]
        if with_track:
            cells.append(md(j["track"]))
        cells += [md(j["type"]), md(j["oa_model"]), money(j_apc(j, pricing)), money(j_member(j, pricing)), dt_cell]
        return "| " + " | ".join(cells) + " |"

    head = ("| Journal | Type | Open-access model | APC {y} | Society member | Time to first decision |\n"
            "|---|---|---|---|---|---|\n").format(y=year)
    sections = "\n".join(
        f"## {t}\n\n{head}" + "\n".join(row(j) for j in regular if j["track"] == t) + "\n"
        for t in tracks
    )
    mag_head = ("| Magazine | Track | Type | Open-access model | Optional OA fee {y} | Society member | Time to first decision |\n"
                "|---|---|---|---|---|---|---|\n").format(y=year)
    mag_rows = "\n".join(row(j, with_track=True) for j in mags)
    sources = "\n".join(f"- {s}" for s in pricing.get("sources", []))

    return f"""<!-- ⚠️ Generated by scripts/build.py from data/journals.yml — edit the YAML, not this file. -->

# IEEE Journals: Open-Access Model, APC {year} and Time to First Decision

**Every main IEEE transactions, journal and letters title — {len(regular)} journals plus {len(mags)} magazines — grouped by research track**, with its publishing model (hybrid or fully open access), the {year} article processing charge (APC), the price after the society-member discount, and the average time to first decision where one has been published.

🌐 [Searchable website]({SITE_URL}/journals/) &nbsp;·&nbsp; 📥 [Excel](docs/IEEE_Journals_APC.xlsx) · [CSV](docs/journals.csv) · [JSON](docs/journals.json) · [YAML](data/journals.yml) &nbsp;·&nbsp; ✍️ [Add or fix a journal](CONTRIBUTING.md#journals) &nbsp;·&nbsp; 📅 [Conference deadlines](README.md)

> Prices are IEEE's {year} list prices, last checked **{pricing.get('verified', '')}**. Only **{with_time} of {len(regular)}** journals have a published time to first decision — help fill in the rest!

## How IEEE charges in {year}

| Model | APC | Society member ({int(disc['society_member'] * 100)}% off) | If you don't choose open access | What it means |
|---|---|---|---|---|
{rules}

- **Discounts:** {int(disc['ieee_member'] * 100)}% for IEEE members or {int(disc['society_member'] * 100)}% for members of the journal's sponsoring society (they don't stack; students excluded). Authors from low- and lower-middle-income countries get reduced fees, and a full waiver in fully open-access journals when every author is from a low-income country.
- **Overlength pages:** many hybrid transactions charge per page beyond a limit (e.g. TGRS: US$230/page over 10 pages).
- **Exceptions:** a few titles have their own prices. Check IEEE's official per-journal APC list before budgeting.
- **Decision times** come from IEEE society pages (Official), named third-party guides (Third-party) or author reports (Crowd-sourced). Treat the last two as rough.

## Tracks

{toc}
- [Magazines](#magazines) ({len(mags)})

{sections}
## Magazines

Most IEEE magazine articles are invited; an optional open-access fee applies to most titles.

{mag_head}{mag_rows}

## Contributing

Know a journal's current APC, its open-access model, or its official time to first decision? Edit [`data/journals.yml`]({REPO_URL}/edit/main/data/journals.yml) or [open an issue]({REPO_URL}/issues/new/choose) with a source link. See [CONTRIBUTING.md](CONTRIBUTING.md#journals).

## Sources and disclaimer

{sources}

Compiled from IEEE Open, IEEE society author pages, SciRev and Manusights. Prices and review times change; always confirm on the journal's own page before submitting. Not affiliated with IEEE.

<sub>Keywords: IEEE journal APC 2026, IEEE article processing charge, IEEE hybrid journal, IEEE open access journal list, IEEE transactions review time, time to first decision, IEEE Access APC, IEEE TAES APC.</sub>
"""


def build_journals_page(pricing: dict, journals: list[dict]) -> str:
    year, models, disc = pricing["year"], pricing["models"], pricing["discounts"]
    title = f"IEEE Journals APC {year}: Open-Access Model & Time to First Decision"
    desc = (f"All main IEEE journals and magazines grouped by track, with hybrid or fully open-access model, "
            f"{year} APC, society-member price and average time to first decision.")
    canonical = f"{SITE_URL}/journals/"
    tracks = j_tracks(journals)
    track_opts = "".join(f'<option value="{h(t)}">{h(t)}</option>' for t in tracks)
    model_opts = "".join(f'<option value="{h(m)}">{h(m)}</option>' for m in models)
    rules = "".join(
        f'<div class="stat"><b>{money(spec["apc"])}</b> {h(m)}</div>' for m, spec in models.items()
    )

    rows = []
    for j in journals:
        tags = '<span class="tag tag-aess" title="IEEE Aerospace and Electronic Systems Society">AESS</span>' if j.get("aess") else ""
        note = j_note(j)
        note_html = f'<div class="sub">{h(note)}</div>' if note else ""
        if j.get("decision_time"):
            t = h(j["decision_time"])
            if j.get("decision_url"):
                t = f'<a href="{h(j["decision_url"])}" rel="nofollow noopener" target="_blank">{t}</a>'
            dt_html = f'{t}<div class="sub">{h(j["decision_source"])}</div>'
        else:
            dt_html = '<span class="fmt-not-stated">Not published</span>'
        model_cls = "b-open" if j["oa_model"] == "Fully open access" else "b-tba"
        without = models[j["oa_model"]].get("without_oa", "")
        search = " ".join(str(j.get(k, "")) for k in ("acronym", "name", "track", "type", "oa_model")).lower()
        rows.append(f"""<tr data-id="{h(j['acronym'])}" data-track="{h(j['track'])}" data-model="{h(j['oa_model'])}" data-aess="{'1' if j.get('aess') else ''}" data-time="{'1' if j.get('decision_time') else ''}" data-search="{h(search)}">
<td data-label="Journal"><div><span class="conf">{h(j['acronym'])}</span>{tags}<div class="sub">{h(j['name'])}</div>{note_html}</div></td>
<td data-label="Track"><div>{h(j['track'])}</div></td>
<td data-label="Type"><div>{h(j['type'])}</div></td>
<td data-label="Open-access model"><div><span class="badge {model_cls}">{h(j['oa_model'])}</span></div></td>
<td data-label="APC {year}"><div><span class="dl">{money(j_apc(j, pricing))}</span><div class="sub">Society member: {money(j_member(j, pricing))}</div></div></td>
<td data-label="Without open access"><div>{h(without)}</div></td>
<td data-label="Time to first decision"><div>{dt_html}</div></td>
</tr>""")

    dataset = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": title, "description": desc, "url": canonical,
        "license": "https://opensource.org/licenses/MIT",
        "creator": {"@type": "Person", "name": "MD ANWARUL ISLAM AION", "url": "https://github.com/aionndc22"},
        "isAccessibleForFree": True,
        "keywords": ["IEEE", "APC", "article processing charge", "open access", "journal review time"],
        "distribution": [
            {"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": f"{SITE_URL}/journals.csv"},
            {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": f"{SITE_URL}/journals.json"},
        ],
    }

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)}</title>
<meta name="description" content="{h(desc)}">
<meta name="keywords" content="IEEE journal APC {year}, IEEE article processing charge, IEEE hybrid journals, IEEE open access journals list, IEEE review time, time to first decision, IEEE Access APC">
<link rel="canonical" href="{h(canonical)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{h(title)}">
<meta property="og:description" content="{h(desc)}">
<meta property="og:url" content="{h(canonical)}">
<meta name="twitter:card" content="summary">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>📚</text></svg>">
<script type="application/ld+json">
{json.dumps(dataset, ensure_ascii=False, indent=1).replace("</", "<\\/")}
</script>
<style>
{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
<h1>IEEE journals <span style="color:var(--muted);font-weight:500">APC {year} &amp; decision time</span></h1>
<p>{h(desc)}</p>
<p>Prices last checked <time datetime="{h(pricing.get('verified', ''))}">{h(pricing.get('verified', ''))}</time>. Society members get {int(disc['society_member'] * 100)}% off, IEEE members {int(disc['ieee_member'] * 100)}%. Hybrid journals are free to publish in unless you choose open access. Community-maintained on <a href="{REPO_URL}">GitHub</a> — <a href="{REPO_URL}/blob/main/CONTRIBUTING.md#journals">add or fix a journal</a>.</p>
</header>
<nav class="pages" aria-label="Pages">{nav_html("journals")}</nav>
<div class="stats">{rules}<div class="stat"><b id="n-shown">{len(journals)}</b> shown</div></div>
<div class="filters" role="search">
<input type="search" id="f-q" placeholder="Search journal name or acronym…" aria-label="Search">
<label>Track <select id="f-track"><option value="">All</option>{track_opts}</select></label>
<label>Model <select id="f-model"><option value="">All</option>{model_opts}</select></label>
<label><input type="checkbox" id="f-time"> Has decision time</label>
<label><input type="checkbox" id="f-aess"> AESS only</label>
</div>
<div class="table-wrap">
<table>
<thead><tr><th>Journal</th><th>Track</th><th>Type</th><th>Open-access model</th><th>APC {year}</th><th>Without open access</th><th>Time to first decision</th></tr></thead>
<tbody id="rows">
{chr(10).join(rows)}
</tbody>
</table>
<div class="empty" id="empty" hidden>No journals match these filters.</div>
</div>
<footer>
<p>Decision times come from IEEE society pages (Official), named third-party guides or author reports (Crowd-sourced) — treat the last two as rough. Some titles have their own APC; check IEEE's official per-journal list before budgeting.</p>
<p>Download: <a href="../IEEE_Journals_APC.xlsx">Excel</a> · <a href="../journals.csv">CSV</a> · <a href="../journals.json">JSON</a> · <a href="{REPO_URL}/blob/main/data/journals.yml">YAML</a>. Licensed MIT. Not affiliated with IEEE.</p>
</footer>
</div>
<script>
(function () {{
  var rows = Array.prototype.slice.call(document.querySelectorAll("#rows tr[data-id]"));
  var q = document.getElementById("f-q"), ft = document.getElementById("f-track"),
      fm = document.getElementById("f-model"), fd = document.getElementById("f-time"),
      fa = document.getElementById("f-aess"), empty = document.getElementById("empty"),
      shownEl = document.getElementById("n-shown");
  function apply() {{
    var text = q.value.trim().toLowerCase(), shown = 0;
    rows.forEach(function (r) {{
      var ok = (!text || r.dataset.search.indexOf(text) !== -1) &&
        (!ft.value || r.dataset.track === ft.value) &&
        (!fm.value || r.dataset.model === fm.value) &&
        (!fd.checked || r.dataset.time) && (!fa.checked || r.dataset.aess);
      r.classList.toggle("hidden", !ok);
      if (ok) shown++;
    }});
    shownEl.textContent = shown;
    empty.hidden = shown > 0;
  }}
  [q, ft, fm, fd, fa].forEach(function (el) {{ el.addEventListener("input", apply); }});
  apply();
}})();
</script>
</body>
</html>
"""


def journal_records(pricing: dict, journals: list[dict]) -> list[dict]:
    out = []
    for j in journals:
        rec = {k: j[k] for k in J_REQUIRED + J_OPTIONAL if k in j}
        rec["apc_usd"] = j_apc(j, pricing)
        rec["apc_society_member_usd"] = j_member(j, pricing)
        rec["without_oa"] = pricing["models"][j["oa_model"]].get("without_oa", "")
        out.append(rec)
    return out


def build_journals_csv(records: list[dict]) -> str:
    cols = ["acronym", "name", "track", "type", "oa_model", "apc_usd", "apc_society_member_usd",
            "without_oa", "decision_time", "decision_source", "decision_url", "aess",
            "model_unconfirmed", "notes", "url"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(records)
    return buf.getvalue()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="validate only, do not write files")
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.datetime.now(dt.timezone.utc).date())
    args = ap.parse_args()

    confs = load(DATA_FILE)
    watch = load(WATCHLIST_FILE) if WATCHLIST_FILE.exists() else []
    errors = validate(confs)
    for i, w in enumerate(watch):
        for key in ("name", "expected", "url"):
            if not w.get(key):
                errors.append(f"watchlist #{i + 1}: missing '{key}'")
    pricing, journals = load_journals()
    errors += validate_journals(pricing, journals)
    if errors:
        print("Validation failed:", *errors, sep="\n  - ", file=sys.stderr)
        return 1
    print(f"OK: {len(confs)} conferences, {len(watch)} watchlist entries, {len(journals)} journals")
    if args.check:
        return 0

    today = args.today
    write(README_FILE, build_readme(confs, watch, today, journal_count=len(journals)))
    write(JOURNALS_MD, build_journals_md(pricing, journals))
    write(DOCS / "journals" / "index.html", build_journals_page(pricing, journals))
    jrecords = journal_records(pricing, journals)
    write(DOCS / "journals.json", json.dumps(
        {"pricing": pricing, "source": REPO_URL, "journals": jrecords}, ensure_ascii=False, indent=2) + "\n")
    write(DOCS / "journals.csv", build_journals_csv(jrecords))
    from journals_xlsx import build_xlsx  # needs openpyxl
    build_xlsx(pricing, journals, DOCS / "IEEE_Journals_APC.xlsx")
    for slug in PAGES:
        write(DOCS / slug / "index.html" if slug else DOCS / "index.html", build_page(slug, confs, today))
    records = [export_record(c, today) for c in sorted(confs, key=lambda c: sort_key(c, today))]
    write(DOCS / "conferences.json", json.dumps(
        {"updated": today.isoformat(), "source": REPO_URL, "conferences": records},
        ensure_ascii=False, indent=2) + "\n")
    write(DOCS / "conferences.csv", build_csv(records))
    write(DOCS / "sitemap.xml", build_sitemap(today))
    write(DOCS / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")
    write(DOCS / ".nojekyll", "")
    print(f"Wrote README.md, JOURNALS.md and docs/ for {today.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
