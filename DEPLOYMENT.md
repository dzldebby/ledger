# Deployment

The ledger runs on AWS Lightsail: a Container Service fronting a private
Postgres database, with images stored in ECR. All infrastructure is managed by
Terraform in `terraform/`.

- **Live URL:** the `container_service_url` output from `terraform output`
- **AWS account:** 302127759466, region `us-east-1`
- **CLI profile:** every AWS/Terraform command must use `AWS_PROFILE=ledger`

## Layout

| Resource | Purpose |
| --- | --- |
| `aws_lightsail_container_service.ledger` | Runs the app; provides the public HTTPS endpoint and load balancing |
| `aws_lightsail_database.ledger` | Postgres 16, `publicly_accessible = false` |
| `aws_ecr_repository.ledger` | Image registry; immutable tags |
| `aws_ssm_parameter.database_url` | `DATABASE_URL` as a SecureString, for the CD pipeline |
| `aws_iam_role.cd` | GitHub Actions deploy role, via OIDC (no long-lived keys) |

State lives in the S3 bucket created by `terraform/bootstrap/`, which is a
separate stack and is *not* destroyed between sessions.

## How code reaches production

Merging to `main` deploys automatically. No manual step is required.

```
push to main -> CI (ci.yml)  -> tests against a Postgres service container
                     |
                     v  on success only
                CD (cd.yml)  -> assume ledger-cd via OIDC
                             -> scripts/deploy.sh (build, push, deploy)
                             -> smoke test /health, fail the job on non-200
```

`cd.yml` is triggered by `workflow_run` on CI completion rather than by `push`,
so a failing test suite can never deploy. It checks out
`github.event.workflow_run.head_sha` — `workflow_run` jobs otherwise run
against the tip of the default branch, which is not necessarily the commit CI
validated.

Deploys are serialised by a `deploy-production` concurrency group. It queues
rather than cancels, so an in-flight deployment is always allowed to settle.
Note that GitHub only holds *one* pending run per group: if several commits
land in quick succession, intermediate runs are cancelled while queued and only
the newest deploys. That is intended — the newest commit is the one you want.

CD authenticates with a short-lived OIDC token. There are no AWS keys in GitHub
secrets.

## Bringing the stack up from nothing

```sh
cd terraform && AWS_PROFILE=ledger terraform apply       # ~12 min (database dominates)
cd ..
AWS_PROFILE=ledger ./scripts/deploy.sh                   # build, push, deploy
AWS_PROFILE=ledger ./scripts/provision_client.sh demo    # mint an API key
```

The last step prints the API key once — save it. The container service URL is
newly generated on each `apply`, so it will differ from last time; get it from
`terraform output container_service_url` or the tail of `deploy.sh`.

## Deploying by hand

Normally you do not need this — merging to `main` deploys. Use it to bring a
fresh stack up, or to deploy something that is not on `main`.

```sh
AWS_PROFILE=ledger ./scripts/deploy.sh          # tags with the current commit SHA
AWS_PROFILE=ledger ./scripts/deploy.sh hotfix1  # or an explicit tag
```

CD runs this same script, so the manual and automated paths cannot drift apart.
It logs in to ECR, builds and pushes, reads `DATABASE_URL` from SSM, creates the
deployment, waits (bounded, 10 min) for it to go `ACTIVE`, and dumps container
logs if it fails.

Two things it handles that are easy to get wrong by hand:
`--provenance=false --sbom=false` (buildx otherwise attaches an attestation
manifest that ECR rejects with a `400` on push), and `MSYS_NO_PATHCONV=1`
(Git Bash on Windows otherwise mangles the leading `/` of the SSM parameter
name into a drive path).

ECR tags are immutable, so re-running with an already-pushed tag fails rather
than silently replacing an image.

Migrations run automatically on container start (`scripts/entrypoint.sh` →
`scripts/migrate_with_lock.py`) under a Postgres advisory lock, so concurrent
replica cold starts are safe — one migrates, the rest find the DB at head and
no-op.

## Rolling back

Deployments are immutable and versioned, and every image tag is retained (the
ECR lifecycle policy keeps the last 10). To roll back, create a new deployment
that cites the previous tag:

```sh
# See deployment history and the image each version used
AWS_PROFILE=ledger aws lightsail get-container-service-deployments \
  --service-name ledger --region us-east-1 \
  --query 'deployments[].{v:version,state:state,image:containers.app.image}'
```

Edit `deployment.json` to reference the known-good tag and run
`create-container-service-deployment` again. Lightsail keeps serving the
current healthy deployment until the new one passes its health check, so a
rollback that itself fails to boot will not take the service down.

