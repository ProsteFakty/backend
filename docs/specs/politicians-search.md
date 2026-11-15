# Spec: search MPs ("Szukaj posła")

Status: implemented · Branch: `6-api-to-search-polititans`

## Goal

Screen "Szukaj posła": search box "Imię lub nazwisko", club chips (Wszystkie / KO / PiS / PSL-TD / …,
horizontally scrollable), alphabetical list with letter section headers. Row: initials avatar, full name,
`<club> · okręg nr <n> (<district>)`, chevron → MP profile (`GET /api/v1/politicians/{mp_id}`).

Two endpoints: the list (filtered by text + club) and the chip list.

The existing `GET /api/v1/politicians/search` (relevance-ordered, `q` required, limit) stays unchanged.

## Data source

Sejm API, term = `settings.sejm_term`, no DB tables:
- `GET /term{t}/MP` — already cached in memory for 6 h by `list_politicians()`. Gives `club` (current club id),
  `districtNum`, `districtName`, `active`.
- `GET /term{t}/clubs` — new, cached the same way (6 h). Gives `id`, `name` (full official name), `membersCount`.

Club ids from the API are not display labels and change during the term (Figma shows `Polska 2050-TD`, today the
API has `Polska2050`, `RozwojPlus`, `Centrum`, …). Chips must come from the API, never hardcoded in FE.

## Endpoint 1: list MPs

```
GET /api/v1/politicians?q=&club=&include_inactive=false&page=1&page_size=30
```

| param              | type          | default | notes                                                         |
|--------------------|---------------|---------|---------------------------------------------------------------|
| `q`                | str, 0..100   | none    | name fragment(s); empty/blank = no text filter                |
| `club`             | str           | none    | club id from endpoint 2 (e.g. `KO`); none = "Wszystkie"       |
| `include_inactive` | bool          | false   | true adds MPs whose mandate expired during the term           |
| `page`             | int ≥ 1       | 1       |                                                               |
| `page_size`        | int 1..100    | 30      |                                                               |

Paginated like the other lists (`Page[T]`); FE loads the next page on scroll. A letter section can span pages:
FE keeps grouping consecutive items by `letter` across pages. Page past the end → 200 with empty `items`.

### Matching (`q`)

Same rule as `/search` (reuse `_fold`): split `q` into words; every word must be a prefix of some word of
`full_name`; case-insensitive, Polish characters optional (`lecki` matches `Łęcki`). One letter is allowed
(`q=a` is a valid filter, unlike `/search`).

`club`: exact match on the MP's **current** club id. Unknown id → 200 with empty list (club list may change
between FE fetching chips and listing).

### Order

Always alphabetical — **last name, then first name**, Polish collation (`a ą b c ć d e ę … l ł … s ś … z ź ż`,
case-insensitive), also when `q` is set (no relevance ranking: the screen groups by letter).

### Response 200 — `Page[PoliticianListItem]`

```json
{
  "items": [
    {
      "mp_id": 1,
      "first_name": "Andrzej",
      "last_name": "Adamczyk",
      "full_name": "Andrzej Adamczyk",
      "letter": "A",
      "club": "PiS",
      "club_label": "PiS",
      "district_number": 13,
      "district_name": "Kraków",
      "active": true,
      "photo_url": "https://api.sejm.gov.pl/sejm/term10/MP/1/photo"
    }
  ],
  "page": 1, "page_size": 30, "total": 460, "pages": 16
}
```

- `letter`: section header = first letter of `last_name`, uppercased, Polish letter kept (`Ł`, `Ś`, `Ż` are their
  own sections, matching the order). FE just groups consecutive items by it.
- `club_label`: display label (see "Club labels"); `club`/`club_label` null for an MP without a club.
- `district_number`/`district_name`: FE renders `okręg nr {district_number} ({district_name})`.
- Initials (avatar) computed by FE from `first_name` + `last_name`.

### Errors

| code | when                                                     |
|------|----------------------------------------------------------|
| 422  | `q` longer than 100, invalid `page` / `page_size`        |
| 502  | Sejm API unavailable and no MP list cached yet           |

## Endpoint 2: club chips

```
GET /api/v1/politicians/clubs?include_inactive=false
```

Must be registered **before** `/{mp_id}` in the router (otherwise `clubs` hits the int path param → 422).

### Response 200 — `list[ClubOut]`

```json
[
  {"id": "KO", "label": "KO", "name": "Klub Parlamentarny Koalicja Obywatelska - …", "members_count": 155},
  {"id": "PiS", "label": "PiS", "name": "Klub Parlamentarny Prawo i Sprawiedliwość", "members_count": 147},
  {"id": "niez.", "label": "Niezrzeszeni", "name": "Posłowie niezrzeszeni", "members_count": 8}
]
```

- `members_count`: counted from the cached MP list with the same `include_inactive` rule as endpoint 1, so a
  chip's count == `total` of the list filtered by it. Clubs with 0 members are omitted.
- Order: `members_count` desc, ties by `label`; `niez.` always last.
- A club present on MPs but missing in `/clubs` (API inconsistency): still listed, `name` = `label`.
- "Wszystkie" chip is FE-only.

Errors: 502 when Sejm API is unavailable and nothing is cached (either list).

## Club labels

Static map in code, fallback for unknown ids: `id.replace("_", " ")`.

| id                | label           |
|-------------------|-----------------|
| `Polska2050`      | Polska 2050     |
| `Polska2050-TD`   | Polska 2050-TD  |
| `RozwojPlus`      | Rozwój Plus     |
| `Konfederacja_KP` | Konfederacja KP |
| `Demokracja`      | Demokracja Bezpośrednia |
| `niez.`           | Niezrzeszeni    |

(others: label = id, e.g. `KO`, `PiS`, `PSL-TD`, `Lewica`, `Razem`, `Centrum`, `Konfederacja`).

## Model changes

- `Politician`: add `district_number: int | None` (from `districtNum`). `district` stays (no breaking change).
  The Qdrant face index payload lacks it until `scripts.index_politicians` is re-run → field is optional.
- New in `app/services/politicians.py`: `list_clubs()` (cached), `polish_sort_key()`, `filter_politicians()`.
- `list_politicians()` / `list_clubs()`: on Sejm error keep serving a stale cache if there is one;
  raise only when nothing is cached (→ 502 in the router).

## Out of scope

- MP counts per chip in the UI (available via `members_count`, but not in the design).
- Club history (club changes mid-term), other terms, DB persistence of MPs.
- Changing `/search` (camera flow / other clients may use it).

## Tests

Stub `list_politicians` / `list_clubs` (as in `tests/test_politicians.py`):
- list: no params → active MPs only, sorted by last name with Polish collation (`Lis` < `Łęcki` < `Maj`),
  `letter` correct incl. `Ł`; `include_inactive=true` adds inactive
- paging: default 30, `page`/`page_size` slicing, `pages`, page past end → empty, 422 on 0 / 101
- `q`: word prefixes, without Polish chars, multi-word AND, single letter OK, blank = all, 101 chars → 422
- `club` filter exact; unknown club → empty; combined with `q`
- row shape: exact key set, `club_label` mapped, null club → null label
- clubs: counts match list totals, order (desc, `niez.` last), 0-member clubs omitted, label fallback,
  `/clubs` not shadowed by `/{mp_id}`
- Sejm down with empty cache → 502; down with cache → 200 stale
- `Politician.from_sejm` maps `districtNum`
