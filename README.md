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

`POST /api/politicians/recognize` (multipart, field `photo`) detects faces and matches them against MPs of the current Sejm term.
Faces: OpenCV YuNet + SFace (ONNX, downloaded to `models/` on first use). Index: Qdrant collection `politician_faces`, built from Sejm API photos. Uploaded photos are not stored.

Build / refresh the index (once after deploy, ~500 MPs):

```bash
docker compose exec backend python -m scripts.index_politicians
```

```bash
curl -F photo=@face.jpg http://localhost:8000/api/politicians/recognize
# {"faces": [{"box": [x, y, w, h], "similarity": 0.62, "politician": {"full_name": ..., "club": ..., ...}}]}
```

`politician: null` = a face was found but nobody is similar enough (`FACE_MATCH_THRESHOLD`, default 0.363).

## Prod (EC2)

Same compose, without `docker-compose.local.yml`. The server's `.env` has `APP_ENV=prod` and no `COMPOSE_FILE` line, so Swagger, ReDoc and `/openapi.json` are disabled.

Code is bind-mounted from the git checkout, so there's no image registry. Deploy:

```bash
./deploy.sh   # git pull + restart backend; rebuild only when requirements/Dockerfile/compose change
```

Backups: `db/dump.sh` (cron) → S3.

Infrastructure and full setup: `docs/deploy.md` in the `context` repo.
