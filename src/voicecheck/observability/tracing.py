"""Tracer initialization, exporter selection, and shutdown.

Importing this module never imports the OTel SDK — that only happens
inside ``init_tracing`` if the user actually wants tracing on. This
keeps voicecheck's cold-start cost zero for users who don't care about
observability.

Exporter precedence (first match wins):
1. ``console=True`` flag → ConsoleSpanExporter (debug)
2. ``endpoint`` set → OTLP HTTP exporter
3. ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var present → OTLP HTTP exporter
4. Otherwise → tracing stays off (no exporter installed)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("voicecheck.observability.tracing")

_PROVIDER: Any = None
_ENABLED: bool = False
_VOICECHECK_VERSION: str | None = None


@dataclass
class ObservabilityConfig:
    """Runtime config for OTel tracing.

    Mostly populated from the YAML scenario's ``observability:`` block,
    with CLI flags taking precedence. Standard ``OTEL_EXPORTER_OTLP_*``
    env vars are honoured by the OTLP exporter automatically — they
    don't need to be wired through here.
    """

    enabled: bool = False
    service_name: str = "voicecheck"
    endpoint: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    console: bool = False
    tags: list[str] = field(default_factory=list)
    # Allow passing raw resource attrs through (e.g. environment, deployment.id).
    resource_attrs: dict[str, str] = field(default_factory=dict)


def is_enabled() -> bool:
    """True iff ``init_tracing`` succeeded with a real exporter."""
    return _ENABLED


def _voicecheck_version() -> str:
    global _VOICECHECK_VERSION
    if _VOICECHECK_VERSION is not None:
        return _VOICECHECK_VERSION
    try:
        from importlib.metadata import version

        _VOICECHECK_VERSION = version("voicecheck")
    except Exception:
        _VOICECHECK_VERSION = "unknown"
    return _VOICECHECK_VERSION


def init_tracing(config: ObservabilityConfig) -> bool:
    """Initialize the global OTel tracer provider.

    Idempotent: calling twice is a no-op (returns the previous result).
    Returns True when tracing actually got wired up, False otherwise
    (OTel not installed, no exporter configured, init failed).
    """
    global _PROVIDER, _ENABLED

    if _PROVIDER is not None:
        return _ENABLED

    if not config.enabled:
        return False

    # Decide exporter early so we can bail before importing OTel SDK
    # (which is heavy) when nothing's configured.
    has_endpoint = bool(config.endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    if not config.console and not has_endpoint:
        logger.debug(
            "Observability enabled but no exporter configured — "
            "set endpoint, OTEL_EXPORTER_OTLP_ENDPOINT, or use console=True"
        )
        return False

    try:
        from opentelemetry import trace  # type: ignore[import-not-found]
        from opentelemetry.sdk.resources import Resource  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace.export import (  # type: ignore[import-not-found]
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
    except ImportError:
        logger.warning(
            "Observability requested but OTel SDK not installed. Run: pip install voicecheck[otel]"
        )
        return False

    resource_attrs: dict[str, str] = {
        "service.name": config.service_name,
        "service.version": _voicecheck_version(),
        "voicecheck.version": _voicecheck_version(),
    }
    if config.tags:
        resource_attrs["voicecheck.tags"] = ",".join(config.tags)
    resource_attrs.update(config.resource_attrs)

    try:
        resource = Resource.create(resource_attrs)
        provider = TracerProvider(resource=resource)
    except Exception as e:
        logger.warning("Failed to create TracerProvider: %s", e)
        return False

    exporter_installed = False

    if config.console:
        try:
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            exporter_installed = True
        except Exception as e:
            logger.warning("Failed to install console exporter: %s", e)

    if has_endpoint and not config.console:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
                OTLPSpanExporter,
            )

            exporter_kwargs: dict[str, Any] = {}
            if config.endpoint:
                exporter_kwargs["endpoint"] = config.endpoint
            if config.headers:
                exporter_kwargs["headers"] = config.headers
            otlp_exporter = OTLPSpanExporter(**exporter_kwargs)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            exporter_installed = True
        except ImportError:
            logger.warning("OTLP HTTP exporter not installed. Run: pip install voicecheck[otel]")
        except Exception as e:
            logger.warning("Failed to install OTLP exporter: %s", e)

    if not exporter_installed:
        return False

    # Don't override an existing global provider — respect the host app
    # if it already wired up its own (e.g. when voicecheck is embedded).
    existing = trace.get_tracer_provider()
    if existing.__class__.__name__ != "ProxyTracerProvider":
        logger.debug(
            "Global TracerProvider already set (%s); attaching processors there too",
            existing.__class__.__name__,
        )
        # We can't merge cleanly — fall back to using the existing provider
        # and skip our own exporter wiring. That avoids spans going nowhere.
        _PROVIDER = existing
        _ENABLED = True
        return True

    trace.set_tracer_provider(provider)
    _PROVIDER = provider
    _ENABLED = True
    logger.info(
        "voicecheck observability initialized: service=%s console=%s endpoint=%s",
        config.service_name,
        config.console,
        config.endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "(none)",
    )
    return True


def shutdown_tracing(timeout_s: float = 5.0) -> None:
    """Flush pending spans and shut down the provider.

    Always safe to call. The CLI calls this in a ``finally`` block so
    a Ctrl-C still flushes whatever spans we have in the batch.
    """
    global _PROVIDER, _ENABLED

    if _PROVIDER is None:
        return

    try:
        # ``shutdown`` exists on the SDK provider; the proxy has no such method.
        shutdown = getattr(_PROVIDER, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception as e:
        logger.debug("Tracer provider shutdown raised: %s", e)
    finally:
        _PROVIDER = None
        _ENABLED = False
