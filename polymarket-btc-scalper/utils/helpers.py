"""General utility helpers."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

import config

logger = logging.getLogger(__name__)


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ts_to_dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def dt_to_ts(dt: datetime) -> int:
    return int(dt.timestamp())


def get_with_retry(
    url: str,
    params: Optional[dict] = None,
    retries: int = config.HTTP_RETRIES,
    backoff: float = config.HTTP_BACKOFF,
    timeout: int = config.HTTP_TIMEOUT,
) -> Optional[Any]:
    """GET *url* with exponential back-off retries. Returns parsed JSON or None."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                wait = backoff * (2 ** attempt)
                logger.warning("Rate-limited by %s – sleeping %ss", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            wait = backoff * (2 ** attempt)
            logger.warning(
                "HTTP error on attempt %d/%d for %s: %s – retrying in %ss",
                attempt, retries, url, exc, wait,
            )
            if attempt < retries:
                time.sleep(wait)
    logger.error("All %d attempts failed for %s", retries, url)
    return None


def post_with_retry(
    url: str,
    json_body: dict,
    retries: int = config.HTTP_RETRIES,
    backoff: float = config.HTTP_BACKOFF,
    timeout: int = config.HTTP_TIMEOUT,
) -> Optional[Any]:
    """POST *url* with exponential back-off retries."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=json_body, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            wait = backoff * (2 ** attempt)
            logger.warning(
                "POST error on attempt %d/%d for %s: %s – retrying in %ss",
                attempt, retries, url, exc, wait,
            )
            if attempt < retries:
                time.sleep(wait)
    logger.error("All %d POST attempts failed for %s", retries, url)
    return None


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))
