"""Fetch the current Tempo workweek and save a raw CSV for Task Scheduler."""

from datetime import date, timedelta
import sys
import traceback

from tempo_client import fetch_tempo_worklogs, load_tempo_credentials, save_tempo_export


def current_workweek() -> tuple[date, date]:
    today = date.today()
    friday = today - timedelta(days=(today.weekday() - 4) % 7)
    start_date = friday - timedelta(days=friday.weekday())
    return start_date, start_date + timedelta(days=6)


def main() -> int:
    try:
        credentials = load_tempo_credentials()
        if not credentials["token"]:
            raise ValueError(
                "No Tempo API token found. Save one from the app or set TEMPO_API_TOKEN."
            )
        if not credentials["worker_id"]:
            raise ValueError(
                "No Tempo worker ID found. Save the workerId from the report URL."
            )
        start_date, end_date = current_workweek()
        records = fetch_tempo_worklogs(
            credentials["token"],
            start_date,
            end_date,
            base_url=credentials["base_url"],
            worker_id=credentials["worker_id"],
        )
        export_path = save_tempo_export(records, start_date, end_date)
        print(f"Saved {len(records):,} Tempo worklogs to {export_path}")
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Tempo scheduled export failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
