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

## Prod (EC2)

Same compose, without `docker-compose.local.yml`. The server's `.env` has `APP_ENV=prod` and no `COMPOSE_FILE` line, so Swagger, ReDoc and `/openapi.json` are disabled.

Code is bind-mounted from the git checkout, so there's no image registry. Deploy:

```bash
./deploy.sh   # git pull + restart backend; rebuild only when requirements/Dockerfile/compose change
```

Backups: `db/dump.sh` (cron) → S3.

Infrastructure and full setup: `docs/deploy.md` in the `context` repo.