## Provisioning an API client

```sh
AWS_PROFILE=ledger ./scripts/provision_client.sh <client_id>
```

The key is printed once at the end. Only its SHA-256 hash is stored, so a lost
key cannot be recovered — provision a new client instead.

### Why it takes two deployments

The database is private, so `scripts/create_client.py` cannot be run from a
laptop, and Lightsail has no exec/run-once primitive. The only thing that can
reach the database is a container inside the service. So the script adds a
second container to the running deployment purely to carry one `INSERT`, then
deploys again with it removed. It reuses the live deployment's image and
settings, so the running app is unaffected.

Three details that matter:

- **The sidecar ends in `sleep infinity`.** Lightsail has no notion of a
  container that is *meant* to exit; one that does looks like a crash and is
  restarted.
- **`register_client_hash.py` uses `ON CONFLICT DO NOTHING`,** so a restart is
  a harmless no-op rather than a crash.
- **Only the hash is sent to AWS.** Container `environment` values are readable
  via the AWS API and persist in deployment history permanently, so the
  plaintext key is generated locally and never leaves the machine.

If you provision clients often, this two-deployment cycle is the wrong shape —
consider a small Lightsail instance in the same region as an admin jumpbox, or
a bootstrap-token-gated admin endpoint.

## Known limitations

**Rate limiting is per-instance.** `app/auth.py` keeps `_rate_limit_state` in
process memory. At the current `container_scale = 1` this is exact. At
`scale = 2` each replica counts independently, so a client's effective limit
becomes up to 2× `RATE_LIMIT_PER_MINUTE` depending on how the load balancer
distributes requests. This is accepted, not a bug to fix here; a correct
multi-instance implementation needs shared state (e.g. Redis).

**No staging environment.** `terraform/` is a single root module deploying
straight to production.

## Cost and the destroy-between-sessions habit

Roughly **$22/month** if left running continuously: $15 database (`micro_2_0`)
plus $7 for one `nano` container node.

Lightsail bills container services and instances hourly and prorates on
deletion, so tearing down between working sessions genuinely saves money —
[AWS confirms](https://aws.amazon.com/lightsail/faq/) that deleting before
month end charges "a prorated cost based on the total number of hours that you
used your Lightsail container service." Note that *disabling* a container
service does **not** stop charges; only deletion does.

```sh
cd terraform && AWS_PROFILE=ledger terraform destroy
```

This destroys the app stack but leaves the S3 state bootstrap intact, so
`terraform apply` rebuilds everything later. The database is destroyed with
`skip_final_snapshot = true`, so **all ledger data is lost** — fine for a
demo/learning deployment, not something to run against real data.

## Debugging a failed deploy

**GitHub's job logs are not readable via the API without a token** — the logs
endpoint returns `403` even on a public repo. When a deploy fails at the AWS
boundary, CloudTrail is the authoritative source and needs no extra setup:

```sh
# Why did OIDC / assume-role fail? Shows the exact subject claim presented.
AWS_PROFILE=ledger aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity \
  --max-results 3

# Which API call was denied, for which principal, on which resource?
AWS_PROFILE=ledger aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetContainerServiceDeployments \
  --max-results 3
```

Every one of the failures below was diagnosed this way after the Actions UI
gave nothing useful.

## Gotchas already paid for

Each of these cost real debugging time. All are now guarded by comments in the
code, but the shared lesson is worth stating plainly: **the expensive failures
here are the silent ones.** Four separate times, a misconfiguration produced no
error at all — just a hang or an empty log — and looked exactly like slowness.
When something appears to be taking too long, check whether it is actually
failing invisibly.

**ECR pull permission uses a different principal than you expect.** A Lightsail
container service exposes *two* ARNs: `principal_arn` and, separately,
`private_registry_access[0].ecr_image_puller_role[0].principal_arn`. The image
pull uses the latter. Granting the former produces no error message anywhere —
the image simply never pulls, no container is created, no application logs are
emitted, and the deployment reports `Canceled`. Health-check tuning will not
help, because the container never reaches the health-check stage.

**`%` in `DATABASE_URL` breaks Alembic.** `config.set_main_option()` writes
through Python's `ConfigParser`, which treats `%` as interpolation syntax. A
Terraform-generated password containing URL-encoded characters (`%5E`, `%25`,
…) raises `ValueError: invalid interpolation syntax`. `migrations/env.py`
escapes `%` as `%%`. This is invisible locally and in CI, where the password is
the literal string `ledger` and contains no `%`.
