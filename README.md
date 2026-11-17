# backend

Backend for Proste Fakty: FastAPI + PostgreSQL + Qdrant, served by Caddy.

## Local

```bash
cp .env.example .env        # APP_ENV=local + COMPOSE_FILE with docker-compose.local.yml
docker compose up -d --build
```

- API: http://localhost:8000/api/ping (hot reload on file save)
- Swagger: http://localhost:8000/docs (local only)
- Through Caddy: http://localhost/api/ping

Tests:

```bash
pip install -r requirements-dev.txt
pytest
```

## Politician recognition (PoC)

`POST /api/v1/politicians/recognize` (multipart, field `photo`) detects faces and matches them against MPs of the current Sejm term.
Faces: OpenCV YuNet + SFace (ONNX, downloaded to `models/` on first use). Index: Qdrant collection `politician_faces`, built from Sejm API photos. Uploaded photos are not stored.

Build / refresh the index (once after deploy, ~500 MPs):

```bash
docker compose exec backend python -m scripts.index_politicians
```

```bash
curl -F photo=@face.jpg http://localhost:8000/api/v1/politicians/recognize
# {"faces": [{"box": [x, y, w, h], "similarity": 0.62, "politician": {"full_name": ..., "club": ..., ...}}]}
```

`politician: null` = a face was found but nobody is similar enough (`FACE_MATCH_THRESHOLD`, default 0.363).

## Law search

What the app does with it: the user types a topic ("papierosy", "zakaz konkurencji w umowie o pracę"), gets tiles
(act name, "Art. N - title", 1-3 sentence summary), opens an article and sees a plain-language explanation
with quotes from the law, and can switch to the original text.

### Loading the data

The acts listed in `app/pipeline/key_acts.py` (about 80: Constitution, codes, taxes, companies, IP, finance,
work, housing, everyday life) are loaded from the Sejm ELI API into Postgres (`acts`, `articles`):

1. the current wording: the unified text (tekst ujednolicony) that the Sejm Chancellery keeps up to date with
   every amendment, or the newest consolidated text (tekst jednolity) when there is none. Where the unified
   text shows a pending amendment (`[in force]` `<future>`), the wording in force is kept;
2. PDF → plain text without footnotes and page headers (`app/pipeline/pdf_text.py`);
3. text → articles (`app/pipeline/act_parser.py`);
4. references between articles found by rules, no LLM (`app/pipeline/references.py`).

Run in this order (each step can be limited to some acts, e.g. `DU/1997/553`, and rerun safely):

```bash
docker compose exec backend python -m scripts.ingest_acts                      # ~25 min, no LLM
docker compose exec backend python -m scripts.summarize_articles --limit 20   # needs GEMINI_API_KEY; check quality first
docker compose exec backend python -m scripts.summarize_articles              # the rest
docker compose exec backend python -m scripts.index_articles                  # embeddings into Qdrant (CPU, slow)
```

- `ingest_acts`: unchanged acts are skipped (`--force` to reload, `--dry-run` to only parse). PDFs are cached in
  `data/acts/`. To add an act, add its base-act ELI to `key_acts.py`.
- `summarize_articles`: Gemini writes a title and a summary per article. They are saved only if every number and
  article number in them appears in the article text (`app/pipeline/grounding.py`); otherwise one retry, then the
  article is listed and left without them. Only articles missing a title or summary are sent. On a key without
  billing (free tier) use `--concurrency 1`; the script stops when the quota runs out and can be rerun.
- `index_articles`: embeds "act, Art. N - title, summary" (bge-m3). An article without a summary is indexed by
  the beginning of its wording, so rerun this after summarizing.

### Endpoints

- `POST /api/v1/articles/search` `{"query", "limit", "offset", "act_key"}` → `{"total", "items"}`. One ranking
  from two searches merged by reciprocal rank fusion (`app/services/articles.py`): by meaning (Qdrant) and by
  words (Postgres full text over title, summary and wording; Polish endings handled by rough stemming in
  `app/pipeline/polish.py`). `"art. 148 kk"` returns that article; a `"phrase in quotes"` is matched as written.
- `GET /api/v1/articles/{slug}`: title, summary, original text, references to other articles.
- `GET /api/v1/articles/{slug}/explanation` (needs `GEMINI_API_KEY`): Gemini explains the article together with
  the provisions it refers to (up to 2 levels, a long referenced article cut to the cited `§`/`ust.`). The model
  gets only texts from our database; every point must quote them verbatim and the backend checks each quote and
  every number (`app/services/explanations.py`). Unverified points are dropped. Each quote comes with its
  position in the article text (`spans`) for highlighting. Generated on first request (several seconds), then
  cached in `article_explanations` until any of the source texts changes.
- `GET /api/v1/acts`, `GET /api/v1/acts/{act_key}`: loaded acts and their articles.

## Prod (EC2)

Same compose, without `docker-compose.local.yml`. The server's `.env` has `APP_ENV=prod` and no `COMPOSE_FILE` line, so Swagger, ReDoc and `/openapi.json` are disabled.

Code is bind-mounted from the git checkout, so there's no image registry. Deploy:

```bash
./deploy.sh   # git pull + restart backend; rebuild only when requirements/Dockerfile/compose change
```

Backups: `db/dump.sh` (cron) → S3.

Infrastructure and full setup: `docs/deploy.md` in the `context` repo.
