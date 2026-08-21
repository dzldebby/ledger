 AWS Lightsail Migration — Ledger API

 Context

 The ledger app (FastAPI + Postgres, built through Steps 1-9 of the original plan) currently only runs locally via uvicorn against a docker-compose Postgres. We already built CI (.github/workflows/ci.yml) but
 explicitly deferred CD because there was nowhere to deploy to. This plan builds that missing piece: containerize the app, stand up AWS infrastructure via Terraform, and wire up automatic deployment on every
 merge to main.

 Compute platform: AWS Lightsail (confirmed by you) — fixed pricing, appropriate for this project's scale (1M users, 20 TPS peak, one region, no sharding), matches the original HLD reasoning for choosing
 Lightsail over full AWS-native (ECS/RDS/ALB) or raw EC2.

 Within Lightsail: Container Service, not raw Instances + a bolted-on Load Balancer. Container Service's scale parameter gives built-in load balancing across replicas (directly satisfies the HLD's "two
 stateless instances minimum behind load balancer"), and it eliminates OS/Docker-daemon ownership and manual rolling-deploy logic that raw Instances would require.

 Database network posture: fully private, always (confirmed by you) — the Lightsail Postgres database is never reachable from outside AWS. Migrations run automatically inside the container on startup via an
 advisory-lock pattern (safe with scale=2 — one replica wins the lock and migrates, the other finds itself already at head and proceeds as a no-op). Admin tasks like provisioning a new API client run as a
 one-off container command, never from a laptop directly.

 Key facts this plan depends on (verified directly)

 - app/main.py mounts static files with a relative path: StaticFiles(directory="app/static"). The container's WORKDIR must be the repo root at runtime, or this mount breaks on startup.
 - app/database.py reads DATABASE_URL from env with no default — missing it crashes the app at pool-creation time inside the FastAPI lifespan (clean, loud failure — good for health-check-driven rollout).
 - migrations/env.py reads the same DATABASE_URL the same way — one env var drives both the app and Alembic.
 - app/auth.py's rate limiter is explicitly in-memory, per-instance — this stays a known, accepted limitation with scale=2 (not something this migration fixes).
 - requirements.txt has zero version pins — a reproducibility gap worth closing before building a Docker image from it.
 - No Dockerfile, no Terraform, no .dockerignore exist yet — this is greenfield infrastructure work.

 Implementation phases

 Phase 0 — Pin dependencies

 In a clean virtualenv, install current requirements.txt, confirm tests pass, then pip freeze > requirements.txt to lock every resolved version. Commit as its own change so a regression is easy to bisect
 before the Dockerfile depends on it.
 - Files: requirements.txt

 Phase 1 — Containerize + verify locally

 - Dockerfile: python:3.11-slim (matches CI's Python version), WORKDIR /app, copies app/, migrations/, alembic.ini, scripts/, non-root user, EXPOSE 8000, entrypoint runs migrations then execs uvicorn (--host
 0.0.0.0 --port 8000, no --reload).
 - .dockerignore: excludes .git/, .github/, .claude/, __pycache__/, .venv/, .env, tests/, docker-compose.yml, high level design.jpg, *.md. Does not exclude migrations/ — needed at runtime for the in-container
 migration step.
 - Add an app service to docker-compose.yml (pointing DATABASE_URL at the existing db service by container name) purely for local "prod-like" verification — doesn't replace the existing bare-Postgres dev
 workflow.
 - Verify: build the image, run it against compose Postgres, hit /health and /ui, provision a client via scripts/create_client.py, run a full deposit/transfer/reversal through the containerized app.
 - Files: Dockerfile, .dockerignore, docker-compose.yml

 Phase 2 — In-container migration strategy

 Implement the advisory-lock pattern in a new entrypoint script: acquire a Postgres advisory lock, run alembic upgrade head, release the lock, exec "$@" (the uvicorn CMD). This keeps the DB fully private —
 the container reaches it over Lightsail's private network, never publicly.
 - Verify: start two containers concurrently against the same fresh DB (simulating a scale=2 cold start) and confirm no crash/race — one migrates, the other is a clean no-op.
 - Files: scripts/entrypoint.sh, Dockerfile (ENTRYPOINT)

 Phase 3 — Terraform foundation (state backend)

 Terraform is not installed locally yet — install it first.
 terraform/bootstrap/: S3 bucket for remote state (versioned, encrypted, public access fully blocked) + DynamoDB table for state locking (the safe default; if the installed Terraform version supports native
 S3 conditional-write locking, that can replace the DynamoDB table as a simplification, verified at implementation time). Apply once with local state, then add a backend "s3" {} block pointing at the bucket
 it just created and run terraform init -migrate-state, so even the bootstrap stack ends up with remote state.
 - Files: terraform/bootstrap/providers.tf, terraform/bootstrap/main.tf, terraform/bootstrap/backend.tf, terraform/bootstrap/outputs.tf

 Phase 4 — Terraform core infrastructure

 Single root module (terraform/, no environment-splitting yet — matches "one region," no staging in scope):
 - aws_lightsail_database — Postgres, publicly_accessible = false, password via random_password (never typed/stored elsewhere).
 - aws_ecr_repository (+ lifecycle policy to expire old images) — image registry for CD.
 - aws_lightsail_container_service — scale = 2, power sized conservatively (compute is very unlikely to be the bottleneck at 20 TPS for a CRUD API; DB tier matters more), with private_registry_access enabled
 so the service can pull from the ECR repo above.
 - aws_ecr_repository_policy — grants the container service's auto-generated pull principal (a computed attribute from the resource above) read access to the ECR repo, wired directly in Terraform, no manual
 copy-paste.
 - aws_lightsail_container_service_deployment_version — initial deployment: container image, port 8000, environment block with DATABASE_URL (composed from the database resource's outputs + the generated
 password) and RATE_LIMIT_PER_MINUTE, public_endpoint with health check on /health.
 - aws_ssm_parameter (SecureString) holding the composed DATABASE_URL — lets the CD pipeline fetch it without needing Terraform state or backend access.
 - GitHub OIDC provider + a CD IAM role scoped narrowly to: ECR push/pull on this one repo, CreateContainerServiceDeployment + read on this one container service, GetParameter on this one SSM path. Trust
 policy scoped to repo:dzldebby/ledger:ref:refs/heads/main.
 - Terraform apply stays a manual, human-run action (your machine, your credentials) — not automated in CI. Only the CD role (used for app deployment, not infra changes) is scoped for GitHub Actions. This
 keeps infra changes deliberate and reviewed.
 - Review terraform plan together before the first apply.
 - Files: terraform/backend.tf, providers.tf, variables.tf, database.tf, container_service.tf, ecr.tf, ssm.tf, iam.tf, outputs.tf, terraform.tfvars.example; update root .gitignore for terraform/.terraform/,
 *.tfstate*, real .tfvars

 Phase 5 — CD pipeline

 New .github/workflows/cd.yml (kept separate from ci.yml — keeps the fast test loop uncoupled from the slower deploy path, makes adding a manual-approval gate later trivial), triggered via workflow_run on
 CI's completion on main, checked out at github.event.workflow_run.head_sha (not the workflow file's default ref).
 Steps: OIDC auth (aws-actions/configure-aws-credentials, no long-lived access-key secrets) → ECR login → build & push image tagged with commit SHA → fetch DATABASE_URL from SSM → aws lightsail
 create-container-service-deployment with the new image → poll until the deployment is healthy, fail the job on timeout.
 - One thing to confirm early in this phase: that ECR-pull via private_registry_access behaves as expected end-to-end. If it doesn't, the fallback is Lightsail's own push-container-image CLI (via the
 lightsailctl plugin) instead of ECR — a contained change to one step, not a rewrite.
 - Files: .github/workflows/cd.yml

 Phase 6 — End-to-end verification

 - Local: containerized app + compose Postgres serves /health, /ui, and a full deposit/transfer/reversal flow.
 - Terraform: terraform plan shows zero diff immediately after each apply (confirms no drift).
 - CD fires correctly: push a trivial commit to main, confirm CI passes, confirm cd.yml triggers and resolves the correct commit, confirm the Lightsail deployment reaches healthy.
 - Smoke test against the deployed service: hit the Lightsail default domain's /health; provision a real API key by running scripts/create_client.py as a one-off container command (per the "fully private,
 always" decision — never via a temporarily-opened public DB); run a real deposit/transfer/reversal against production; confirm idempotency-key reuse returns 409.
 - Explicitly confirm (and accept, don't attempt to fix here) that the rate limiter is only approximately correct with scale=2 — document as a known limitation, consistent with the existing code comment in
 app/auth.py.
 - Document the rollback path: redeploying a previous image via a new deployment version citing the previous ECR tag.

 Open items to verify during implementation (not blocking the plan, but flagged so they're not missed)

 - Exact current Lightsail relational_database_blueprint_id for Postgres — confirm Postgres 16 is available, or decide whether to accept a minor version skew against local/CI Postgres.
 - Exact current Terraform provider argument names/shape for private_registry_access on aws_lightsail_container_service (the single biggest uncertainty in this plan — the ECR-pull path hinges on it).
 - Whether the installed Terraform version supports S3-native state locking (simplifying away the DynamoDB table) or whether to use the DynamoDB table as the safe default.