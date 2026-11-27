# Spec: CI/CD pipeline (GitHub Actions)

Status: draft · Branch: `ci-cd`

## Goal

Every PR and push gets checked automatically. Every merge to `main` that passes checks is deployed to
the EC2 server without anyone SSH-ing in by hand. A failed deploy is visible right away and easy to roll back.

Today:

- tests run only when someone runs `pytest` locally (and local envs drift: a stale venv fails on
  `ModuleNotFoundError: No module named 'pypdf'`);
- deploy = SSH to EC2, `./deploy.sh` (`git pull --ff-only` + restart/rebuild backend);
- no registry: code is bind-mounted from the git checkout, the image holds only dependencies.

The pipeline keeps that model (no registry, no image push). It automates the existing steps and adds no new infrastructure.

## Non-goals

- Image registry (ECR/GHCR), immutable image deploys, blue/green. Revisit when there is more than one server.
- Staging environment.
- Running the data pipelines (`scripts.ingest_acts`, `summarize_articles`, `index_*`) from CI.
- Integration tests against live Postgres/Qdrant. The current tests stub the DB and Sejm client
  (`tests/conftest.py`); adding service containers is a follow-up.
- Linting / formatting checks (decided: tests only).
- Infra-as-code for the EC2 box. It lives in the `context` repo (`docs/deploy.md`).

## Requirements

### R1 — CI on every PR and push

| id    | requirement                                                                                          |
|-------|------------------------------------------------------------------------------------------------------|
| R1.1  | Trigger: `pull_request` (any base) and `push` to `main`. Manual `workflow_dispatch` too.              |
| R1.2  | Python **3.13** (same as `Dockerfile`), deps from `requirements-dev.txt` (includes CPU torch index).   |
| R1.3  | `pytest` runs and must pass. Network calls are already blocked in tests (`no_sejm_network`); CI sets no API keys. |
| R1.5  | `docker compose config -q` validates compose files (prod + local override).                          |
| R1.6  | Docker image build (`docker build .`) runs **only** when `Dockerfile` or `requirements*.txt` change; it is not pushed anywhere. |
| R1.7  | pip cache keyed on `requirements*.txt` hash; a warm-cache CI run finishes in **≤ 5 min**.             |
| R1.8  | A newer push to the same PR cancels the older run (`concurrency`, `cancel-in-progress`).              |
| R1.9  | Workflow `permissions: contents: read` by default; no secrets available to PRs from forks.           |

### R2 — CD to production on merge to `main`

| id    | requirement                                                                                          |
|-------|------------------------------------------------------------------------------------------------------|
| R2.1  | Deploy runs only after CI succeeds on a `push` to `main` (same workflow, `needs: ci`) or by manual `workflow_dispatch` with an optional `ref`. Auto-deploy only while repo variable `AUTO_DEPLOY` = `true` (kill switch). |
| R2.2  | Deploys the **exact commit SHA that CI tested**, not "whatever HEAD is when the server pulls".        |
| R2.3  | Uses the existing `deploy.sh` logic (reload Caddy / rebuild / restart only what changed).             |
| R2.4  | No long-lived AWS keys and no SSH key in GitHub: GitHub OIDC → IAM role → **AWS SSM Run Command** on the instance. Port 22 does not need to be open to GitHub. |
| R2.5  | GitHub Environment `production` holds config (`AWS_ROLE_ARN`, `AWS_REGION`, `EC2_INSTANCE_ID`); IAM trust policy limited to `repo:ProsteFakty/backend:environment:production`. |
| R2.6  | At most one deploy at a time (`concurrency: deploy-prod`, `cancel-in-progress: false`).               |
| R2.7  | Post-deploy health check: `GET /api/ping` returns 200 within **180 s** (backend warms up embeddings at startup). Fails the job otherwise. |
| R2.8  | Server checkout dirty or not fast-forwardable → deploy aborts **before** restarting anything, job fails with the git error in the log. |
| R2.9  | Job summary shows: deployed SHA, previous SHA, what was restarted/rebuilt, health check result.      |

### R3 — Rollback

| id    | requirement                                                                                          |
|-------|------------------------------------------------------------------------------------------------------|
| R3.1  | Rollback = `workflow_dispatch` of the deploy with `ref=<older sha>`. Same path as a normal deploy.    |
| R3.2  | `deploy.sh <sha>` can move the checkout backwards (detached HEAD at `<sha>`); the restart/rebuild decision still compares old vs new. |
| R3.3  | README documents the rollback in ≤ 5 lines.                                                          |

### R4 — Repo hygiene

| id    | requirement                                                                                          |
|-------|------------------------------------------------------------------------------------------------------|
| R4.1  | Branch protection on `main`: required status checks `test`, `compose-config`; PR required.            |
| R4.2  | Third-party actions pinned to a full commit SHA (with version comment).                              |
| R4.3  | Dependabot: `github-actions` (weekly), `pip` (weekly, grouped minor/patch), `docker` (monthly).       |

## Acceptance criteria

1. PR with a failing test → `ci / test` red, merge blocked.
2. PR touching only `app/**` → no docker build job runs; PR touching `requirements.txt` → docker build runs and passes.
3. Two quick pushes to one PR → first run cancelled.
4. Merge to `main` → deploy job runs, server `git rev-parse HEAD` == merged SHA, `/api/ping` 200, summary written.
5. Merge touching only `app/**` → server log shows `restart backend`, no rebuild.
6. Merge touching `requirements.txt` → server rebuilds backend image.
7. Manual dispatch with `ref=<previous sha>` → server back on previous SHA, health check green.
8. Dirty server checkout (`touch` tracked file change) → deploy fails before restart, services keep running old code.
9. No AWS access key or SSH key stored anywhere in GitHub secrets.
10. Fork PR → CI runs, deploy job never runs and has no access to `production` env.

## Decisions

- Q1 — manual approval before deploy: **no**, auto-deploy on merge.
- Q2 — SSM agent: **to verify** on the instance (T-infra-1). Fallback if not wanted: SSH deploy key in the `production` env.
- Q3 — health check: **on the server itself** via SSM (`curl -fsS localhost/api/ping` through Caddy).
- Q4 — extra failure notifications: **no**, GitHub emails only.
- No lint job: CI runs tests only.
