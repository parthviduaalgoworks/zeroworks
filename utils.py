"""Tempo worklog parsing, normalization, gap detection, and report exports."""

from datetime import date, timedelta
from io import BytesIO
import math
import re
import zipfile
from typing import BinaryIO

import pandas as pd


COLUMN_ALIASES = {
    "date": ("date", "work date", "worklog date", "started", "start date", "startdate"),
    "resource": (
        "resource",
        "employee",
        "employee name",
        "full name",
        "display name",
        "displayname",
        "account id",
        "accountid",
        "worker",
        "author",
        "user",
        "username",
        "account name",
    ),
    "project": (
        "project",
        "project name",
        "project key",
        "client project",
        "space name",
        "space key",
        "activity name",
        "account name",
    ),
    "work_item": (
        "work item summary",
        "work item",
        "issue summary",
        "task summary",
    ),
    "description": (
        "description",
        "work description",
        "worklog description",
        "worklog",
        "comment",
        "work item",
    ),
    "hours": (
        "hours",
        "time spent",
        "time logged",
        "logged time",
        "duration",
        "logged hours",
        "time spent seconds",
        "time",
    ),
    "billable_hours": ("billable hours",),
    "billable_seconds": ("billable seconds",),
    "billable": ("billable", "is billable", "billing status", "billable status"),
}


def _normalized_header(value: object) -> str:
    header = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value).strip())
    return re.sub(r"[^a-z0-9]+", " ", header.lower()).strip()


def _find_column(
    columns: list[object], field: str, *, exact_only: bool = False
) -> object | None:
    normalized = {_normalized_header(column): column for column in columns}
    for alias in COLUMN_ALIASES[field]:
        if alias in normalized:
            return normalized[alias]
    if exact_only:
        return None
    for header, original in normalized.items():
        if any(alias in header for alias in COLUMN_ALIASES[field] if len(alias) > 4):
            return original
    if field == "project":
        return normalized.get("issue key")
    return None


def _parse_hours(value: object, header: object) -> float:
    if pd.isna(value):
        return float("nan")
    if isinstance(value, (int, float)):
        number = float(value)
        header_name = _normalized_header(header)
        parsed = number / 3600 if "second" in header_name else number
        return parsed if math.isfinite(parsed) else float("nan")

    text = str(value).strip().lower()
    if not text:
        return float("nan")
    try:
        number = float(text.replace(",", ""))
        parsed = number / 3600 if "second" in _normalized_header(header) else number
        return parsed if math.isfinite(parsed) else float("nan")
    except ValueError:
        pass

    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(d|h|m|s)\b", text)
    if matches:
        multipliers = {"d": 8.0, "h": 1.0, "m": 1 / 60, "s": 1 / 3600}
        return sum(float(amount) * multipliers[unit] for amount, unit in matches)
    return float("nan")


