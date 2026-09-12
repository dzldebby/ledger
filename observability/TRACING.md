# Following a transaction trace

Rebuild the services with `docker compose up --build -d`. Make a ledger request
using the usual API key and a fresh idempotency key. The response includes
`X-Trace-ID` and `traceparent`, alongside `X-Correlation-ID`.

Open http://localhost:3000, select Explore and the Tempo datasource, and search
for the value of `X-Trace-ID`. Allow several seconds for telemetry export and
Kafka processing. Refresh the trace to see asynchronous spans arriving later.

You can supply a valid W3C `traceparent` header to continue an existing trace.
Without one, HTTP instrumentation creates a new trace automatically. The trace
ID stays constant; each span receives a different span ID. Do not reuse a fixed
trace ID across unrelated requests.

The ledger trace includes its HTTP request, risk HTTP call and evaluation,
posting/balance stage, PostgreSQL operations, and outbox enqueue. The event
stores the enqueue context. The publisher creates a child producer span and
injects its context into Kafka headers. Compliance uses that header as the
parent of its consumer span. Duplicate processing is recorded as a span event.
DLQ publishing continues the original message trace and records retry exhaustion.

The event payload remains unchanged when published; its traceparent describes
the originating ledger operation. Kafka headers describe the current delivery.
Repeated delivery can therefore create additional publisher and consumer spans
under the same originating trace.

Settlement runs have their own trace covering preparation, bank payout,
confirmation, and the resulting outbox/compliance flow. They do not automatically
join the earlier traces of deposits that funded the business account. Correlate
those operations using ledger history and transaction/settlement identifiers.

The focused propagation tests run without PostgreSQL or Kafka:

```powershell
py -3.11 -m pytest tests/test_tracing.py -v
```

These tests verify response trace headers and producer-to-consumer parentage
using an in-memory span exporter. Live Tempo ingestion still requires the local
Compose stack.
