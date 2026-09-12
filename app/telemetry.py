import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from opentelemetry import metrics, trace
from opentelemetry.propagate import inject
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk._logs import LoggingHandler, LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        span = trace.get_current_span().get_span_context()
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "severity": record.levelname,
            "service": os.getenv("OTEL_SERVICE_NAME", "ledger-api"),
            "environment": os.getenv("APP_ENV", "local"),
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
            "trace_id": f"{span.trace_id:032x}" if span.is_valid else None,
            "span_id": f"{span.span_id:016x}" if span.is_valid else None,
        }
        return json.dumps(payload, separators=(",", ":"))


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        return True


def _endpoint(path: str) -> str:
    base = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
    return f"{base}{path}"


def configure_telemetry(service_name: str) -> None:
    global _configured
    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
    if _configured:
        return

    resource = Resource.create({
        "service.name": service_name,
        "deployment.environment.name": os.getenv("APP_ENV", "local"),
    })
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    tracer_provider = TracerProvider(resource=resource)
    if otlp_endpoint:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=_endpoint("/v1/traces")))
        )
    trace.set_tracer_provider(tracer_provider)

    metric_readers = []
    if otlp_endpoint:
        metric_readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=_endpoint("/v1/metrics")),
                export_interval_millis=5000,
            )
        )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=metric_readers))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(JsonFormatter())
    stream_handler.addFilter(CorrelationFilter())
    root_logger.addHandler(stream_handler)

    if otlp_endpoint:
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=_endpoint("/v1/logs")))
        )
        otlp_handler = LoggingHandler(logger_provider=logger_provider)
        otlp_handler.addFilter(CorrelationFilter())
        root_logger.addHandler(otlp_handler)

    AsyncPGInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    _configured = True


def instrument_app(app: FastAPI) -> None:
    meter = metrics.get_meter(os.getenv("OTEL_SERVICE_NAME", "service"))
    request_count = meter.create_counter("http.server.requests")
    request_duration = meter.create_histogram("http.server.duration", unit="ms")

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        token = correlation_id_var.set(correlation_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Correlation-ID"] = correlation_id
            carrier = {}
            inject(carrier)
            if carrier.get("traceparent"):
                response.headers["traceparent"] = carrier["traceparent"]
                response.headers["X-Trace-ID"] = carrier["traceparent"].split("-")[1]
            return response
        finally:
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            attributes = {
                "http.request.method": request.method,
                "http.route": route_path,
                "http.response.status_code": status_code,
            }
            request_count.add(1, attributes)
            request_duration.record((time.perf_counter() - started) * 1000, attributes)
            correlation_id_var.reset(token)

    FastAPIInstrumentor.instrument_app(app)


def get_correlation_id() -> str:
    return correlation_id_var.get() or str(uuid.uuid4())
