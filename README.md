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

## Law: loading acts

The key acts (`app/pipeline/key_acts.py`: Constitution, codes, tax acts, road traffic law) are loaded from the Sejm ELI API into Postgres (`acts`, `articles`):

1. newest consolidated text in force (tekst jednolity, PDF only), or the unified text when there is none;
2. PDF → plain text without footnotes and page headers (`app/pipeline/pdf_text.py`);
3. text → articles (`app/pipeline/act_parser.py`);
4. references between articles found by rules, no LLM (`app/pipeline/references.py`).

```bash
docker compose exec backend python -m scripts.ingest_acts                        # all key acts
docker compose exec backend python -m scripts.ingest_acts DU/1997/553 --dry-run  # one act, parse only
```

Unchanged acts are skipped (`--force` to reload). PDFs are cached in `data/acts/`.

Then summaries (needs `GEMINI_API_KEY`) and the search index, in this order:

```bash
docker compose exec backend python -m scripts.summarize_articles --limit 20   # check quality on a few first
docker compose exec backend python -m scripts.summarize_articles              # the rest (~5000 articles)
docker compose exec backend python -m scripts.index_articles                  # embeds summaries into Qdrant
```

A summary is saved only if every number and article number in it appears in the article text (`app/pipeline/grounding.py`); otherwise Gemini gets one retry, then the article is listed and left without a summary.

Search (`POST /api/v1/articles/search`) combines: the article named in the query (`art. 148 kk`), semantic similarity of summaries (Qdrant) and full-text match of the wording (Postgres). Acts: `GET /api/v1/acts`, `GET /api/v1/acts/{act_key}`.

## Prod (EC2)

Same compose, without `docker-compose.local.yml`. The server's `.env` has `APP_ENV=prod` and no `COMPOSE_FILE` line, so Swagger, ReDoc and `/openapi.json` are disabled.

Code is bind-mounted from the git checkout, so there's no image registry. Deploy:

```bash
./deploy.sh   # git pull + restart backend; rebuild only when requirements/Dockerfile/compose change
```

Backups: `db/dump.sh` (cron) → S3.

Infrastructure and full setup: `docs/deploy.md` in the `context` repo.