def normalize_worklogs(raw: pd.DataFrame) -> pd.DataFrame:
    """Map common Tempo export headers into a consistent worklog schema."""
    if raw.empty:
        raise ValueError("The selected file contains no worklog rows.")

    columns = list(raw.columns)
    mapped = {
        field: _find_column(columns, field, exact_only=field == "billable")
        for field in COLUMN_ALIASES
    }
    if mapped["date"] is not None:
        total_rows = raw[mapped["date"]].astype(str).str.strip().str.casefold().eq("total")
        raw = raw.loc[~total_rows].copy()
    if mapped["description"] is not None and mapped["hours"] is not None:
        weekend_rows = (
            raw[mapped["description"]]
            .astype(str)
            .str.strip()
            .str.casefold()
            .eq("weekend")
            & raw[mapped["hours"]].map(
                lambda value: _parse_hours(value, mapped["hours"]) == 0
            )
        )
        raw = raw.loc[~weekend_rows].copy()
    columns = list(raw.columns)
    mapped = {
        field: _find_column(columns, field, exact_only=field == "billable")
        for field in COLUMN_ALIASES
    }
    if mapped["hours"] is None:
        raise ValueError(
            "Could not find a time-spent column. Expected a header such as "
            "'Time Spent', 'Hours', or 'Duration'."
        )

    result = pd.DataFrame(index=raw.index)
    result["date"] = (
        pd.to_datetime(raw[mapped["date"]], errors="coerce", utc=True)
        .dt.tz_convert(None)
        .dt.normalize()
        if mapped["date"] is not None
        else pd.NaT
    )
    result["resource"] = (
        raw[mapped["resource"]].fillna("Unknown resource").astype(str).str.strip()
        if mapped["resource"] is not None
        else "Unknown resource"
    )
    result["project"] = (
        raw[mapped["project"]].fillna("Unassigned").astype(str).str.strip()
        if mapped["project"] is not None
        else "Unassigned"
    )
    descriptions = (
        raw[mapped["description"]].fillna("").astype(str).str.strip()
        if mapped["description"] is not None
        else pd.Series("", index=raw.index, dtype=str)
    )
    result["work_item"] = (
        raw[mapped["work_item"]].fillna("").astype(str).str.strip()
        if mapped["work_item"] is not None
        else pd.Series("", index=raw.index, dtype=str)
    )
    result["description"] = (
        result["work_item"].where(
            descriptions.eq(""), descriptions
        )
    )
    both_descriptions = descriptions.ne("") & result["work_item"].ne("") & descriptions.ne(result["work_item"])
    result.loc[both_descriptions, "description"] = (
        result.loc[both_descriptions, "work_item"]
        + " — "
        + descriptions.loc[both_descriptions]
    )
    result["hours"] = raw.apply(
        lambda row: _parse_hours(row[mapped["hours"]], mapped["hours"]), axis=1
    )
    leave_labels = (
        result["project"].astype(str)
        + " "
        + result["work_item"].astype(str)
        + " "
        + result["description"].astype(str)
    )
    is_holiday = leave_labels.str.contains(
        r"\bpublic\s+holidays?\b|\bholidays?\b", case=False, regex=True
    )
    leave_label_pattern = (
        r"\b(?:annual|casual|sick|medical|paternity|maternity|parental|earned|"
        r"privilege|paid|unpaid|personal|bereavement|compensatory)\s+leaves?\b"
        r"|\bpaid\s+time\s+off\b|\bpto\b|\bvacation\b|\bleave\s+of\s+absence\b"
        r"|\bautomated\s+applied\s+leaves?\b"
    )
    is_leave_label = leave_labels.str.contains(
        leave_label_pattern, case=False, regex=True
    )
    is_time_off_project = result["project"].str.contains(
        r"\bTIME\s*OFF\b|\bTIMEOFF\b", case=False, regex=True
    )
    result["leave_type"] = ""
    result.loc[is_time_off_project, "leave_type"] = "Time Off"
    result.loc[is_leave_label, "leave_type"] = "Leave"
    result.loc[is_holiday, "leave_type"] = "Public Holiday"
    result["is_leave"] = is_time_off_project | is_leave_label | is_holiday

    if mapped["billable_hours"] is not None:
        billable_hours = raw.apply(
            lambda row: _parse_hours(
                row[mapped["billable_hours"]], mapped["billable_hours"]
            ),
            axis=1,
        )
    elif mapped["billable_seconds"] is not None:
        billable_hours = raw.apply(
            lambda row: _parse_hours(
                row[mapped["billable_seconds"]], mapped["billable_seconds"]
            ),
            axis=1,
        )
    elif mapped["billable"] is not None:
        raw_billable = raw[mapped["billable"]].fillna("").astype(str).str.strip().str.lower()
        result["billable"] = raw_billable.isin(
            {"true", "yes", "y", "1", "billable", "billable work"}
        ) & ~result["is_leave"]
        billable_hours = result["hours"].where(result["billable"], 0)
    else:
        billable_hours = result["hours"].where(~result["is_leave"], 0)
    if mapped["billable_hours"] is not None or mapped["billable_seconds"] is not None:
        if billable_hours.isna().any():
            raise ValueError("One or more billable-time values could not be interpreted.")
        if (billable_hours < 0).any() or (billable_hours > result["hours"]).any():
            raise ValueError("Billable time must be between zero and logged time.")
    billable_hours = billable_hours.where(~result["is_leave"], 0)
    result["billable_hours"] = billable_hours
    result["billable"] = (billable_hours > 0).astype(bool)
    result.loc[result["description"] == "", "description"] = "No description provided"
    result.loc[result["resource"] == "", "resource"] = "Unknown resource"
    result.loc[result["project"] == "", "project"] = "Unassigned"

    if (result["hours"] < 0).any():
        raise ValueError("Time spent cannot be negative.")
    if result["hours"].isna().any():
        raise ValueError("One or more time-spent values could not be interpreted.")
    return result.reset_index(drop=True)


