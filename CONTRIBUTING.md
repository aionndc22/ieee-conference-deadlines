# Contributing

Thanks for helping keep this list accurate! Every new call for papers, extended deadline or corrected venue helps other researchers.

You don't need to know how to code. There are two ways to contribute.

## Option 1 — Open an issue (easiest)

[Open a new issue](https://github.com/aionndc22/ieee-conference-deadlines/issues/new/choose) and pick **Add a conference** or **Update a conference**. Fill in what you know and paste the official link — a maintainer will add it.

## Option 2 — Edit the data file (pull request)

All content lives in **[`data/conferences.yml`](data/conferences.yml)**. `README.md`, the website in `docs/`, and the JSON/CSV downloads are **generated** from it, so please don't edit those by hand.

1. Open [`data/conferences.yml`](data/conferences.yml) on GitHub and click the ✏️ pencil icon (GitHub will fork the repo for you).
2. Copy an existing entry and change the values. Entries are grouped by region, but order doesn't matter — the list is sorted automatically.
3. Commit and open a pull request. An automatic check validates your entry; after merge, the README and website update themselves.

### Example entry

```yaml
- id: plans-2027                      # unique, lowercase, dashes
  acronym: PLANS 2027
  name: IEEE/ION Position, Location and Navigation Symposium
  url: https://www.ion.org/plans/      # official conference website
  organizers: IEEE AESS · Institute of Navigation (ION)
  aess: true                           # only for IEEE AESS sponsored / co-sponsored events
  city: Portland, Oregon
  venue: Hilton Portland Downtown
  country: USA
  start: 2027-04-12
  end: 2027-04-15
  format: In person                    # In person | Hybrid | Virtual | Not stated
  deadline: 2026-10-16
  deadline_type: abstract              # abstract | full paper | extended abstract | paper summary | digest
  deadline_note: Full manuscript due 1 Feb 2027.
  notes: Presentations must be given in person.
  sources:                             # at least one official link
    - https://www.ion.org/plans/call-for-abstracts.cfm
  verified: 2026-10-16                 # the day you checked the sources
```

### Field reference

| Field | Required | What to put |
|---|---|---|
| `id` | ✅ | Unique slug, e.g. `icc-2027` |
| `acronym` | ✅ | Short name shown in the table, e.g. `IEEE ICC 2027` |
| `url` | ✅ | Official conference website |
| `city` | ✅ | City (and state/province if useful) |
| `country` | ✅ | Use `USA`, `India`, `Bangladesh`, … (match existing spelling so the country pages work) |
| `format` | ✅ | `In person`, `Hybrid`, `Virtual`, or `Not stated` if the organizers haven't said |
| `sources` | ✅ | List of links that back up the dates (official pages preferred) |
| `verified` | ✅ | Date you last checked, `YYYY-MM-DD` |
| `name` | | Full conference name |
| `organizers` | | Sponsoring IEEE societies / host institution |
| `aess` | | `true` for IEEE Aerospace and Electronic Systems Society events |
| `independent` | | `true` if a college or private company runs it and IEEE Xplore inclusion is subject to IEEE approval |
| `venue` | | Hotel, campus or convention centre |
| `start`, `end` | | Conference dates, `YYYY-MM-DD` |
| `dates_text` | | Use instead of `start`/`end` when only the month is known, e.g. `July 2027` |
| `deadline` | | Main paper (or abstract) submission deadline, `YYYY-MM-DD` |
| `deadline_type` | | `abstract`, `full paper`, `extended abstract`, `paper summary` or `digest` |
| `deadline_text` | | When there's no date yet, e.g. `Not announced (usually mid-Feb)` |
| `deadline_passed` | | `true` if the deadline is over but you don't know the exact date |
| `deadline_note` | | Other key dates, extensions, or conflicting sources |
| `notes` | | Anything else useful |

Each entry needs either `start`/`end` or `dates_text`, and one of `deadline`, `deadline_text` or `deadline_passed`.

### What belongs here

- ✅ Conferences **sponsored, co-sponsored or technically co-sponsored by IEEE** (any society), or whose proceedings go to IEEE Xplore.
- ✅ Especially IEEE AESS events and conferences in Bangladesh, India and the USA — but any country is welcome.
- ❌ Predatory or look-alike events (for example WASET conferences reusing well-known acronyms).
- ❌ Conferences with no verifiable source.

When sources disagree, use the **official site** and mention the conflict in `deadline_note`.

### Updating a deadline

Just change `deadline` (and `deadline_note`, e.g. "Extended from 1 Dec 2026"), add the new source link, and bump `verified`.

Conferences disappear from the README and website automatically once they've ended — no need to delete them. A maintainer may move very old entries out occasionally.

## Previewing locally (optional)

```bash
pip install -r requirements.txt
python scripts/build.py --check   # validate data/conferences.yml
python scripts/build.py           # regenerate README.md and docs/
```

Open `docs/index.html` in a browser to see the website.

## Watchlist

[`data/watchlist.yml`](data/watchlist.yml) lists conferences we expect but that haven't published dates yet. When one announces its call for papers, move it into `conferences.yml` with full details.
