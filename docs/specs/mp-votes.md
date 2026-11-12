# Spec: how an MP voted on bills

Status: draft · Branch: `feat/mp-votes`

## Goal

Screen "Głosowania · A. Kowalczyk" (scan → MP profile → votes): list of final votes on bills
("głosowania nad całością ustaw") in the current term, newest first, each with the MP's vote and
the position of the MP's club. Filter chips: Wszystkie / Za / Przeciw / Nieobecna (+ Wstrzymał się).

## Endpoint

```
GET /api/v1/mps/{mp_id}/votes?vote=&page=1&page_size=20
```

| param       | type                                         | default | notes                         |
|-------------|----------------------------------------------|---------|-------------------------------|
| `mp_id`     | int, path                                    | —       | Sejm API MP id (term-scoped)  |
| `vote`      | `for` \| `against` \| `abstained` \| `absent` | none    | none = all ("Wszystkie")      |
| `page`      | int ≥ 1                                      | 1       |                               |
| `page_size` | int 1..100                                   | 20      |                               |

Term = `settings.sejm_term` (same as sittings).

### Response 200 — `Page[MpVote]`

```json
{
  "items": [
    {
      "sitting": 45,
      "voting_number": 112,
      "held_at": "2026-10-17T11:32:05",
      "print_number": "1702",
      "title": "Rządowy projekt ustawy o zmianie ustawy o podatku od czynności cywilnoprawnych",
      "summary": "Dwa zdania prostym językiem.",
      "source_url": "https://www.sejm.gov.pl/sejm10.nsf/PrzebiegProc.xsp?nr=1702",
      "vote": "for",
      "club": "KO",
      "club_position": "for"
    }
  ],
  "page": 1, "page_size": 20, "total": 87, "pages": 5
}
```

- `vote`: what the MP did. Sejm `YES`→`for`, `NO`→`against`, `ABSTAIN`→`abstained`, anything else
  (`ABSENT`, …)→`absent`. FE labels: Za / Przeciw / Wstrzymał się / Nieobecny(-a).
- `club`: MP's club **at the time of the voting** (an MP can change club mid-term); null if none.
- `club_position`: majority of that club's voting members, same rule as `ClubVotes.position` in the
  sitting detail: `for` | `against` | `abstained` | `split` | `absent`. Null when `club` is null.
- `title`: official bill title (short Gemini title is a later task). `summary`: null until generated.
- Order: `held_at` desc.

### Errors

| code | when                                                                                |
|------|-------------------------------------------------------------------------------------|
| 404  | no stored votes for `mp_id` **and** Sejm `GET /term{t}/MP/{mp_id}` returns 404      |
| 422  | invalid `vote`, `page`, `page_size`                                                 |
| 502  | Sejm API unavailable and nothing stored yet for the term                            |
| 503  | nothing stored for `mp_id` yet while the term backfill runs; `Retry-After: 30`      |

An MP who exists but has no final votes yet (e.g. took the mandate last week) → 200 with empty page.

## Which votings count

A voting is listed when all hold:
1. `kind == ELECTRONIC` (ON_LIST / TRADITIONAL have no per-MP YES/NO)
2. it is the vote on the whole document: `"całość projektu"` in description or `"nad całością"`
   in topic (existing `is_final` rule)
3. its bill has `document_type == "projekt ustawy"` (uchwały etc. excluded)

## Data

New tables (created by `create_all`; existing tables are **not** altered, there are no migrations):

- `mp_votes(id, voting_id → votings ON DELETE CASCADE, mp_id, club, vote)`,
  unique `(voting_id, mp_id)`, index `mp_id`. Rows only for final ELECTRONIC votings.
- `sitting_mp_votes_sync(sitting_id PK → sittings, synced_at)`: backfill marker per sitting.

## Sync

- Regular sitting sync (`_sync_votings`): `_build_voting` also creates `mp_votes` for final electronic
  votings, from the `votes` list it already downloads.
- Backfill (`ensure_mp_votes`), one lock per term:
  - ensures the sitting list (same as sittings endpoints)
  - stale sitting = started, and no marker, or marker taken before the sitting ended and older than `LIVE_TTL`
  - per batch of 10 stale sittings, 5 concurrent Sejm calls, holding the sittings' sync locks:
    - `GET /votings/{sitting}` listing → final electronic candidates (listing has topic/description)
    - detail only for candidates that are not stored, or stored without `mp_votes`
      (votings synced before this feature)
    - store voting (+club results, bill via `_ensure_bills`) or just its `mp_votes`; set markers;
      commit per batch, so progress survives a failure
  - Sejm failure mid-backfill: if anything is stored, log and serve stored data; otherwise 502.
- Who runs it:
  - app startup: background task (with its own session)
  - request: inline when ≤ 2 sittings are stale (live sitting refresh); more → starts the background
    task if not running and serves what is stored
  - `python -m scripts.backfill_mp_votes`: manual / cron, same function
- Cold backfill, term 10 (69 sittings): Sejm detail ≈ 1.7 s,
  process ≈ 1 s → measured 453 s cold (792 votings, 364k MP votes), warm no-op < 1 s; hence background.

## Out of scope

- Short bill titles (Gemini), MP profile data (lives on `feat/politician-recognition`),
  per-vote counts for chips, amendments/motions, other terms.

## Tests

- endpoint contract with stubbed service: shape, defaults, `vote` filter passthrough, 422s, 404, 502
- pure: Sejm vote → enum mapping, final-candidate filter on listing items,
  `_build_voting` creates `mp_votes` only for final electronic votings, keeps MP club per vote
