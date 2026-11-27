# Tasks: CI/CD pipeline

Legend: `[x]` done · `[~]` code written, verification pending (needs push / AWS / real merge) · `[ ]` not started.

Order matters. Each task = one commit (or small PR), each with a check that proves it's done.
Ids map to `spec.md` requirements.

## Phase 0 — Baseline

- [x] **T0 — Clean-env test run** (R1.2, R1.3)
  Fresh venv, Python 3.13, `pip install -r requirements-dev.txt`, `pytest -q`. List failures; fix or mark
  with a reason. **Done:** green on a fresh venv; note runtime + install time.
  Result (2026-11-27): **164 passed in 4.5 s**, no fixes needed. Install 5 s with a warm local uv cache
  (cold pip on a CI runner will be slower: CPU torch wheel ~200 MB → pip cache in T1 matters).

## Phase 1 — CI

- [~] **T1 — `ci.yml` with `test` job** (R1.1, R1.2, R1.3, R1.7, R1.8, R1.9)
  **Done:** PR on `ci-cd` shows `test` green; second push cancels first run; warm-cache run ≤ 5 min.
- [~] **T3 — Compose validation** (R1.5)
  `compose-config` job. **Done:** green; broken YAML in a scratch commit → red.
- [~] **T4 — Conditional docker build** (R1.6)
  `changes` job (paths-filter) + `docker-build` (no push, GHA cache). **Done:** acceptance #2 both cases.
- [~] **T5 — Pin actions + Dependabot** (R4.2, R4.3)
  Replace tags with SHAs (`# vX.Y.Z` comment). Add `.github/dependabot.yml`. **Done:** Dependabot tab shows 3 ecosystems.

## Phase 2 — Infra (out of repo, needs AWS console / `context` repo)

- [ ] **T-infra-1 — SSM on EC2** (R2.4, Q2)
  Check `aws ssm describe-instance-information` lists the instance as `Online`; else attach
  `AmazonSSMManagedInstanceCore` to the instance profile, ensure `snap.amazon-ssm-agent` running.
- [ ] **T-infra-2 — OIDC provider + deploy role** (R2.4, R2.5)
  Trust `sub = repo:ProsteFakty/backend:environment:production`; least-privilege SSM policy (see design).
  **Done:** role ARN known; document in `context/docs/deploy.md`.
- [ ] **T-infra-3 — GitHub Environment `production`** (R2.5, Q1)
  Vars: `AWS_ROLE_ARN`, `AWS_REGION`, `EC2_INSTANCE_ID`, `DEPLOY_DIR`, `DEPLOY_USER`.
  Deployment branch rule: `main` only. Required reviewers: none (Q1).

## Phase 3 — CD

- [~] **T6 — `deploy.sh` accepts SHA, refuses dirty tree** (R2.2, R2.3, R2.8, R3.2, R2.9)
  Local: all scenarios pass in throwaway repos + shellcheck clean. Pending: first run on the real server.
  Keep no-arg behaviour. Echo `deploy: OLD -> NEW` and `action: ...` lines (parsed for summary).
  **Done:** on server by hand: `./deploy.sh <sha>` forward, `./deploy.sh <older sha>` back, dirty tree → exit 1 with
  services untouched (acceptance #8). Shellcheck clean.
- [~] **T7 — `deploy.yml` (dispatch only first)** (R2.1, R2.4–R2.7, R2.9, R3.1)
  Only `workflow_dispatch` at first, so it can be tested without merging. **Done:** manual run deploys current
  `main` SHA, health green, summary filled (acceptance #4 minus auto-trigger, #7).
- [~] **T8 — Wire auto-deploy** (R2.1, R2.2)
  `deploy` job in `ci.yml` calling `deploy.yml` via `workflow_call` on push to `main`.
  Off until repo variable `AUTO_DEPLOY=true` — set it only after T7 passes by hand.
  **Done:** acceptance #4, #5, #6 on real merges; #10 with a fork PR (or reasoning + `pull_request` event check).

## Phase 4 — Lock down + docs

- [ ] **T9 — Branch protection** (R4.1)
  Required checks: `test`, `compose-config`. Require PR, require branch up to date.
  **Done:** acceptance #1.
- [~] **T10 — README** (R3.3)
  Replace "Prod (EC2)" deploy section: auto-deploy on merge, manual dispatch, rollback (`Run workflow` →
  `ref=<sha>`), hand deploy `./deploy.sh` (detached HEAD note), where logs are.
  **Done:** acceptance #9 checked (no keys in repo/org secrets); spec status → `implemented`.

## Verification matrix

| acceptance # | task(s) |
|--------------|---------|
| 1            | T1, T9 |
| 2            | T4 |
| 3            | T1 |
| 4, 5, 6      | T6, T7, T8 |
| 7            | T6, T7 |
| 8            | T6 |
| 9, 10        | T-infra-2, T8, T10 |
