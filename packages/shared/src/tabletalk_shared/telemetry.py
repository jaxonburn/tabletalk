import logging
import os
from collections.abc import Callable

from elastic_transport import TransportError
from elasticsearch import ApiError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_telemetry(app: FastAPI, service_name: str) -> Callable[[], None]:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("elastic_transport").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        logging.getLogger(__name__).info("OTLP endpoint not set; telemetry export is disabled.")
        return lambda: None

    resource = Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", service_name),
            "service.namespace": os.getenv("OTEL_SERVICE_NAMESPACE", "tabletalk"),
            "service.version": os.getenv("APP_VERSION", "development"),
        }
    )
    traces = TracerProvider(resource=resource)
    traces.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(traces)
    meter = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=10000)
        ],
    )
    metrics.set_meter_provider(meter)
    logs = LoggerProvider(resource=resource)
    logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logs)
    handler = LoggingHandler(level=logging.INFO, logger_provider=logs)
    logging.getLogger().addHandler(handler)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
    HTTPXClientInstrumentor().instrument()

    def shutdown() -> None:
        logging.getLogger().removeHandler(handler)
        traces.shutdown()
        meter.shutdown()
        logs.shutdown()

    return shutdown


def register_storage_errors(app: FastAPI) -> None:
    async def unavailable(_request: Request, error: Exception) -> JSONResponse:
        logging.getLogger(__name__).error(
            "Elasticsearch request failed (%s).", type(error).__name__
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "Elasticsearch is unavailable. Please try again shortly."},
        )

    app.add_exception_handler(TransportError, unavailable)
    app.add_exception_handler(ApiError, unavailable)
