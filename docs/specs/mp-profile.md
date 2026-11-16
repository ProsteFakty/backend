# Spec: MP profile ("Profil posła")

Status: implemented · Branch: `8-mp-profile-api` · Issue #8

## Goal

Screen "Profil posła": initials avatar, name, club chip ("Koalicja Obywatelska"), subtitle
"Posłanka X kadencji · okręg nr 13 (Kraków)", three stat tiles (obecność %, głosów, kadencje) and a details card:
Funkcja, Kadencje ("VIII, IX, X"), Okręg ("nr 13, Kraków"), Biuro poselskie.

## Endpoint

```
GET /api/v1/politicians/{mp_id}
```

Existing endpoint, response extended (additive, no breaking change) from `Politician` to `MpProfile`.
The camera scan (`POST /recognize`) and lists keep returning the light objects: the extra data costs Sejm API calls.

### Response 200 — `MpProfile` = `Politician` +:

```json
{
  "...": "all Politician fields (mp_id, full_name, club, district, district_number, photo_url, ...)",
  "gender": "female",
  "club_label": "KO",
  "club_name": "Koalicja Obywatelska",
  "functions": ["Przewodnicząca Komisji Zdrowia", "Wiceprzewodnicząca klubu KO"],
  "terms": [8, 9, 10],
  "attendance_percent": 97.3,
  "votes_cast": 4880,
  "votings_count": 5232,
  "votes_cast_all_terms": 11873,
  "office_address": null
}
```

| field                  | source / rule                                                                                       |
|------------------------|-----------------------------------------------------------------------------------------------------|
| `gender`               | not in Sejm API → first name ends with `a` = `female`, else `male` (no exception in term 10). FE: Posłanka / Poseł |
| `club_label`           | same short label as the list (`RozwojPlus` → `Rozwój Plus`)                                         |
| `club_name`            | chip on the profile: official club name without `Klub Parlamentarny` / `Klub Poselski` / `Koło Poselskie` / `Koalicyjny Klub Parlamentarny` prefix, cut at ` - ` / ` (`; overrides `Lewica` → `Lewica`, `niez.` → `Posłowie niezrzeszeni` |
| `functions`            | `GET /term{t}/committees` (`function` + `nameGenitive`) and `GET /term{t}/clubs` members (`function` + `klubu {club_label}`); capitalized; for women `przewodniczący` → `przewodnicząca`. Order: chairs first, then deputies, then other; committees before club. Empty = no function. FE shows the first one in "Funkcja" |
| `terms`                | Sejm terms the person was an MP in, ascending, incl. current; matched in `GET /term{n}/MP` lists by first name + last name + birth date, terms 3..current (terms 1–2 are empty in the Sejm API). FE renders Roman numerals and the count |
| `attendance_percent`   | current term, `GET /term{t}/MP/{id}/votings/stats`: `sum(numVoted) / sum(numVotings) * 100`, 1 decimal |
| `votes_cast`           | current term `sum(numVoted)` — "głosów" tile                                                        |
| `votings_count`        | current term `sum(numVotings)`                                                                      |
| `votes_cast_all_terms` | `sum(numVoted)` over all `terms` (stats per term with that term's MP id)                            |
| `office_address`       | always `null` for now (see "Biuro poselskie"); FE hides the row when null                          |

"głosów" tile: open question which number the design means — `votes_cast` (this term, default) or
`votes_cast_all_terms`. Both are returned; FE picks.

Degradation: the extras are best effort. If a Sejm call for them fails: stats fields `null`
(`votes_cast_all_terms` `null` if any term failed), `functions` `[]`, a past term whose MP list failed is left
out of `terms`, `club_name` = `club_label`. Logged as warning.

Committee names can be very long (investigative committees: ~300 chars) — FE truncates.

### Errors

| code | when                                                         |
|------|--------------------------------------------------------------|
| 404  | no MP with this id in the current term                       |
| 502  | Sejm API unavailable and the current MP list is not cached   |

## Caching (in memory)

- current-term committees and clubs: 6 h (same as MP list)
- MP lists of past terms: never expire (closed terms do not change)
- votings stats: current term 6 h per MP, past terms never expire
- first profile view of an MP: ≈ 1.5–3 s (stats calls run in parallel, ≤ 8 threads); next views instant

## Biuro poselskie

Not in the Sejm API (`api.sejm.gov.pl/sejm/openapi/` has no office/address field). The address is only on
sejm.gov.pl MP pages, which are behind bot protection (Incapsula) — scraping them is not an option.
Possible sources for a follow-up task: ask Kancelaria Sejmu for an export / open-data request, or a curated
dataset maintained by us. Until then `office_address: null`.

## Out of scope

- Office address source (above), MP photo in avatar (FE uses initials), bookmark / share buttons (FE only).
- Prezydium Sejmu functions (Marszałek, wicemarszałek) — not exposed by the Sejm API.
- MPs who changed their name between terms are not matched (terms list then shorter).

## Tests

Stub Sejm calls (no network):
- gender heuristic; club name derivation + overrides
- functions: committee + club, ordering, feminization, empty
- terms matching by name + birth date (homonym with other birth date not matched)
- stats sums, attendance rounding, all-terms sum uses per-term MP ids
- degradation: stats / committees failure → nulls / `[]`, still 200
- endpoint: 404 unknown id, 502 when MP list unavailable, response keeps all `Politician` fields
