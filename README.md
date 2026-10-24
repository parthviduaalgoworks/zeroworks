# AI Timesheet & Client Billing Copilot

A Streamlit prototype for turning Tempo CSV/Excel worklogs into client-ready
summaries, missing-timesheet reminders, and invoice drafts.

## Run locally

Requires Python 3.10 or newer.

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

The app opens with sample data enabled so the dashboard can be explored without
an export. Upload a Tempo `.csv`, `.xlsx`, or `.xls` file in the sidebar to use
your own worklogs. Supported headers include common variations of work date,
resource/worker (including Tempo `Full name`), project (including `Space Name`),
description, and hours/time spent/duration. Tempo exports with `Billable Hours`
or `Billable Seconds` use those values directly for billing totals.

## Download directly from Tempo

1. In Tempo, create an API token under **Settings → Data Access → API
   integration**.
2. In the app sidebar, choose the Tempo region, paste the token, and click
   **Save token locally**. The token is stored in a local, git-ignored
   `.tempo_credentials.json` file and is never included in the scheduled task
   command. Confirm the **Tempo worker ID** matches the `workerId` parameter in
   the timesheet page URL. Alternatively, set `TEMPO_API_TOKEN`,
   `TEMPO_API_BASE_URL`, and `TEMPO_WORKER_ID`.
3. Choose any **from/to** dates and click **Fetch worklogs from Tempo**. The
   app fetches only that worker's worklogs using Tempo's documented
   `/4/worklogs/user/{accountId}` endpoint, loads the entries, and offers the
   raw API response as a CSV download. The default date range is Monday through
   Sunday to match the linked weekly timesheet.
4. To enable the automatic export, click **Enable Friday 5 PM export**. On
   Windows this registers `AITimesheetTempoFridayExport` in Task Scheduler.
   CSV files are saved under `tempo_exports/`.

The scheduled task runs in the Windows account's interactive session, uses
the computer's local timezone, and is configured to start a missed run when
the computer becomes available. This computer is set to India Standard Time;
it must be awake and signed in at 5 PM Friday for an on-time export. API access
is limited to the chosen worker's worklogs and the records the token owner is
permitted to read. The API CSV contains the raw API worklog fields; it is not a
pixel-for-pixel copy of Tempo's Jira timesheet export layout.

## Send timesheet reminder emails

The reminder tab first prepares a message for a selected employee and missing
weekdays. Enter the employee's email address, review the recipient, subject,
and message, tick the explicit confirmation checkbox, then click **Send
reminder email**.

SMTP defaults to Microsoft 365 (`smtp.office365.com:587` with STARTTLS). Enter
the SMTP username and mailbox app password in the sidebar; **From email** may
be left blank to use the SMTP username as the sender, or set it explicitly.
Alternatively, provide `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USERNAME`,
and `SMTP_PASSWORD` as environment variables. The app does not store SMTP
credentials in its local Tempo credentials file. Microsoft 365 SMTP AUTH must
be allowed by the mailbox and tenant; follow your organization's mail policy.
Sending requires an explicit per-email confirmation and a valid SMTP
configuration.

## Gemini summaries (optional)

Add a Gemini API key in the sidebar, or set `GEMINI_API_KEY` in the environment.
If no key is configured, the app uses a clearly labeled local cleanup that
removes URLs and Jira-style ticket keys. If Gemini cannot complete a request,
the local report remains available and the UI shows the error.

The **AI analysis** tab can use Gemini to review workload allocation, billable
mix, description quality, and PM review opportunities. Before Gemini analysis,
the app asks for confirmation to share aggregated metrics, project/resource
names, and cleaned description samples. Without Gemini, it reports a smaller
set of transparent rule-based checks. Findings are advisory; verify them
against the source worklogs before acting.

## Prototype notes

- Gap detection checks Monday through Friday for the selected week and excludes
  holidays and leave automatically labeled in the uploaded worklog (including
  Keka/Tempo `TIMEOFF`, `Public Holiday`, and leave descriptions), plus any
  additional public holidays entered as comma-separated `YYYY-MM-DD` dates in
  the sidebar.
- Reminder text is generated for review. Email is sent only after entering an
  address, reviewing the message, checking the confirmation box, and clicking
  the send button. No Slack message is sent.
- Invoice amounts are drafts calculated from billable project hours and the
  hourly rate entered in the app. Review all client-facing output before use.
