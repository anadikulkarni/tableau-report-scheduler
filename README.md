# Tableau Report Mailer

Pull crosstabs out of a Tableau Cloud workbook, format them into a proper Excel
report, and email them with the Gmail API.

Everything runs locally. Nothing is uploaded anywhere except the email you
explicitly click send on.

---

## What it does

1. **Lists your Tableau Cloud workbooks** and the worksheets inside them.
2. **Downloads the crosstab** of each worksheet you pick
   (REST `GET /views/{id}/crosstab/excel`).
3. **Reformats it** — Tableau's export is completely bare, so the app rebuilds it
   with a title block, banded headers, accounting number formats, frozen panes,
   auto-filter, tuned column widths and print setup.
4. **Renders dashboards** to a PDF attachment and an inline image in the email body.
5. **Emails it** through the Gmail API, with subject/body templates that expand
   `{today}`, `{month}`, `{fy}` and friends.

---

## Setup

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Tableau personal access token

Tableau Cloud + MFA means username/password sign-in will not work, so the app uses
a PAT.

1. In Tableau Cloud: **My Account Settings → Personal Access Tokens**
2. Create a token, copy the **name** and the **secret** (the secret is shown once)
3. In the app sidebar → **Tableau connection**, fill in:
   - **Server URL** — the pod, e.g. `https://10ay.online.tableau.com`
   - **Site content URL** — the bit after `/site/` in your Tableau URL; blank for Default
   - **Token name** and **Token secret**
4. Click **Test**

The secret is written to `.env` (gitignored), never to `settings.json`.

> Personal access tokens expire after 15 days of no use. If sign-in starts
> failing, make a new one.

### 3. Gmail OAuth client

