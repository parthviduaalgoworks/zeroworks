"""AI Timesheet & Client Billing Copilot — Streamlit prototype."""

from datetime import date, timedelta
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import streamlit as st

from ai_agent import analyze_worklogs, generate_client_summaries, generate_reminder
from email_sender import smtp_settings_from_environment, send_reminder_email
from utils import (
    build_invoice_workbook,
    detect_missing_timesheets,
    load_timesheet,
    make_demo_timesheet,
    summarize_worklogs,
)
from tempo_client import (
    DEFAULT_BASE_URL,
    DEFAULT_WORKER_ID,
    US_BASE_URL,
    fetch_tempo_worklogs,
    load_tempo_credentials,
    normalize_tempo_records,
    save_tempo_credentials,
    tempo_records_to_csv,
)


st.set_page_config(
    page_title="AI Timesheet & Billing Copilot",
    page_icon="⏱️",
    layout="wide",
)

st.markdown(
    """
    <style>
    .stApp { background: linear-gradient(135deg, #f6f8fc 0%, #edf3fb 100%); }
    [data-testid="stMetric"] {
        background: white; border: 1px solid #e5eaf2; padding: 16px 18px;
        border-radius: 14px; box-shadow: 0 4px 16px rgba(24, 39, 75, .05);
    }
    .hero {
        padding: 22px 26px; border-radius: 18px; color: white; margin-bottom: 18px;
        background: linear-gradient(110deg, #182b59 0%, #3159a8 58%, #547ee8 100%);
    }
    .hero p { margin-bottom: 0; color: #e2eaff; }
    </style>
    """,
    unsafe_allow_html=True,
)


def display_hours(value: float) -> str:
    return f"{value:,.1f} h"


