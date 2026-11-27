# Design: CI/CD pipeline

Implements `spec.md`. Requirement ids in brackets.

## Files

```
.github/
  workflows/
    ci.yml          # test, compose check, conditional docker build   [R1]
    deploy.yml      # reusable + dispatch: OIDC → SSM → deploy.sh → health  [R2, R3]
  dependabot.yml                                                            [R4.3]
deploy.sh           # accepts optional <sha>, refuses dirty tree, prints summary   [R2.2, R2.8, R3.2]
README.md           # CI/CD + rollback section                              [R3.3]
```

## Flow

```
PR ──────────► ci.yml: test ─┬─ compose-config ─┬─ docker-build (if deps changed)
                             └──────────────────┘
push main ───► ci.yml (same jobs) ──► deploy job (uses: ./.github/workflows/deploy.yml, sha=${{ github.sha }})
dispatch ────► deploy.yml (input ref) ─────────────────────────────────┘
                                │
                                ▼
           configure-aws-credentials (OIDC, role from env `production`)
                                │
           aws ssm send-command AWS-RunShellScript on EC2_INSTANCE_ID:
             sudo -u <deploy-user> /srv/backend/deploy.sh <sha>
             curl retry loop localhost/api/ping (≤180s)
                                │
           poll get-command-invocation → stdout/stderr into job log + $GITHUB_STEP_SUMMARY
```

## ci.yml