def load_timesheet(uploaded_file: BinaryIO) -> pd.DataFrame:
    """Read a CSV or Excel upload and normalize the worklogs."""
    name = getattr(uploaded_file, "name", "").lower()
    try:
        if name.endswith((".xlsx", ".xls")):
            sheets = pd.read_excel(
                uploaded_file,
                engine="xlrd" if name.endswith(".xls") else "openpyxl",
                sheet_name=None,
            )
            raw = pd.concat(
                [
                    sheet.assign(
                        **{
                            "Workbook Sheet": sheet_name,
                        }
                    )
                    for sheet_name, sheet in sheets.items()
                    if not sheet.empty
                ],
                ignore_index=True,
            )
        else:
            try:
                raw = pd.read_csv(uploaded_file, sep=None, engine="python")
            except UnicodeDecodeError:
                uploaded_file.seek(0)
                raw = pd.read_csv(uploaded_file, sep=None, engine="python", encoding="latin-1")
    except ImportError as exc:
        engine = "xlrd for .xls files or openpyxl for .xlsx files"
        raise ImportError(f"Excel support requires {engine}. Install requirements.txt.") from exc
    except (ValueError, OSError, pd.errors.ParserError, zipfile.BadZipFile, EOFError) as exc:
        raise ValueError(f"Unsupported or malformed file: {exc}") from exc
    return normalize_worklogs(raw)


def summarize_worklogs(worklogs: pd.DataFrame) -> pd.DataFrame:
    """Aggregate hours by project and resource for the overview table."""
    billable_worklogs = worklogs.assign(
        billable_hours=worklogs["billable_hours"]
    )
    return (
        billable_worklogs.groupby(["project", "resource"], as_index=False)
        .agg(
            hours=("hours", "sum"),
            billable_hours=("billable_hours", "sum"),
        )
        .rename(
            columns={
                "project": "Project",
                "resource": "Resource",
                "hours": "Logged Hours",
                "billable_hours": "Billable Hours",
            }
        )
        .sort_values(["Project", "Resource"])
        .reset_index(drop=True)
    )


def detect_missing_timesheets(
    worklogs: pd.DataFrame,
    week_ending: date,
    public_holidays: set[date] | None = None,
) -> pd.DataFrame:
    """Find missing workdays, excluding weekends, configured holidays, and leave."""
    if week_ending.weekday() != 4:
        raise ValueError("The selected week-ending date must be a Friday.")
    if not worklogs["date"].notna().any():
        return pd.DataFrame(columns=["resource", "missing_days", "missing_count"])

    monday = week_ending - timedelta(days=4)
    holidays = public_holidays or set()
    weekdays = [
        monday + timedelta(days=offset)
        for offset in range(5)
        if monday + timedelta(days=offset) not in holidays
    ]
    dated = worklogs.loc[worklogs["date"].notna()].copy()
    dated["date_only"] = dated["date"].dt.date
    week = dated.loc[dated["date_only"].between(monday, week_ending)]
    resources = sorted(worklogs["resource"].dropna().unique().tolist())
    missing_rows = []

    for resource in resources:
        missing = []
        resource_week = week.loc[week["resource"] == resource]
        for weekday in weekdays:
            day_entries = resource_week.loc[resource_week["date_only"] == weekday]
            if day_entries["is_leave"].any():
                continue
            logged_hours = day_entries.loc[~day_entries["is_leave"], "hours"].sum()
            if logged_hours <= 0:
                missing.append(weekday.strftime("%a %b %d"))
        if missing:
            missing_rows.append(
                {
                    "resource": resource,
                    "missing_days": missing,
                    "missing_count": len(missing),
                }
            )
    return pd.DataFrame(
        missing_rows, columns=["resource", "missing_days", "missing_count"]
    )


