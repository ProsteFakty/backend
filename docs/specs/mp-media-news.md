# Spec: MP in the media ("Ostatnio w mediach")

Status: implemented (API only) · Branch: `politician-hot-take`

## Goal

Profile screen ("Profil posła"), from design review:

- **Remove** the "Jak głosował(a) · ostatnie" card (2 latest votes). Keep the "Zobacz wszystkie głosowania"
  button → votes screen (`GET /api/v1/mps/{mp_id}/votes`, unchanged).
- **Add** a section "Ostatnio w mediach" (working name; design said "Ostatnie gorące wydarzenia"):
  3–5 short items about what the media wrote about the MP in the last 30 days — statements, controversies,
  scandals, viral moments, also tabloids (Pudelek etc.). Each item: one-line headline, 1–2 neutral
  sentences, date, source links.

Data comes from Claude Haiku 5.5 with the web search tool, cached in Postgres, so a profile view almost
never pays for a call. Spending is capped at $5 per month.

FE-only changes (no backend work): hide the votes card, keep the button, render the new section,
load it **separately** from the profile (it can be slow on a cold cache).

## Endpoint

```
GET /api/v1/politicians/{mp_id}/news
```

No query params. Term = `settings.sejm_term`.

### Response 200 — `MpNews`

```json
{
  "mp_id": 123,
  "status": "ok",
  "generated_at": "2026-11-26T09:14:02Z",
  "items": [
    {
      "headline": "Kowalska o podatku PCC w TVN24",
      "summary": "Posłanka skrytykowała w programie projekt zmian w PCC. Nagranie wywiadu szeroko komentowano w mediach społecznościowych.",
      "date": "2026-11-21",
      "category": "statement",
      "sources": [
        {"url": "https://tvn24.pl/...", "domain": "tvn24.pl", "title": "Burzliwa rozmowa w Kropce nad i"}
      ]
    }
  ],
  "disclaimer": "Podsumowanie automatyczne na podstawie publikacji medialnych. Sprawdź źródła."
}
```

| field          | rule                                                                                                  |
|----------------|-------------------------------------------------------------------------------------------------------|
| `status`       | `ok` — items fresh or stale-but-served; `empty` — search found nothing relevant (FE hides the section or shows "Brak doniesień"); `unavailable` — never fetched successfully (no key, provider down) — FE hides the section |
| `generated_at` | when the stored result was produced; null when `unavailable`                                         |
| `items`        | 0–5, `date` desc                                                                                       |
| `headline`     | ≤ 80 chars, Polish, no clickbait, no exclamation marks                                                |
| `summary`      | ≤ 300 chars, 1–2 sentences, Polish, reported speech ("według Pudelka…"), no own judgement; contains the MP's last name |
| `date`         | `YYYY-MM-DD` of the earliest source publication; within last 30 days                                 |
| `category`     | `statement` \| `controversy` \| `legislation` \| `personal` \| `other` — FE may show an icon / chip |
| `sources`      | 1–3 `{url, domain, title}`; only URLs the web search returned (see Validation); `domain` without `www.` is the link label |
| `disclaimer`   | fixed string, FE shows it small under the section                                                    |

### Errors

| code | when                                    |
|------|-----------------------------------------|
| 404  | no MP with this id in the current term  |
| 502  | Sejm API down and MP list not cached (needed for the name) |

Provider errors are **never** an error status: stored result is served, or `status: "unavailable"`.

## Provider: Claude Haiku 5.5 + web search

- `app/utils/claude_news.py`: `messages.create` with model `MP_NEWS_MODEL` (`claude-haiku-5-5`),
  effort `MP_NEWS_EFFORT` (`low`), tool `web_search_20250305` with `max_uses = MP_NEWS_MAX_SEARCHES` (3),
  `user_location` PL, `blocked_domains = MP_NEWS_BLOCKED_DOMAINS` (starts empty; tabloids are **allowed**).
  The basic tool version is used on purpose: every result Claude sees arrives as a `web_search_tool_result`
  block, and those are what source URLs are checked against. `pause_turn` is resumed up to 3 times.
- Prompt (Polish): only events about **this** person (namesakes), report and attribute, never rumours as
  facts, no speculation about health / family / intimate life unless made public by the MP, no insults /
  exclamation marks / clickbait, max 5 items, the summary names the MP by last name, `source_urls` exactly as
  returned by the search. The answer is a JSON object in text (structured output cannot be combined with web
  search citations), parsed tolerantly (fences, text around, last object wins).
- `stop_reason` `refusal` / `max_tokens` → failure.