1. [Google Cloud Console](https://console.cloud.google.com/) → create or pick a project
2. **APIs & Services → Library →** enable **Gmail API**
3. **APIs & Services → OAuth consent screen →** set it up (External is fine; add
   your own address as a test user)
4. **Credentials → Create credentials → OAuth client ID → Desktop app**
5. Download the JSON and upload it in the app sidebar under **Gmail connection**
   (or drop it at `config/gmail_credentials.json`)
6. Click **Connect Gmail** and complete the browser consent

The refresh token is cached in `config/gmail_token.json`, so you only do this once.
Scope requested is `gmail.compose` — enough to send and to save drafts, nothing else.

### 4. Run

```bash
streamlit run app.py
```

Or double-click `run.bat`.

---

## Using it

### The quick way

The app opens on **Send report**. Each saved report is one card with a
**Build & review** button: it pulls fresh data from Tableau, then shows you the
finished email and the attachment before anything is sent. **Send now** sends it;
**Save as draft** puts it in Gmail for you to check first.

A **Daily Sales Report** preset ships configured against the Primary Sales
workbook: the five detail sheets become tabs in the Excel, and Channel Wise
Overview is rendered as a table inside the email body rather than as a tab.
Set the recipients once on the Compose tab, save the job, and from then on it is
two clicks.

### Building a new report


The **Compose** tab walks top to bottom:

| Step | What you choose |
|---|---|
| 1 · Workbook | Which Tableau workbook |
| 2 · What to include | Worksheets → Excel crosstab, dashboards → PDF + inline image, small sheets → table in the email body |
| 3 · Excel formatting | Accent colour, one file vs file-per-sheet, freeze rule, filters |
| 4 · Email | Subject and body, from a saved template if you like |
| 5 · Recipients | To / Cc / Bcc, or apply a saved distribution list |

**Preview & send** builds everything and shows you exactly what will go out —
the rendered subject, the body, each attachment with its size, the dashboard
images, and an HTML preview of the formatted Excel. Nothing is emailed until you
click **Send now**. **Save as draft** puts it in Gmail instead, so you can check
it in your own mailbox first.


### Tables in the email body

A small crosstab often reads better in the email than as a spreadsheet tab. Pick
those sheets under **Small sheets → table in the email body**; they are left out
of the workbook and rendered inline instead, styled like the Excel (header band,
totals highlighted, accounting numbers).

Put `{tables}` in the body where you want them. Without it they go after your
message text but above the signature. The tables are written with inline styles
throughout, because Gmail strips `<style>` blocks.

### Subject and file name tokens

| Token | Becomes |
|---|---|
| `{today}` | `25.08.2026` |
| `{today:%d %B %Y}` | `25 August 2026` (any strftime format) |
| `{yesterday}` `{tomorrow}` | Adjacent dates |
| `{month}` `{month_short}` `{prev_month}` | `August`, `Aug`, `July` |
| `{month_start}` `{month_end}` | First / last day of this month |
| `{year}` `{quarter}` `{week}` | `2026`, `Q3`, `35` |
| `{fy}` | `FY26-27` (April–March) |
| `{workbook}` `{view}` `{views}` `{view_count}` | What you selected |
| `{site}` | Tableau site |

Unknown tokens are left as-is rather than breaking the send.

### Saved jobs

**Save job** stores the whole configuration — workbook, sheets, formatting,
subject, body, recipients — under a name. Reload it from the sidebar to repeat a
send in two clicks.

---

## Scheduling

Saved jobs can run unattended:

```bash
python run_job.py "Daily sales report"
python run_job.py "Daily sales report" --draft
python run_job.py "Daily sales report" --dry-run
python run_job.py --list
```

To run it every weekday at 9am with Windows Task Scheduler:

```bash
schtasks /create /tn "Daily Sales Report" /tr "\"C:\Path\To\python.exe\" \"D:\anadi.k\Github\tableau-report-mailer\run_job.py\" \"Daily sales report\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:00
```

Gmail must have been authorised through the app at least once first.

---

## The formatting

The house style is lifted from `24.08.2026 - SALE REPORT FOR AUG 26.xlsm`:

- **Title block** — Calibri 14 bold, report date top-right
- **Header band** — solid accent (`5B9BD5` by default), white bold, wrapped and
  centred; grouped measure headers get the Lighter-80% tint above them and are
  merged across their columns
- **Totals** — rows whose label contains "Total" get the Lighter-60% tint;
  "Grand Total" gets grey. Both bold
- **Numbers** — Excel accounting formats (`#,##0.00` with bracketed negatives);
  percentages and ₹ currency carried through from Tableau's own formatting
- **Chrome** — thin borders, gridlines off, auto-filter, A4 landscape fitted to
  one page wide, repeating header rows, page numbers in the footer
- **Frozen panes** — by default only on sheets with 12+ columns, since locked
  panes on a narrow sheet are more annoying than useful. Switch to always/never
  per job

Six accent themes ship in `tabmailer/style.py` (Blue, Indigo, Green, Orange,
Amber, Slate). Adding another is one line in `THEMES`.

Layout detection is automatic: the app finds the first genuinely numeric row to
work out how many header rows there are, and the leading non-numeric columns to
work out the row-label block. It handles multi-row grouped headers and dashboard
crosstabs that come back as several tabs at once.

---

## Layout

```
app.py              Streamlit interface
run_job.py          CLI for scheduled runs
tabmailer/
  config.py         Settings, templates, lists, saved jobs
  tableau.py        Tableau Cloud REST wrapper (PAT auth)
  excel.py          Crosstab parsing, layout detection, formatting, HTML preview
  style.py          Design tokens - colours, fonts, number formats
  gmail.py          OAuth, MIME assembly, send/draft
  templates.py      {token} rendering
  pipeline.py       Download → format → send
  history.py        Send log
config/             settings.json, credentials, history  (gitignored)
output/             Local copies of everything sent      (gitignored)
```

## Files with secrets

`.env`, `config/gmail_credentials.json` and `config/gmail_token.json` are all
gitignored. Anyone with the token file can send mail as you — treat it like a
password.
