import logging
import sys
from typing import Literal

import structlog
from structlog.types import Processor

LogFormat = Literal["console", "json"]
_configured = False


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def configure_logging(
    *,
    log_level: str = "INFO",
    log_format: LogFormat = "console",
) -> None:
    global _configured

    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors = _shared_processors()
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    _configured = True


def _ensure_configured() -> None:
    if _configured:
        return
    from config import settings

    configure_logging(
        log_level=settings.log_level,
        log_format=settings.log_format,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    _ensure_configured()
    return structlog.get_logger(name)
