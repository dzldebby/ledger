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

## Deploying a new image

```sh
AWS_PROFILE=ledger ./scripts/deploy.sh          # tags with the current commit SHA
AWS_PROFILE=ledger ./scripts/deploy.sh hotfix1  # or an explicit tag
```

The script logs in to ECR, builds and pushes, reads `DATABASE_URL` from SSM,
creates the deployment, waits for it to go `ACTIVE`, and dumps container logs
if it fails.

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

The database is private, so `scripts/create_client.py` cannot be run from a
laptop, and Lightsail has no run-once/exec primitive. Instead, generate the key
locally and send only its hash to a temporary sidecar container:

```sh
# 1. Locally - the plaintext key never leaves this machine
python -c "from app.auth import generate_api_key, hash_api_key; \
k=generate_api_key(); print('KEY:', k); print('HASH:', hash_api_key(k))"

# 2. Add a second container to deployment.json alongside `app`:
#      image:       (same image)
#      command:     ["sh","-c","python scripts/register_client_hash.py && sleep infinity"]
#      environment: DATABASE_URL, CLIENT_ID, API_KEY_HASH
#    Deploy, confirm "Client '<id>' registered." in that container's log,
#    then deploy again with the sidecar removed.
```

`sleep infinity` is needed because Lightsail has no notion of a container that
is *meant* to exit — one that does looks like a crash and is restarted.
`register_client_hash.py` uses `ON CONFLICT DO NOTHING`, so a restart is a
harmless no-op.

Only the hash is sent to AWS. This matters because container `environment`
values are visible via the AWS API and persist in deployment history
permanently.

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

## Gotchas already paid for

Two failures cost significant debugging time; both are now guarded by comments
in the code, but they are worth knowing about.

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