def make_demo_timesheet() -> pd.DataFrame:
    """Create a small, realistic Tempo-style sample for an instant demo."""
    today = date.today()
    friday = today - timedelta(days=(today.weekday() - 4) % 7)
    monday = friday - timedelta(days=4)
    rows = [
        (monday, "Avery Chen", "WEB", "Worked on https://jira.example.com/browse/WEB-2672 fixed responsive nav", 4.0, True),
        (monday, "Avery Chen", "WEB", "Updated checkout validation and added WEB-2690 regression tests", 4.0, True),
        (monday, "Jordan Lee", "MOBILE", "Investigated crash on https://example.com/build/123 and fixed MOB-882", 7.5, True),
        (monday, "Sam Rivera", "WEB", "TIMEOFF - annual leave", 8.0, False),
        (monday + timedelta(days=1), "Avery Chen", "WEB", "Cross-browser QA for WEB-2690", 7.5, True),
        (monday + timedelta(days=1), "Jordan Lee", "MOBILE", "Refined offline sync and reviewed MOB-901", 8.0, True),
        (monday + timedelta(days=1), "Sam Rivera", "WEB", "Reviewed accessibility findings and contrast fixes", 8.0, True),
        (monday + timedelta(days=2), "Avery Chen", "WEB", "Fixed keyboard navigation https://jira.example.com/WEB-2701", 8.0, True),
        (monday + timedelta(days=2), "Jordan Lee", "MOBILE", "MOB-912: improved retry handling", 7.0, True),
        (monday + timedelta(days=2), "Sam Rivera", "WEB", "Built analytics events for the onboarding funnel", 8.0, True),
        (monday + timedelta(days=3), "Avery Chen", "WEB", "Validated checkout on tablet and mobile breakpoints", 8.0, True),
        (monday + timedelta(days=3), "Jordan Lee", "MOBILE", "Updated API error states and release notes", 8.0, True),
        (monday + timedelta(days=3), "Sam Rivera", "WEB", "TIMEOFF - medical leave", 8.0, False),
        (friday, "Avery Chen", "WEB", "Final smoke testing and handoff notes", 6.0, True),
        (friday, "Sam Rivera", "WEB", "Pairing session for the deployment checklist", 4.0, True),
    ]
    raw = pd.DataFrame(
        rows,
        columns=["Work Date", "Resource", "Project", "Description", "Time Spent", "Billable"],
    )
    return normalize_worklogs(raw)


def build_invoice_workbook(
    worklogs: pd.DataFrame,
    summaries: pd.DataFrame,
    project: str,
    client_name: str,
    hourly_rate: float,
) -> tuple[bytes, bytes]:
    """Build invoice/report downloads as Excel workbook bytes and CSV bytes."""
    lines = summaries.loc[summaries["Project"] == project].copy()
    lines["Hourly Rate"] = hourly_rate
    lines["Amount"] = lines["Billable Hours"] * hourly_rate
    lines = lines.loc[
        :, ["Project", "Resource", "Client Summary", "Billable Hours", "Hourly Rate", "Amount"]
    ]
    project_logs = worklogs.loc[
        (worklogs["project"] == project) & ~worklogs["is_leave"]
    ].copy()
    dates = project_logs["date"].dropna()
    date_range = (
        f"{dates.min().strftime('%Y-%m-%d')} to {dates.max().strftime('%Y-%m-%d')}"
        if not dates.empty
        else "Not available"
    )
    billable_hours = float(lines["Billable Hours"].sum())
    invoice_summary = pd.DataFrame(
        [
            {"Field": "Client", "Value": client_name},
            {"Field": "Project", "Value": project},
            {"Field": "Reporting period", "Value": date_range},
            {"Field": "Billable hours", "Value": billable_hours},
            {"Field": "Hourly rate", "Value": hourly_rate},
            {"Field": "Draft total", "Value": billable_hours * hourly_rate},
            {"Field": "Status", "Value": "DRAFT — for review"},
        ]
    )
    details = project_logs.loc[
        :, ["date", "resource", "project", "work_item", "hours", "billable_hours", "billable"]
    ].rename(
        columns={
            "date": "Work Date",
            "resource": "Resource",
            "project": "Project",
            "work_item": "Work Item",
            "hours": "Logged Hours",
            "billable_hours": "Billable Hours",
            "billable": "Billable",
        }
    )
    details = details.merge(
        summaries.loc[:, ["Project", "Resource", "Client Summary"]],
        on=["Project", "Resource"],
        how="left",
    ).loc[
        :,
        [
            "Work Date",
            "Resource",
            "Project",
            "Work Item",
            "Logged Hours",
            "Billable Hours",
            "Billable",
            "Client Summary",
        ],
    ]
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        invoice_summary.to_excel(writer, sheet_name="Invoice Summary", index=False)
        lines.to_excel(writer, sheet_name="Client Line Items", index=False)
        details.to_excel(writer, sheet_name="Timesheet Detail", index=False)
    return output.getvalue(), lines.to_csv(index=False).encode("utf-8-sig")