st.markdown(
    """
    <div class="hero">
      <h1>⏱️ AI Timesheet &amp; Client Billing Copilot</h1>
      <p>Turn raw Tempo worklogs into client-ready updates, proactive nudges, and invoice drafts.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Workspace")
    uploaded_file = st.file_uploader(
        "Upload a Tempo export",
        type=["csv", "xlsx", "xls"],
        help="CSV or Excel worklogs with dates, resources, projects, descriptions, and time spent.",
    )
    use_demo = st.toggle("Use interactive demo data", value=uploaded_file is None)
    holiday_input = st.text_input(
        "Additional public holidays (YYYY-MM-DD, comma-separated)",
        help="Optional extra dates. Holidays and leaves labeled in the uploaded Tempo/Keka worklogs are detected automatically.",
    )
    st.divider()
    st.subheader("Tempo Cloud connection")
    try:
        saved_tempo_credentials = load_tempo_credentials()
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    tempo_region_labels = {
        "Global / other regions": DEFAULT_BASE_URL,
        "Americas": US_BASE_URL,
    }
    saved_region_index = (
        1 if saved_tempo_credentials["base_url"] == US_BASE_URL else 0
    )
    tempo_region = st.selectbox(
        "Tempo API region",
        list(tempo_region_labels),
        index=saved_region_index,
        help="Choose the region where your Tempo Cloud data is hosted.",
    )
    tempo_base_url = tempo_region_labels[tempo_region]
    tempo_token_input = st.text_input(
        "Tempo API token",
        type="password",
        help="Generated in Tempo Settings → Data Access → API integration.",
    )
    tempo_worker_id = st.text_input(
        "Tempo worker ID",
        value=saved_tempo_credentials.get("worker_id", DEFAULT_WORKER_ID),
        help="The workerId value in the Tempo timesheet URL. This integration downloads only this worker's entries.",
    )
    save_token_col, task_col = st.columns(2)
    with save_token_col:
        if st.button("Save token locally", key="save_tempo_token"):
            try:
                save_tempo_credentials(
                    tempo_token_input, tempo_base_url, tempo_worker_id
                )
                saved_tempo_credentials = {
                    "token": tempo_token_input.strip(),
                    "base_url": tempo_base_url,
                    "worker_id": tempo_worker_id.strip(),
                }
                st.success("Token saved in a local git-ignored credentials file.")
            except (OSError, ValueError) as exc:
                st.error(f"Could not save Tempo credentials: {exc}")
    if saved_tempo_credentials["token"]:
        st.caption(
            f"On-demand and Friday exports are filtered to worker {saved_tempo_credentials['worker_id']}."
        )
    else:
        st.caption("Save a token locally to enable the recurring Friday task.")

    if os.name == "nt" and saved_tempo_credentials["token"]:
        with task_col:
            if st.button("Enable Friday 5 PM export", key="register_tempo_task"):
                setup_script = Path(__file__).with_name("setup_tempo_friday_task.ps1")
                try:
                    result = subprocess.run(
                        [
                            "powershell.exe",
                            "-NoProfile",
                            "-File",
                            str(setup_script),
                            "-PythonExecutable",
                            sys.executable,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=30,
                    )
                    if result.returncode == 0:
                        st.success(result.stdout.strip() or "Friday export task enabled.")
                    else:
                        st.error(
                            "Could not register the Windows task: "
                            + (result.stderr.strip() or result.stdout.strip())
                        )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    st.error(f"Could not run the task setup script: {exc}")

    st.divider()
    st.subheader("Reminder email (Microsoft 365 SMTP)")
    try:
        smtp_defaults = smtp_settings_from_environment()
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    smtp_host = st.text_input(
        "SMTP server",
        value=str(smtp_defaults["host"]),
        key="smtp_host",
    )
    smtp_port = st.number_input(
        "SMTP port (STARTTLS)",
        min_value=1,
        max_value=65535,
        value=int(smtp_defaults["port"]),
        step=1,
        key="smtp_port",
    )
    sender_email = st.text_input(
        "From email",
        value=str(smtp_defaults["sender"]),
        key="smtp_sender",
        help="If blank, the SMTP username is used as the sender address.",
    )
    smtp_username = st.text_input(
        "SMTP username",
        value=str(smtp_defaults["username"]),
        key="smtp_username",
    )
    smtp_password = st.text_input(
        "SMTP password / app password",
        type="password",
        value=str(smtp_defaults["password"]),
        key="smtp_password",
        help="Held only in app memory when entered here; it is not written to the local Tempo credentials file.",
    )
    st.caption(
        "Microsoft 365 SMTP AUTH must be enabled for this mailbox and tenant. "
        "You may also set SMTP_HOST, SMTP_PORT, SMTP_FROM, SMTP_USERNAME, and SMTP_PASSWORD."
    )

    st.caption(
        "Friday automation uses Windows Task Scheduler at 5:00 PM system-local time. "
        "Keep this PC awake and signed in; missed runs start when it is available."
    )

    today = date.today()
    default_from = today - timedelta(days=today.weekday())
    tempo_start_date = st.date_input(
        "Tempo export from",
        value=default_from,
        key="tempo_export_from",
    )
    tempo_end_date = st.date_input(
        "Tempo export to",
        value=default_from + timedelta(days=6),
        key="tempo_export_to",
    )
    if st.button("⬇️ Fetch worklogs from Tempo", type="primary", key="fetch_tempo"):
        active_token = tempo_token_input.strip() or saved_tempo_credentials["token"]
        try:
            with st.spinner("Fetching Tempo worklogs…"):
                tempo_records = fetch_tempo_worklogs(
                    active_token,
                    tempo_start_date,
                    tempo_end_date,
                    base_url=tempo_base_url,
                    worker_id=tempo_worker_id.strip()
                    or saved_tempo_credentials.get("worker_id"),
                )
                tempo_worklogs = normalize_tempo_records(tempo_records)
                tempo_csv = tempo_records_to_csv(tempo_records)
            st.session_state["tempo_records"] = tempo_records
            st.session_state["tempo_worklogs"] = tempo_worklogs
            st.session_state["tempo_csv"] = tempo_csv
            st.session_state["tempo_export_range"] = (
                tempo_start_date,
                tempo_end_date,
            )
            st.session_state["source_choice"] = "Tempo API"
            st.success(f"Fetched {len(tempo_records):,} worklogs from Tempo.")
        except (RuntimeError, ValueError, OSError) as exc:
            st.error(f"Tempo export failed: {exc}")

    if st.session_state.get("tempo_records"):
        exported_from, exported_to = st.session_state["tempo_export_range"]
        st.download_button(
            f"Download raw Tempo CSV ({exported_from} to {exported_to})",
            data=st.session_state["tempo_csv"],
            file_name=f"tempo_worklogs_{exported_from}_to_{exported_to}.csv",
            mime="text/csv",
            key="download_tempo_csv",
        )

    st.divider()
    st.subheader("AI polish")
    api_key = st.text_input(
        "Gemini API key",
        type="password",
        value=os.getenv("GEMINI_API_KEY", ""),
        help="Optional. Without a key, local cleanup removes URLs and ticket keys.",
    )
    st.caption("AI uses Gemini when configured; otherwise the report clearly labels local cleanup.")

try:
    public_holidays = {
        date.fromisoformat(value.strip())
        for value in holiday_input.split(",")
        if value.strip()
    }
except ValueError as exc:
    st.error(f"Invalid public holiday date. Use YYYY-MM-DD: {exc}")
    st.stop()

worklogs = None
tempo_worklogs = st.session_state.get("tempo_worklogs")
source_options = []
if tempo_worklogs is not None:
    source_options.append("Tempo API")
if uploaded_file is not None:
    source_options.append("Uploaded file")
if len(source_options) > 1:
    if st.session_state.get("source_choice") not in source_options:
        st.session_state["source_choice"] = source_options[0]
    selected_source = st.sidebar.radio(
        "Dashboard data source",
        source_options,
        key="source_choice",
    )
elif source_options:
    selected_source = source_options[0]
else:
    selected_source = None

if selected_source == "Tempo API":
    worklogs = tempo_worklogs
    st.sidebar.success(f"Using {len(worklogs):,} Tempo API worklogs")
    st.caption(
        "Tempo API results are restricted to the configured worker. The raw CSV keeps the API fields; "
        "Tempo's API may identify authors/issues by ID where its response does not include display names."
    )
elif selected_source == "Uploaded file" and uploaded_file is not None:
    try:
        worklogs = load_timesheet(uploaded_file)
        st.sidebar.success(f"Loaded {len(worklogs):,} worklog rows")
    except (ValueError, ImportError, OSError) as exc:
        st.error(f"Could not read the uploaded timesheet: {exc}")
        st.stop()
elif use_demo:
    worklogs = make_demo_timesheet()
    st.sidebar.info("Showing a sample Tempo-style dataset.")
else:
    st.info("Upload a Tempo CSV/Excel export or enable demo data to get started.")
    st.stop()

if worklogs.empty:
    st.warning("The file was read successfully, but it contains no usable worklog rows.")
    st.stop()

summary = summarize_worklogs(worklogs)
fingerprint = int(pd.util.hash_pandas_object(worklogs, index=True).sum())
if st.session_state.get("data_fingerprint") != fingerprint:
    for state_key in (
        "client_summaries",
        "ai_status",
        "ai_error",
        "analysis_result",
        "invoice_result",
        "invoice_files",
        "invoice_params",
        "reminder_preview",
        "last_sent_reminder",
    ):
        st.session_state.pop(state_key, None)
    st.session_state["data_fingerprint"] = fingerprint

total_hours = float(summary["Logged Hours"].sum())
billable_hours = float(summary["Billable Hours"].sum())
non_billable_hours = total_hours - billable_hours
active_resources = int(worklogs.loc[worklogs["hours"] > 0, "resource"].nunique())

metric_cols = st.columns(4)
metric_cols[0].metric("Logged hours", display_hours(total_hours))
metric_cols[1].metric("Billable hours", display_hours(billable_hours))
metric_cols[2].metric("Non-billable hours", display_hours(non_billable_hours))
metric_cols[3].metric("Active resources", active_resources)

tabs = st.tabs(
    [
        "📊 Overview",
        "🧠 AI analysis",
        "✨ Client-ready worklogs",
        "🔔 Weekend nudge",
        "🧾 Invoice draft",
    ]
)

with tabs[0]:
    st.subheader("Portfolio at a glance")
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("**Hours by project**")
        project_totals = summary.groupby("Project", as_index=False)["Logged Hours"].sum()
        if not project_totals.empty:
            st.bar_chart(project_totals.set_index("Project"))
        else:
            st.caption("No project hours to chart.")
    with right:
        st.markdown("**Billable mix**")
        st.dataframe(
            pd.DataFrame(
                {
                    "Category": ["Billable", "Non-billable"],
                    "Hours": [billable_hours, non_billable_hours],
                }
            ),
            hide_index=True,
            use_container_width=True,
        )
        st.caption("Uploaded holiday and leave entries are automatically treated as non-billable.")

    st.markdown("**Project and resource totals**")
    st.dataframe(summary, hide_index=True, use_container_width=True)
    recognized_time_off = worklogs.loc[
        worklogs["is_leave"],
        ["date", "resource", "project", "leave_type", "hours", "billable_hours"],
    ].rename(
        columns={
            "date": "Date",
            "resource": "Resource",
            "project": "Project",
            "leave_type": "Recognized as",
            "hours": "Recorded Hours",
            "billable_hours": "Billable Hours",
        }
    )
    with st.expander(f"Keka holidays & leaves recognized ({len(recognized_time_off)} entries)"):
        if recognized_time_off.empty:
            st.info(
                "No holiday or leave labels were found in the uploaded worklogs. "
                "You can add extra dates in the sidebar for gap detection."
            )
        else:
            holiday_count = int((recognized_time_off["Recognized as"] == "Public Holiday").sum())
            leave_count = int(
                recognized_time_off["Recognized as"].isin(["Leave", "Time Off"]).sum()
            )
            st.caption(
                f"Automatically recognized {holiday_count} public holiday(s) and "
                f"{leave_count} leave/time-off entry/entries from the uploaded worklog labels. "
                "These dates are excluded from missing-timesheet checks."
            )
            st.dataframe(recognized_time_off, hide_index=True, use_container_width=True)
    with st.expander("Inspect normalized worklogs"):
        st.dataframe(worklogs, hide_index=True, use_container_width=True)

with tabs[1]:
    st.subheader("AI workload & billing analysis")
    st.write(
        "Ask Gemini to identify workload allocation patterns, billable mix, "
        "worklog quality issues, and practical items for a PM to review."
    )
    if api_key:
        share_analysis_data = st.checkbox(
            "I agree to send aggregated timesheet metrics and cleaned worklog "
            "description samples to Gemini for analysis.",
            key="share_analysis_data",
        )
        st.caption(
            "Resource and project names are included to make findings actionable. "
            "Review your organization's data-sharing policy before continuing."
        )
    else:
        share_analysis_data = False
        st.info(
            "Add a Gemini API key in the sidebar for AI-generated analysis. "
            "Without one, this tab provides transparent rule-based checks."
        )

    if st.button(
        "🧠 Analyze timesheets",
        type="primary",
        key="analyze_worklogs",
        disabled=bool(api_key) and not share_analysis_data,
    ):
        with st.spinner("Analyzing workload and billing patterns…"):
            st.session_state["analysis_result"] = analyze_worklogs(
                worklogs, api_key=api_key or None
            )

    analysis_result = st.session_state.get("analysis_result")
    if analysis_result is not None:
        if analysis_result.status == "Gemini AI":
            st.success("Analysis generated with Gemini AI.")
        else:
            st.info("Showing rule-based local analysis.")
        if analysis_result.error:
            st.warning(f"Gemini could not be used: {analysis_result.error}")
        st.caption(
            "These findings are decision support, not employee performance ratings "
            "or automatic billing decisions. Verify evidence against the source export."
        )
        for finding in analysis_result.findings:
            with st.container(border=True):
                st.markdown(f"**{finding['type']}: {finding['title']}**")
                st.markdown(f"**Evidence:** {finding['evidence']}")
                st.write(finding["analysis"])
                st.markdown(f"**Suggested review:** {finding['recommendation']}")
    else:
        st.info("Run the analysis to see findings based on the currently selected timesheet.")

with tabs[2]:
    st.subheader("Polished summaries for client review")
    st.write(
        "Worklog descriptions are consolidated by project and resource, with ticket keys "
        "and long URLs removed before client review."
    )
    if st.button("✨ Generate client-ready summaries", type="primary", key="polish_report"):
        with st.spinner("Preparing client-ready summaries…"):
            result = generate_client_summaries(worklogs, api_key=api_key or None)
        st.session_state["client_summaries"] = result.summaries
        st.session_state["ai_status"] = result.status
        st.session_state["ai_error"] = result.error

    client_summaries = st.session_state.get("client_summaries")
    if client_summaries is not None:
        status = st.session_state.get("ai_status", "Local cleanup")
        if status == "Gemini AI":
            st.success("Summaries generated with Gemini AI.")
        else:
            st.info("Summaries use local cleanup (URLs and ticket keys removed).")
        if st.session_state.get("ai_error"):
            st.warning(f"Gemini could not be used: {st.session_state['ai_error']}")
        st.dataframe(client_summaries, hide_index=True, use_container_width=True)
        st.download_button(
            "Download client summaries (CSV)",
            client_summaries.to_csv(index=False).encode("utf-8-sig"),
            file_name="client_timesheet_summaries.csv",
            mime="text/csv",
        )
    else:
        st.info("Generate summaries to see client-ready line items here.")

with tabs[3]:
    st.subheader("Missing timesheets & Friday reminders")
    if worklogs["date"].notna().any():
        latest_workday = worklogs["date"].dropna().max().date()
        if latest_workday.weekday() <= 4:
            suggested_friday = latest_workday + timedelta(days=4 - latest_workday.weekday())
        else:
            suggested_friday = latest_workday - timedelta(days=latest_workday.weekday() - 4)
        week_ending = st.date_input(
            "Week ending Friday",
            value=suggested_friday,
            help="Checks Monday through Friday, excluding holidays and leave identified from the uploaded worklog labels, plus any additional dates configured in the sidebar.",
        )
        if week_ending.weekday() != 4:
            st.warning("Choose a Friday to check the intended workweek.")
        else:
            missing = detect_missing_timesheets(worklogs, week_ending, public_holidays)
            selected_resource = None
            if missing.empty:
                st.success("All tracked resources have complete weekday timesheets for this week.")
            else:
                st.warning(f"{len(missing)} resource(s) have one or more missing weekdays.")
                st.dataframe(missing, hide_index=True, use_container_width=True)
                selected_resource = st.selectbox(
                    "Choose a teammate to nudge",
                    missing["resource"].tolist(),
                    key=f"nudge_resource_{week_ending.isoformat()}",
                )
                missing_days = missing.loc[
                    missing["resource"] == selected_resource, "missing_days"
                ].iloc[0]
                if st.button("📨 Prepare personalized reminder", type="primary"):
                    with st.spinner("Writing a personalized reminder…"):
                        reminder = generate_reminder(
                            selected_resource,
                            missing_days,
                            week_ending,
                            api_key=api_key or None,
                        )
                    st.session_state["reminder_preview"] = {
                        "resource": selected_resource,
                        "week_ending": week_ending,
                        "result": reminder,
                    }
            preview = st.session_state.get("reminder_preview")
            if (
                preview
                and selected_resource is not None
                and preview["resource"] == selected_resource
                and preview["week_ending"] == week_ending
            ):
                reminder = preview["result"]
                if reminder.status == "Gemini AI":
                    st.success("AI reminder prepared — no external message was sent.")
                else:
                    st.info("Reminder template prepared — no external message was sent.")
                if reminder.error:
                    st.warning(f"Gemini could not be used: {reminder.error}")
                recipient_email = st.text_input(
                    "Recipient email",
                    key=(
                        f"reminder_email_{week_ending.isoformat()}_"
                        f"{selected_resource}"
                    ),
                    help="Enter the teammate's email; timesheet exports do not always include email addresses.",
                )
                email_subject = (
                    f"Timesheet reminder — week ending "
                    f"{week_ending.strftime('%b %d, %Y')}"
                )
                st.markdown(f"**Subject:** {email_subject}")
                st.markdown(f"**To:** {recipient_email or 'Enter a recipient email above'}")
                st.code(reminder.message)
                email_confirmed = st.checkbox(
                    "I confirm sending this reminder email to the recipient above.",
                    key=(
                        f"confirm_reminder_email_{week_ending.isoformat()}_"
                        f"{selected_resource}_{recipient_email.strip().lower()}"
                    ),
                )
                if st.button(
                    "✉️ Send reminder email",
                    type="primary",
                    key=(
                        f"send_reminder_email_{week_ending.isoformat()}_"
                        f"{selected_resource}"
                    ),
                    disabled=not email_confirmed or not recipient_email.strip(),
                ):
                    try:
                        send_reminder_email(
                            recipient=recipient_email,
                            subject=email_subject,
                            body=reminder.message,
                            sender=sender_email,
                            smtp_host=smtp_host,
                            smtp_port=int(smtp_port),
                            username=smtp_username or None,
                            password=smtp_password or None,
                        )
                        st.session_state["last_sent_reminder"] = (
                            selected_resource,
                            week_ending,
                            recipient_email.strip(),
                        )
                        st.success(
                            f"Reminder email sent to {recipient_email.strip()}."
                        )
                    except (RuntimeError, ValueError) as exc:
                        st.error(f"Could not send reminder email: {exc}")
                last_sent = st.session_state.get("last_sent_reminder")
                if (
                    last_sent
                    and last_sent[0] == selected_resource
                    and last_sent[1] == week_ending
                ):
                    st.caption(f"Last sent to {last_sent[2]}.")
    else:
        st.info(
            "The uploaded data has no readable work dates, so weekday completeness cannot be checked."
        )

with tabs[4]:
    st.subheader("Client invoice draft")
    projects = sorted(worklogs["project"].dropna().astype(str).unique().tolist())
    billable_projects = summary.loc[
        summary["Billable Hours"] > 0, "Project"
    ].drop_duplicates().tolist()
    project_options = billable_projects or projects
    if not project_options:
        st.info("No projects are available for invoicing.")
    else:
        project = st.selectbox("Client project", project_options, key="invoice_project")
        client_name = st.text_input("Client name", value=project)
        rate = st.number_input(
            "Hourly rate (USD)",
            min_value=0.0,
            value=150.0,
            step=5.0,
            help="Applied to billable hours for this project.",
        )
        project_summary = summary.loc[summary["Project"] == project]
        project_billable = float(project_summary["Billable Hours"].sum())
        total_amount = project_billable * rate
        invoice_cols = st.columns(3)
        invoice_cols[0].metric("Billable hours", display_hours(project_billable))
        invoice_cols[1].metric("Hourly rate", f"${rate:,.2f}")
        invoice_cols[2].metric("Draft amount", f"${total_amount:,.2f}")

        if st.button("Generate invoice files", type="primary"):
            with st.spinner("Polishing line items and preparing downloads…"):
                result = generate_client_summaries(worklogs, api_key=api_key or None)
                st.session_state["invoice_result"] = result
                st.session_state["invoice_files"] = build_invoice_workbook(
                    worklogs=worklogs,
                    summaries=result.summaries,
                    project=project,
                    client_name=client_name.strip() or project,
                    hourly_rate=rate,
                )
                st.session_state["invoice_params"] = (project, client_name.strip() or project, rate)

        invoice_result = st.session_state.get("invoice_result")
        invoice_files = st.session_state.get("invoice_files")
        expected_invoice_params = (project, client_name.strip() or project, rate)
        if (
            invoice_result is not None
            and invoice_files is not None
            and st.session_state.get("invoice_params") == expected_invoice_params
        ):
            if invoice_result.status == "Gemini AI":
                st.success("Invoice line items polished with Gemini AI.")
            else:
                st.info("Invoice uses local cleanup for its client-facing line items.")
            if invoice_result.error:
                st.warning(f"Gemini could not be used: {invoice_result.error}")
            invoice_lines = invoice_result.summaries.loc[
                invoice_result.summaries["Project"] == project
            ]
            st.dataframe(invoice_lines, hide_index=True, use_container_width=True)
            xlsx_data, csv_data = invoice_files
            st.download_button(
                "⬇️ Download invoice & timesheet report (Excel)",
                data=xlsx_data,
                file_name="client_timesheet_invoice.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            st.download_button(
                "⬇️ Download invoice line items (CSV)",
                data=csv_data,
                file_name="client_timesheet_invoice.csv",
                mime="text/csv",
            )
