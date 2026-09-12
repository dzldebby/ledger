# Local development

The local stack follows the high-level design using PostgreSQL for each owned
data boundary, Kafka for committed transaction events, and Grafana's
OpenTelemetry LGTM image for traces, metrics, and logs.

## Start everything

Start Docker Desktop, then run:

```powershell
docker compose up --build -d
docker compose ps
```

Local endpoints:

| Component | URL |
| --- | --- |
| Ledger API and test UI | `http://localhost:8000` / `http://localhost:8000/ui` |
| Risk API | `http://localhost:8001/docs` |
| Compliance alerts | `http://localhost:8002/compliance-alerts` |
| Synthetic external bank | `http://localhost:8003/docs` |
| Settlement orchestrator | `http://localhost:8004/docs` |
| Temporal settlement API | `http://localhost:8005/docs` |
| Kafka UI | `http://localhost:8080` |
| Temporal UI | `http://localhost:8081` |
| Grafana | `http://localhost:3000` |

Grafana starts with the Ledger dashboards and alert rules already provisioned.
Use the default local login `admin` / `admin` if prompted.

The local API key is `local-development-key`. It is intentionally committed
only as a synthetic local-development credential and must never be reused in a
deployed environment.

## Exercise the distributed path

1. Open the ledger test UI and enter `local-development-key`.
2. Create cash, personal, and business accounts, then post deposits or transfers.
3. Inspect `ledger.events` in Kafka UI.
4. Read alerts from `http://localhost:8002/compliance-alerts`.
5. Open Grafana Explore and search traces by the `ledger-api` service name.
6. Copy the `X-Correlation-ID` response header to correlate the same request in logs.

Risk rules decline owners beginning with `blocked-`, send transactions at or
above 500,000 minor units to review, decline at or above 1,000,000, and send a
client to review after five decisions in one minute. The ledger fails closed
with HTTP 503 when the configured risk service is unavailable.

To run the business payout saga after funding a business account:

```powershell
Invoke-RestMethod -Method Post http://localhost:8004/settlements/run
```

The orchestrator places a hold, calls the synthetic external bank, confirms the
ledger settlement, and produces a `transaction.settlement` outbox event.

## Learn Temporal with the additive settlement path

The original settlement endpoint on port 8004 is unchanged. The Temporal path
uses the same ledger and external-bank APIs while Temporal durably records each
workflow step.

### Test it entirely from the UI

1. Open `http://localhost:8000/ui/`.
2. Paste `local-development-key` into **API Key** and select **Save API Key**.
3. In **Temporal Settlement Lab**, choose a scenario.
4. Select **1. Create & Fund Business Account**. This creates the required cash
   and business accounts and deposits the amount shown in the funding field.
5. Select **2. Start Temporal Workflow**. The page polls the workflow until it
   finishes and then displays the test account balance.
6. Select the generated **Open ... in Temporal UI** link to inspect the batch,
   child workflow, activities, retries, and event history.
7. Select **Test Duplicate Start** to prove the same batch ID is rejected with
   `started: false` rather than creating a second workflow.

The scenarios demonstrate:

| Scenario | Expected result |
| --- | --- |
| Normal successful settlement | Payout is confirmed; business balance becomes zero |
| Bank fails twice, then succeeds | Payout activity shows three attempts; balance becomes zero |
| Bank rejects payout | Settlement is failed, its hold is released, and the funded balance remains available |
| Durable workflow timer | Workflow remains running during the timer, then settles successfully |
| Slow bank response | Payout activity takes about eight seconds, then settles successfully |

The rejection scenario deliberately leaves the business account funded, so a
later settlement batch can pick it up again. Create a fresh funded account for
each independent demonstration.

### Test it from PowerShell

1. Fund at least one `business` account in the ledger UI.
2. Start a Temporal batch with a unique learning batch ID:

```powershell
$body = @{ batch_id = "learning-001" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8005/temporal/settlements/run `
  -ContentType application/json `
  -Body $body
```

3. Open `http://localhost:8081` and select `SettlementBatchWorkflow`.
4. Open its child `SettlementWorkflow` to see the payout and confirmation
   activities and their retry history.
5. Check the workflow status through the starter API:

```powershell
Invoke-RestMethod http://localhost:8005/temporal/workflows/settlement-batch-learning-001
```

To demonstrate durable recovery, start a batch and stop the worker while it is
running, then start it again:

```powershell
docker compose stop temporal-worker
docker compose start temporal-worker
```

Temporal keeps the workflow history in `temporal-db`. The worker resumes the
next unfinished activity. Activity calls use the settlement ID as the bank's
idempotency key, so a retry returns the same synthetic payout.

## Tests

```powershell
docker compose up -d db
py -3.11 -m pytest tests -v
```

Stop the stack without deleting PostgreSQL or Grafana data:

```powershell
docker compose down
```

Use `docker compose down -v` only when you deliberately want to erase all local
ledger, risk, compliance, and observability data.
