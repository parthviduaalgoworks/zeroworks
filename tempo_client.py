"""Tempo Cloud worklog API client and local credential helpers."""

from datetime import date
from http.client import HTTPException
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd

from utils import normalize_worklogs


DEFAULT_BASE_URL = "https://api.tempo.io"
US_BASE_URL = "https://api.us.tempo.io"
PAGE_SIZE = 1000
MAX_PAGES = 100
PROJECT_ROOT = Path(__file__).resolve().parent
CREDENTIALS_FILE = PROJECT_ROOT / ".tempo_credentials.json"
DEFAULT_WORKER_ID = "712020:2e6bdb19-a84b-4822-a7fc-4d549ae2e565"


def load_tempo_credentials() -> dict[str, str]:
    """Load a saved token, preferring the environment variable."""
    credentials: dict[str, str] = {}
    if CREDENTIALS_FILE.exists():
        try:
            loaded = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                credentials = {
                    str(key): str(value)
                    for key, value in loaded.items()
                    if value is not None
                }
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read {CREDENTIALS_FILE.name}: {exc}") from exc
    token = os.getenv("TEMPO_API_TOKEN", "").strip() or credentials.get("token", "").strip()
    base_url = os.getenv("TEMPO_API_BASE_URL", "").strip() or credentials.get(
        "base_url", DEFAULT_BASE_URL
    )
    worker_id = os.getenv("TEMPO_WORKER_ID", "").strip() or credentials.get(
        "worker_id", DEFAULT_WORKER_ID
    ).strip()
    if token:
        return {
            "token": token,
            "base_url": _normalize_base_url(base_url),
            "worker_id": worker_id,
        }
    return {
        "token": "",
        "base_url": _normalize_base_url(base_url),
        "worker_id": worker_id,
    }


def save_tempo_credentials(token: str, base_url: str, worker_id: str) -> None:
    """Save API settings locally for the Friday task, without tracking the file."""
    clean_token = token.strip()
    if not clean_token:
        raise ValueError("Enter a Tempo API token before saving.")
    clean_worker_id = worker_id.strip()
    if not clean_worker_id:
        raise ValueError("Enter the Tempo worker ID from the report URL.")
    CREDENTIALS_FILE.write_text(
        json.dumps(
            {
                "token": clean_token,
                "base_url": _normalize_base_url(base_url),
                "worker_id": clean_worker_id,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized not in {DEFAULT_BASE_URL, US_BASE_URL}:
        raise ValueError("Choose a supported Tempo API cluster.")
    return normalized


def _authorization_value(token: str) -> str:
    clean_token = token.strip()
    return clean_token if clean_token.lower().startswith("bearer ") else f"Bearer {clean_token}"


def fetch_tempo_worklogs(
    token: str,
    start_date: date,
    end_date: date,
    base_url: str = DEFAULT_BASE_URL,
    worker_id: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch Tempo worklogs in a date range, optionally restricted to one worker."""
    if not token.strip():
        raise ValueError("A Tempo API token is required.")
    if start_date > end_date:
        raise ValueError("The start date must be on or before the end date.")
    base = _normalize_base_url(base_url)
    if worker_id is not None and not worker_id.strip():
        raise ValueError("Worker ID cannot be blank.")
    endpoint = (
        f"{base}/4/worklogs/user/{quote(worker_id.strip(), safe='')}"
        if worker_id
        else f"{base}/4/worklogs"
    )
    headers = {
        "Authorization": _authorization_value(token),
        "Accept": "application/json",
        "User-Agent": "AI-Timesheet-Billing-Copilot/1.0",
    }
    records: list[dict[str, Any]] = []
    offset = 0

    for _ in range(MAX_PAGES):
        query = urlencode(
            {
                "from": start_date.isoformat(),
                "to": end_date.isoformat(),
                "limit": PAGE_SIZE,
                "offset": offset,
            }
        )
        request = Request(f"{endpoint}?{query}", headers=headers)
        for attempt in range(4):
            try:
                with urlopen(request, timeout=45) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt < 3:
                        try:
                            delay = min(float(exc.headers.get("Retry-After", 2**attempt)), 60)
                        except (TypeError, ValueError):
                            delay = min(2**attempt, 60)
                        time.sleep(delay)
                        continue
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(
                    f"Tempo API returned HTTP {exc.code}: {detail or exc.reason}"
                ) from exc
            except (URLError, HTTPException, TimeoutError, OSError) as exc:
                raise RuntimeError(f"Could not connect to Tempo API: {exc}") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Tempo API returned an invalid JSON response.") from exc

        if isinstance(payload, list):
            page = payload
        elif isinstance(payload, dict):
            page = payload.get("results")
            if page is None:
                page = payload.get("worklogs")
            if not isinstance(page, list):
                raise RuntimeError(
                    "Tempo API response did not contain a worklog results list."
                )
        else:
            raise RuntimeError("Tempo API returned an unsupported response format.")

        if any(not isinstance(item, dict) for item in page):
            raise RuntimeError("Tempo API returned a malformed worklog entry.")
        records.extend(page)
        if len(page) < PAGE_SIZE:
            return records
        offset += len(page)
        time.sleep(0.15)

    raise RuntimeError(
        f"Tempo returned more than {MAX_PAGES * PAGE_SIZE:,} records; narrow the date range."
    )


def tempo_records_to_csv(records: list[dict[str, Any]]) -> bytes:
    """Flatten raw Tempo API worklogs into a downloadable CSV."""
    if not records:
        return b""
    frame = pd.json_normalize(records, sep=".")
    return frame.to_csv(index=False).encode("utf-8-sig")


def normalize_tempo_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert Tempo API objects to the dashboard's normalized worklog shape."""
    if not records:
        raise ValueError("Tempo returned no worklogs for the selected date range.")
    flattened = pd.json_normalize(records, sep=".")
    if flattened.empty:
        raise ValueError("Tempo returned no readable worklogs.")
    columns = set(flattened.columns)
    if "author.displayName" not in columns and "author.accountId" in columns:
        flattened["resource"] = flattened["author.accountId"].map(
            lambda value: f"Tempo user {value}" if pd.notna(value) else "Unknown resource"
        )
    if not columns.intersection(
        {"project", "project.name", "project.key", "issue.key", "space.name", "space.key"}
    ):
        if "issue.id" in columns:
            flattened["project"] = flattened["issue.id"].map(
                lambda value: f"Issue ID {value}" if pd.notna(value) else "Unknown issue"
            )
            flattened["work item summary"] = flattened["project"]
        elif "issue.self" in columns:
            flattened["project"] = "Tempo issue"
            flattened["work item summary"] = "Tempo issue"
    return normalize_worklogs(flattened)


def save_tempo_export(
    records: list[dict[str, Any]], start_date: date, end_date: date
) -> Path:
    """Write a scheduled Tempo CSV into the local exports folder."""
    if not records:
        raise ValueError("Tempo returned no worklogs; no CSV was created.")
    export_dir = PROJECT_ROOT / "tempo_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / (
        f"tempo_worklogs_{start_date.isoformat()}_to_{end_date.isoformat()}.csv"
    )
    path.write_bytes(tempo_records_to_csv(records))
    return path