- `on: pull_request, push: {branches: [main]}, workflow_dispatch` [R1.1]
- `permissions: contents: read` [R1.9]
- `concurrency: group: ci-${{ github.ref }}, cancel-in-progress: ${{ github.event_name == 'pull_request' }}` [R1.8]
  (don't cancel `main` runs: each one may be a deploy.)
- Jobs:
  - `test`: setup-python 3.13 with `cache: pip`, `cache-dependency-path: requirements*.txt` [R1.7],
    `pip install -r requirements-dev.txt`, `pytest -q`. Env: `APP_ENV=local`, no keys [R1.3].
    Torch CPU wheel is big (~200 MB) → pip cache matters; if still slow, cache `~/.cache/pip` + venv keyed on hash.
  - `compose-config`: `cp .env.example .env && docker compose -f docker-compose.yml config -q` and same with the local override [R1.5].
  - `changes`: `dorny/paths-filter` → output `deps` for `Dockerfile|requirements*.txt` [R1.6].
  - `docker-build`: `if: needs.changes.outputs.deps == 'true'`, `docker/build-push-action` with `push: false`, GHA layer cache.
  - `deploy`: `if: github.event_name == 'push' && github.ref == 'refs/heads/main' && vars.AUTO_DEPLOY == 'true'`,
    `needs: [test, compose-config, docker-build]`
    (docker-build may be skipped → use `if: always() && !failure() && !cancelled()` pattern),
    `uses: ./.github/workflows/deploy.yml`, `with: {sha: ${{ github.sha }}}`,
    `permissions: {contents: read, id-token: write}` [R2.1, R2.2].
    **Kill switch:** repository variable `AUTO_DEPLOY` (default unset = off). Lets the workflow merge before the
    AWS side exists, and lets anyone stop auto-deploys without a commit.

Required checks for branch protection [R4.1]: `test`, `compose-config`. Not `docker-build` (it's skipped on most PRs, and a skipped required check blocks merging).

## deploy.yml

- `on: workflow_call (inputs.sha)` + `workflow_dispatch (inputs.ref, default main)` [R2.1, R3.1]
- `environment: production` [R2.5] — vars: `AWS_ROLE_ARN`, `AWS_REGION`, `EC2_INSTANCE_ID`, `DEPLOY_DIR`, `DEPLOY_USER`.
  Stored as environment **variables** (not secrets): none is sensitive alone, and access is gated by the IAM trust policy.
- `concurrency: group: deploy-prod, cancel-in-progress: false` [R2.6]
- `permissions: id-token: write, contents: read` [R2.4]
- Steps:
  1. Resolve ref → full SHA (`git rev-parse` after checkout with `fetch-depth: 0`); dispatching a branch name deploys its current tip.
     Refuse a SHA that is not an ancestor of `origin/main` (deploy.sh checks this again on the server).
  2. `aws-actions/configure-aws-credentials` with `role-to-assume` (OIDC).
  3. `aws ssm send-command --document-name AWS-RunShellScript --instance-ids $ID --parameters commands=[...] --timeout-seconds 900`.
     SHA / user / dir validated by regex before going into the script; commands passed as JSON built with `jq`.
     Commands: `cd $DEPLOY_DIR && sudo -H -u $DEPLOY_USER ./deploy.sh $SHA` then the health loop
     `for i in $(seq 36); do curl -fsS localhost/api/ping && exit 0; sleep 5; done; exit 1` [R2.7].
  4. Poll `get-command-invocation` every 5 s up to 15 min (`aws ssm wait` caps at ~100 s; a rebuild takes longer).
  5. `get-command-invocation` → print stdout/stderr; write summary (SHA, previous SHA from deploy.sh output, action, health) [R2.9].
  6. Non-`Success` status → `exit 1`.

## deploy.sh changes

Implemented in `deploy.sh`:

- `./deploy.sh [sha]`; no arg = latest `origin/main`.
- Refuses dirty tracked files [R2.8], unknown commits, and commits not on `origin/main` — all before touching anything.
- `git checkout --detach <sha>` (forwards or backwards) [R3.2]; prints `deploy: OLD -> NEW` and `action: ...` lines [R2.9].
- Whole body inside `main()` so bash has parsed the script before checkout replaces `deploy.sh`.
- Tested in throwaway repos (stubbed docker): forward code-only → restart; requirements → rebuild; same SHA → none;
  rollback across requirements → rebuild; feature-branch SHA / garbage / dirty tree → exit 1, HEAD unchanged. Shellcheck clean.

Notes:
- Server moves from "on branch main" to detached HEAD. `git pull` by hand would stop working, so hand-run deploys use `./deploy.sh` (no arg). Write this in README.
- Diff compares OLD vs NEW in both directions, so a rollback across a `requirements.txt` change rebuilds too.
- `db/dump.sh` and cron are untouched.

## AWS side (out of repo, `context` repo / console) — T-infra

1. IAM OIDC provider `token.actions.githubusercontent.com` (once per account).
2. Role `gh-backend-deploy`, trust:
   `aud = sts.amazonaws.com`, `sub = repo:ProsteFakty/backend:environment:production`.
   Policy: `ssm:SendCommand` on `arn:aws:ssm:<region>::document/AWS-RunShellScript` + the instance ARN;
   `ssm:GetCommandInvocation`, `ssm:ListCommandInvocations` on `*`.
3. EC2: SSM agent running, instance profile has `AmazonSSMManagedInstanceCore`. Verify with
   `aws ssm describe-instance-information`.
4. SSM runs as root → `sudo -u <deploy user>` so git files keep their owner; deploy user must be in the `docker` group.

## Security notes

- `pull_request` (not `pull_request_target`): fork PRs get read-only token and no secrets/env.
- OIDC `sub` pinned to the `production` environment: a workflow on another branch can't assume the role
  unless it can deploy to that environment → add environment protection rule "only `main` branch".
- Actions pinned by SHA [R4.2]; Dependabot keeps them updated.
- Nothing from PR titles/branch names is interpolated into `run:` shell lines (script-injection).

## Risks

| risk | mitigation |
|------|------------|
| Tests red on clean env | checked in T0: 164 passed on a fresh venv |
| Backend startup > 180 s (bge-m3 load on CPU, first HF download) | `hf_cache` volume already persists the model; tune timeout after first real deploy |
| `docker compose up --build` during deploy = downtime of backend | accepted (single box, current behaviour); summary shows it |
| SSM not set up on instance | T-infra-1 installs it; fallback SSH deploy key |