### Budget

- Every API response's usage is priced and stored in `llm_usage(created_at, feature, model, input_tokens,
  output_tokens, web_searches, cost_usd)`, including responses of a fetch that later fails.
- Price: Haiku 5.5 $0.10 / $0.50 per MTok in / out (prompts > 100K tokens: $0.50 / $2.50), cache writes 1.25x,
  reads 0.1x, web search `CLAUDE_WEB_SEARCH_USD` ($0.01). A model without a price in `PRICES` is refused.
- Before each fetch: `sum(cost_usd)` this calendar month (UTC) ≥ `CLAUDE_MONTHLY_BUDGET_USD` (5) → no call,
  treated as a failed fetch (stored results keep being served, retry after the backoff). In-flight calls can
  overshoot by a few cents. Also set a spend limit on the key in the Claude Console.
- Typical fetch: 1–3 searches + ~10–30K input tokens ≈ $0.01–0.04 → roughly 150–400 fetches per month.

Perplexity was dropped (paid, no key); Gemini + Google Search was implemented first but the key's project had
no Search grounding quota (429).

## Validation (before storing)

Drop an item when:
1. no `source_urls` entry that matches a search result (ignoring fragment / trailing slash / host case), or only blocked domains;
2. `date` missing, unparseable, older than 30 days or in the future;
3. MP last name (stem without the last letter for names ≥ 6 chars, so declined forms match; each part of a
   double name) does not appear in `headline + summary` (namesake / off-topic guard);
4. `headline` / `summary` empty. Too long → clipped at a word boundary with `…`.

A source URL already used by an earlier item is not reused (model order = most discussed first). Unknown
`category` → `other`. Sort by `date` desc, cap at 5. Zero items → `status: "empty"`.

## Data

New table (created by `create_all`, no migration):

- `mp_news(term, mp_id, status, items JSONB, generated_at, error_at, error, blocked)`, PK `(term, mp_id)`.
  - `items`: validated list as returned by the API.
  - `error_at` / `error`: last failed attempt (for backoff and debugging); success clears them.
  - `blocked`: admin takedown, served as `empty`, never refreshed.

## Freshness and cost

- TTL **24 h** (`settings.mp_news_ttl_hours`). Fresh row → served, no call.
- Stale row → **served immediately** + background refresh (one per MP at a time, in-process lock keyed
  `(term, mp_id)` like `sittings._locks`).
- No row (or never succeeded) → synchronous call, same lock, so parallel first views make one call;
  whole fetch capped at `MP_NEWS_TIMEOUT_SECONDS` (90) incl. retries; response waits, FE shows a skeleton.
- Failed call (any error, incl. timeout / unparseable answer): keep the old row; retry not before
  `MP_NEWS_RETRY_MINUTES` (60) after `error_at`.
- No `ANTHROPIC_API_KEY` → no call, `status: "unavailable"` (local dev without the key works).
- Cost: at most 1 fetch per viewed MP per day, hard-capped by the monthly budget above.

## Legal / content safety

Content about real, named people, partly from tabloids. Mitigations in this spec:
attribution in every summary, links to sources, disclaimer, prompt bans on rumour-as-fact and private
matters, no URLs from model text, 30-day window. Plus:
- No public delete endpoint; admin script `python -m scripts.mp_news`:
  `--show ID`, `--refresh ID` (fetch now, counts toward the budget), `--spend` (this month vs budget), `--clear ID` (refetched on next view), `--block ID` (takedown until `--clear`).
- Log every stored result (mp_id, item headlines, source URLs) for later review.

FE: hide the section unless `status == "ok"`.

## Out of scope

- Push / notifications about new items, history beyond 30 days, sentiment scores.
- Social media (X, Facebook) posts as sources beyond what the search returns.
- FE changes (listed in Goal for reference only).

## Tests

`tests/test_mp_news.py`, no network (Claude and DB stubbed):
- validation: URL must be a search result (normalised match), old / future / missing date, other person, empty
  headline, deny-list, declined last name, URL dedupe across items, sort, cap 5, clipping, unknown category
- answer parsing (fences, surrounding text, drafts, no JSON → error); search loop: results collected across
  `pause_turn`, error result blocks skipped, request params, refusal → error
- cost (tokens, searches, long-prompt price, unknown model refused); budget used up → no Claude call
- freshness: parallel first views → one call; fresh → no call; stale → served + one background refresh;
  failure keeps old row + backoff; first-view failure → `unavailable`; no key → no call; blocked → no call
- endpoint: contract, 404, 502
