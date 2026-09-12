import json
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.telemetry import instrument_app
from app.services.publisher import publish_batch
from compliance_app.service import process_event
from tests.test_publisher import RecordingConnection, RecordingProducer, fixture

exporter = InMemorySpanExporter()
trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))


def test_http_response_exposes_incoming_trace_id():
    application = FastAPI()
    instrument_app(application)

    @application.get("/probe")
    async def probe():
        return {"ok": True}

    with TestClient(application) as client:
        response = client.get("/probe", headers={
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        })
    assert response.headers["X-Trace-ID"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert response.headers["traceparent"].split("-")[2] != "00f067aa0ba902b7"


class DuplicateConnection:
    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchval(self, *args):
        return None


@pytest.mark.asyncio
async def test_publisher_and_consumer_continue_event_trace():
    exporter.clear()
    event = fixture("deposit")
    original_context = event["traceparent"]
    producer = RecordingProducer()
    row = {"event_id": event["event_id"], "payload": json.dumps(event)}
    await publish_batch(RecordingConnection(), producer, [row])
    message = producer.messages[0][1]
    assert json.loads(message["value"])["traceparent"] == original_context
    event["traceparent"] = dict(message["headers"])["traceparent"].decode()
    await process_event(DuplicateConnection(), event)
    spans = exporter.get_finished_spans()
    published = next(span for span in spans if span.name == "outbox.publish")
    consumed = next(span for span in spans if span.name == "compliance.evaluate")
    assert published.parent.span_id == int(original_context.split("-")[2], 16)
    assert consumed.parent.span_id == published.context.span_id
    assert consumed.context.trace_id == published.context.trace_id
    assert consumed.events[0].name == "compliance.duplicate_skipped"
