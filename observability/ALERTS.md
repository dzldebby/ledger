# Local observability alerts

These thresholds are intended for the mentorship demo environment. The ledger
team owns first response and records the correlation ID before investigating.

| Signal | Trigger | First response |
| --- | --- | --- |
| HTTP errors | Any sustained 5xx rate for five minutes | Inspect the service trace and correlated logs, then check its PostgreSQL dependency. |
| Outbox backlog | More than 100 unpublished events or oldest age above 60 seconds | Check publisher health and Kafka connectivity; do not delete or mark events manually. |
| Invariant violation | Any occurrence | Stop new writes, preserve evidence, and investigate the failed transaction before recovery. |
| Compliance DLQ | Any dead-lettered event | Inspect `/dead-letter-events`, correct the consumer, and replay from the DLQ with the original event ID. |
| Risk unavailable | Any sustained ledger 503 response | Check risk service and risk PostgreSQL; the ledger deliberately fails closed. |
