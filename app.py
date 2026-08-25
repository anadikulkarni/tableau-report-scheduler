"""Tableau Report Mailer - pick a workbook, format the crosstabs, email them.

Run with:  streamlit run app.py     (or double-click run.bat)
"""

from __future__ import annotations

import datetime as dt

import streamlit as st
import streamlit.components.v1 as components

from tabmailer import excel, gmail, history, pipeline, style, templates
from tabmailer.config import (
    DistributionList,
    GMAIL_CLIENT_PATH,
    SavedJob,
    SubjectTemplate,
    load_settings,
    save_settings,
    save_token_secret,
    upsert_job,
    upsert_list,
    upsert_template,
)
from tabmailer.tableau import TableauClient, TableauError

st.set_page_config(page_title="Tableau Report Mailer", page_icon="📊", layout="wide")

CSS = """
<style>
  #MainMenu, footer, header {visibility: hidden;}
  .block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1180px;}
  h1, h2, h3 {font-family: 'Segoe UI', Calibri, sans-serif; letter-spacing: -0.01em;}
  h1 {font-size: 1.7rem !important; font-weight: 650; margin-bottom: 0.1rem;}
  .tm-lede {color: #6b7280; font-size: 0.9rem; margin-bottom: 1.4rem;}
  .tm-step {font-size: 0.72rem; font-weight: 700; letter-spacing: 0.09em;
            text-transform: uppercase; color: #9ca3af; margin: 1.6rem 0 0.5rem;}
  .tm-pill {display:inline-block; padding: 2px 9px; border-radius: 999px;
            font-size: 0.72rem; font-weight: 600; margin-right: 6px;}
  .tm-ok {background:#dcfce7; color:#166534;}
  .tm-bad {background:#fee2e2; color:#991b1b;}
  .tm-warn {background:#fef3c7; color:#92400e;}
  .tm-card {border:1px solid #e5e7eb; border-radius:10px; padding:14px 16px;
            background:#fff; margin-bottom:10px;}
  .tm-mono {font-family: Consolas, monospace; font-size: 0.8rem;}
  div[data-testid="stMetricValue"] {font-size: 1.25rem;}
  .stButton button {border-radius: 7px;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

if "settings" not in st.session_state:
    st.session_state.settings = load_settings()
st.session_state.setdefault("workbooks", None)
st.session_state.setdefault("views", {})
st.session_state.setdefault("build", None)
st.session_state.setdefault("job", SavedJob(name="Untitled"))

settings = st.session_state.settings
job: SavedJob = st.session_state.job


def persist() -> None:
    save_settings(st.session_state.settings)


def pill(ok: bool, text: str, warn: bool = False) -> str:
    cls = "tm-warn" if warn else ("tm-ok" if ok else "tm-bad")
    return f"<span class='tm-pill {cls}'>{text}</span>"


def step(label: str) -> None:
    st.markdown(f"<div class='tm-step'>{label}</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Tableau Report Mailer")

    tab_ok = settings.tableau.is_configured
    gm_ok = gmail.is_authorised()
    gm_ready = gmail.client_secret_present()
    st.markdown(
        pill(tab_ok, "Tableau connected" if tab_ok else "Tableau not set up")
        + pill(gm_ok, "Gmail connected" if gm_ok else ("Gmail not authorised" if gm_ready else "Gmail not set up"),
               warn=gm_ready and not gm_ok),
        unsafe_allow_html=True,
    )

    with st.expander("Tableau connection", expanded=not tab_ok):
        server_url = st.text_input(
            "Server URL", value=settings.tableau.server_url,
            placeholder="https://10ay.online.tableau.com",
            help="The pod your Tableau Cloud site lives on - copy it from your browser's address bar.",
        )
        site_url = st.text_input(
            "Site content URL", value=settings.tableau.site_content_url,
            placeholder="mycompany",
            help="The bit after /site/ in the Tableau URL. Leave blank for the Default site.",
        )
        token_name = st.text_input("Token name", value=settings.tableau.token_name)
        token_secret = st.text_input(
            "Token secret", value=settings.tableau.token_secret, type="password",
            help="Tableau Cloud > My Account Settings > Personal Access Tokens.",
        )
        c1, c2 = st.columns(2)
        if c1.button("Save", use_container_width=True, key="save_tab"):
            settings.tableau.server_url = server_url.strip()
            settings.tableau.site_content_url = site_url.strip()
            settings.tableau.token_name = token_name.strip()
            if token_secret:
                save_token_secret(token_secret.strip())
            persist()
            st.session_state.workbooks = None
            st.success("Saved")
            st.rerun()
        if c2.button("Test", use_container_width=True, key="test_tab"):
            try:
                info = TableauClient(settings.tableau).test_connection()
                st.success(f"Connected to '{info['site']}' (API {info['api_version']})")
            except TableauError as exc:
                st.error(str(exc))

    with st.expander("Gmail connection", expanded=False):
        if not gm_ready:
            st.caption(
                "Create an OAuth **Desktop app** client in Google Cloud Console with the "
                "Gmail API enabled, then upload the JSON here."
            )
            up = st.file_uploader("OAuth client JSON", type="json", key="gm_json")
            if up is not None:
                gmail.save_client_secret(up.getvalue())
                st.success(f"Saved to {GMAIL_CLIENT_PATH.name}")
                st.rerun()
        else:
            st.caption(f"Client file: `{GMAIL_CLIENT_PATH.name}`")
            c1, c2 = st.columns(2)
            if c1.button("Connect Gmail", use_container_width=True, key="gm_conn"):
                try:
                    addr = gmail.authorised_address()
                    st.success(f"Authorised as {addr}")
                    st.rerun()
                except gmail.GmailError as exc:
                    st.error(str(exc))
            if c2.button("Sign out", use_container_width=True, key="gm_out"):
                gmail.revoke_local_token()
                st.rerun()

    with st.expander("Defaults", expanded=False):
        settings.sender_name = st.text_input("Sender display name", value=settings.sender_name)
        settings.signature = st.text_area(
            "Signature", value=settings.signature, height=80,
            help="Appended to every email body. Supports the same {tokens}.",
        )
        settings.output_dir = st.text_input("Local copy folder", value=settings.output_dir)
        settings.keep_local_copies = st.checkbox(
            "Keep a local copy of every file", value=settings.keep_local_copies
        )
        if st.button("Save defaults", use_container_width=True, key="save_def"):
            persist()
            st.success("Saved")

    st.divider()
    st.markdown("**Saved jobs**")
    job_names = [j.name for j in settings.jobs]
    if job_names:
        chosen = st.selectbox("Load a job", ["-"] + job_names, label_visibility="collapsed")
        c1, c2 = st.columns(2)
        if c1.button("Load", use_container_width=True, disabled=chosen == "-"):
            loaded = next(j for j in settings.jobs if j.name == chosen)
            st.session_state.job = SavedJob(**vars(loaded))
            st.session_state.build = None
            st.rerun()
        if c2.button("Delete", use_container_width=True, disabled=chosen == "-"):
            settings.jobs = [j for j in settings.jobs if j.name != chosen]
            persist()
            st.rerun()
    else:
        st.caption("None yet - configure a report and save it below.")


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown("# Tableau Report Mailer")
st.markdown(
    "<div class='tm-lede'>Pull crosstabs out of Tableau Cloud, format them as a proper "
    "report, and email them.</div>",
    unsafe_allow_html=True,
)

tab_compose, tab_preview, tab_lists, tab_history = st.tabs(
    ["Compose", "Preview & send", "Lists & templates", "History"]
)


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------

def _render_compose() -> None:
    """Compose tab. A function, not an inline block, so that bailing out early
    only skips the rest of this tab rather than halting the whole page."""
    if not settings.tableau.is_configured:
        st.info("Add your Tableau connection in the sidebar to get started.")
        return

    step("1 · Workbook")
    c1, c2 = st.columns([5, 1])
    if st.session_state.workbooks is None:
        with c1, st.spinner("Loading workbooks from Tableau…"):
            try:
                st.session_state.workbooks = TableauClient(settings.tableau).list_workbooks()
            except TableauError as exc:
                st.error(str(exc))
                return
    workbooks = st.session_state.workbooks or []
    if c2.button("Refresh", use_container_width=True, key="refresh_wb"):
        st.session_state.workbooks = None
        st.session_state.views = {}
        st.rerun()

    if not workbooks:
        st.warning("No workbooks visible to this account.")
        return

    # Options are plain strings mapped back to objects - selection then survives
    # a workbook list refresh, which object identity would not.
    wb_by_label: dict[str, object] = {}
    for w in workbooks:
        label = w.label
        if label in wb_by_label:
            label = f"{label} [{w.id[:6]}]"
        wb_by_label[label] = w
    wb_labels = list(wb_by_label)
    wb_index = next((i for i, lbl in enumerate(wb_labels) if wb_by_label[lbl].id == job.workbook_id), 0)
    workbook = wb_by_label[
        c1.selectbox("Workbook", wb_labels, index=wb_index, label_visibility="collapsed")
    ]
    if workbook.id != job.workbook_id:
        job.workbook_id = workbook.id
        job.workbook_name = workbook.name
        job.view_ids, job.dashboard_view_ids, job.view_names = [], [], []
        st.session_state.build = None

    if workbook.id not in st.session_state.views:
        with st.spinner("Loading worksheets…"):
            try:
                st.session_state.views[workbook.id] = TableauClient(settings.tableau).list_views(workbook.id)
            except TableauError as exc:
                st.error(str(exc))
                return
    views = st.session_state.views[workbook.id]
    sheets = [v for v in views if not v.is_story]
    dashboards = [v for v in views if v.is_dashboard]

    st.caption(
        f"{len(views)} sheets in this workbook · "
        f"{len(dashboards)} dashboard(s) · updated "
        f"{workbook.updated_at.strftime('%d %b %Y %H:%M') if workbook.updated_at else 'unknown'}"
    )

    # -- 2. worksheets -----------------------------------------------------
    step("2 · What to include")
    sheet_by_label = {v.label: v for v in sheets}
    dash_by_name = {v.name: v for v in dashboards}

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Select all worksheets", use_container_width=True):
            job.view_ids = [v.id for v in sheets]
            st.rerun()
        chosen_labels = st.multiselect(
            "Worksheets → Excel crosstab",
            options=list(sheet_by_label),
            default=[lbl for lbl, v in sheet_by_label.items() if v.id in job.view_ids],
            help="Each becomes a formatted tab in one workbook.",
        )
        selected = [sheet_by_label[lbl] for lbl in chosen_labels]
        job.view_ids = [v.id for v in selected]
        job.view_names = [v.name for v in selected]
    with c2:
        st.markdown("<div style='height:38px'></div>", unsafe_allow_html=True)
        chosen_dash = st.multiselect(
            "Dashboards → PDF attachment + image in the email",
            options=list(dash_by_name),
            default=[n for n, v in dash_by_name.items() if v.id in job.dashboard_view_ids],
            help="Rendered server-side by Tableau, so the layout matches what you see online.",
        )
        dash_selected = [dash_by_name[n] for n in chosen_dash]
        job.dashboard_view_ids = [v.id for v in dash_selected]

    if not selected and not dash_selected:
        st.warning("Pick at least one worksheet or dashboard.")

    # -- 3. formatting -----------------------------------------------------
    step("3 · Excel formatting")
    c1, c2, c3 = st.columns([2, 2, 3])
    theme_names = list(style.THEMES)
    job.theme = c1.selectbox("Accent colour", theme_names, index=theme_names.index(job.theme) if job.theme in theme_names else 0)
    job.one_file_per_view = c2.selectbox(
        "Packaging", [False, True],
        index=1 if job.one_file_per_view else 0,
        format_func=lambda v: "One file, tab per sheet" if not v else "Separate file per sheet",
    )
    with c3:
        cc1, cc2 = st.columns(2)
        job.contents_sheet = cc1.checkbox("Contents tab", value=job.contents_sheet)
        job.autofilter = cc2.checkbox("Auto-filter", value=job.autofilter)
        job.freeze_panes = cc1.checkbox("Freeze headers", value=job.freeze_panes)
        job.banded_rows = cc2.checkbox("Banded rows", value=job.banded_rows)
        job.negatives_red = cc1.checkbox("Negatives in red", value=job.negatives_red)

    with st.expander("View filters (optional)"):
        st.caption(
            "Restrict the data Tableau exports, the same way `?vf_Region=West` works in a "
            "Tableau URL. One `Field = Value` per line; field names are case-sensitive."
        )
        raw_filters = st.text_area(
            "Filters",
            value="\n".join(f"{k} = {v}" for k, v in job.filters.items()),
            height=90,
            placeholder="Region = West\nCategory = Pickles",
            label_visibility="collapsed",
        )
        parsed: dict[str, str] = {}
        for line in raw_filters.splitlines():
            field_name, sep, value = line.partition("=")
            if sep and field_name.strip() and value.strip():
                parsed[field_name.strip()] = value.strip()
        job.filters = parsed
        if parsed:
            st.caption("Applying: " + " · ".join(f"{k}={v}" for k, v in parsed.items()))

    # -- 4. email ----------------------------------------------------------
    step("4 · Email")
    context = templates.build_context(
        workbook=job.workbook_name,
        views=job.view_names,
        site=settings.tableau.site_content_url or "Default",
    )

    tpl_names = [t.name for t in settings.templates]
    c1, c2 = st.columns([3, 2])
    picked = c1.selectbox("Start from a saved template", ["-"] + tpl_names)
    if c2.button("Apply template", use_container_width=True, disabled=picked == "-"):
        tpl = next(t for t in settings.templates if t.name == picked)
        job.subject, job.body = tpl.subject, tpl.body
        st.rerun()

    job.subject = st.text_input("Subject", value=job.subject or "{workbook} - {today}")
    st.caption(f"Preview: **{templates.render(job.subject, context)}**")

    job.body = st.text_area(
        "Body", value=job.body or "Hi all,\n\nPlease find attached the {workbook} for {today}.\n\nRegards",
        height=140,
    )
    job.filename = st.text_input("Attachment file name", value=job.filename or "{workbook} - {today}.xlsx")
    job.report_title = st.text_input(
        "Report title inside the Excel", value=job.report_title,
        placeholder="Leave blank to use the workbook name",
    )
    st.caption(f"File: **{templates.render_filename(job.filename, context)}**")

    with st.expander("Available tokens"):
        st.dataframe(
            [{"Token": t, "Meaning": d, "Right now": v} for t, d, v in templates.preview_tokens(context)],
            use_container_width=True, hide_index=True,
        )

    # -- 5. recipients -----------------------------------------------------
    step("5 · Recipients")
    list_names = [d.name for d in settings.lists]
    if list_names:
        c1, c2 = st.columns([3, 2])
        chosen_list = c1.selectbox("Use a saved list", ["-"] + list_names)
        if c2.button("Apply list", use_container_width=True, disabled=chosen_list == "-"):
            dist = next(d for d in settings.lists if d.name == chosen_list)
            job.to, job.cc, job.bcc = list(dist.to), list(dist.cc), list(dist.bcc)
            st.rerun()

    c1, c2, c3 = st.columns(3)
    job.to = gmail.parse_addresses(c1.text_area("To", value="\n".join(job.to), height=100))
    job.cc = gmail.parse_addresses(c2.text_area("Cc", value="\n".join(job.cc), height=100))
    job.bcc = gmail.parse_addresses(c3.text_area("Bcc", value="\n".join(job.bcc), height=100))

    bad = gmail.invalid_addresses([*job.to, *job.cc, *job.bcc])
    if bad:
        st.error("These don't look like valid addresses: " + ", ".join(bad))

    # -- save job ----------------------------------------------------------
    st.divider()
    c1, c2 = st.columns([3, 1])
    job.name = c1.text_input("Save this whole configuration as", value=job.name)
    if c2.button("Save job", use_container_width=True, type="secondary"):
        if job.name.strip() and job.name != "Untitled":
            upsert_job(settings, SavedJob(**vars(job)))
            persist()
            st.success(f"Saved '{job.name}'")
        else:
            st.warning("Give the job a name first.")

    st.session_state.job = job


with tab_compose:
    _render_compose()


# ---------------------------------------------------------------------------
# Preview & send
# ---------------------------------------------------------------------------

with tab_preview:
    if not (job.view_ids or job.dashboard_view_ids):
        st.info("Choose a workbook and some worksheets on the Compose tab first.")
    else:
        c1, c2 = st.columns([1, 3])
        if c1.button("Build report", type="primary", use_container_width=True):
            bar = st.progress(0.0, text="Starting…")
            try:
                all_views = st.session_state.views.get(job.workbook_id, [])
                st.session_state.build = pipeline.build(
                    job, settings, all_views,
                    progress=lambda msg, frac: bar.progress(min(frac, 1.0), text=msg),
                )
            except TableauError as exc:
                st.session_state.build = None
                st.error(str(exc))
            finally:
                bar.empty()

        result: pipeline.BuildResult | None = st.session_state.build

        if result and result.has_content:
            for warning in result.warnings:
                st.warning(warning)

            st.markdown("#### Email")
            recipients = ", ".join(job.to) or "(no recipients yet)"
            st.markdown(
                f"<div class='tm-card'><b>To</b> {recipients}<br>"
                + (f"<b>Cc</b> {', '.join(job.cc)}<br>" if job.cc else "")
                + (f"<b>Bcc</b> {', '.join(job.bcc)}<br>" if job.bcc else "")
                + f"<b>Subject</b> {result.subject}</div>",
                unsafe_allow_html=True,
            )
            st.text(result.body)

            st.markdown("#### Attachments")
            for f in result.files:
                c1, c2 = st.columns([4, 1])
                c1.markdown(
                    f"<div class='tm-card tm-mono'>{f.filename} &nbsp;·&nbsp; "
                    f"{f.size_kb:,.0f} KB</div>", unsafe_allow_html=True
                )
                c2.download_button(
                    "Download", f.content, file_name=f.filename,
                    use_container_width=True, key=f"dl_{f.filename}",
                )
            if result.inline_images:
                st.caption(f"{len(result.inline_images)} dashboard image(s) embedded in the email body")
                for img in result.inline_images:
                    try:
                        st.image(img.content, caption=img.caption, use_container_width=True)
                    except Exception:  # noqa: BLE001 - a bad render shouldn't kill the page
                        st.warning(
                            f"'{img.caption}' came back from Tableau as something that isn't a "
                            f"readable image ({len(img.content):,} bytes). It will still be "
                            "attached to the email, but check the view renders online."
                        )
            if result.saved_paths:
                st.caption("Saved locally: " + " · ".join(result.saved_paths))

            if result.specs:
                st.markdown("#### How the Excel will look")
                components.html(
                    excel.preview_html(result.specs, pipeline.report_options(job, job.workbook_name, "")),
                    height=520, scrolling=True,
                )

            st.divider()
            st.markdown("#### Send")
            if not job.to:
                st.warning("Add at least one recipient on the Compose tab.")
            elif not gmail.is_authorised():
                st.warning("Connect Gmail in the sidebar before sending.")
            else:
                st.caption(
                    f"Sending {len(result.files)} attachment(s), {result.total_size_kb:,.0f} KB "
                    f"total, to {len(job.to)} recipient(s)."
                )
                c1, c2, _ = st.columns([1, 1, 2])
                if c1.button("Send now", type="primary", use_container_width=True):
                    try:
                        outcome = pipeline.send(result, job, settings, job.to, job.cc, job.bcc)
                        st.success(f"Sent from {outcome.get('sender', '')}.")
                        st.balloons()
                    except gmail.GmailError as exc:
                        st.error(str(exc))
                if c2.button("Save as draft", use_container_width=True):
                    try:
                        pipeline.send(result, job, settings, job.to, job.cc, job.bcc, as_draft=True)
                        st.success("Draft saved in Gmail.")
                    except gmail.GmailError as exc:
                        st.error(str(exc))
        elif result:
            st.warning("Nothing came back from Tableau for that selection.")


# ---------------------------------------------------------------------------
# Lists & templates
# ---------------------------------------------------------------------------

with tab_lists:
    c1, c2 = st.columns(2)

    with c1:
        st.markdown("#### Distribution lists")
        for dist in settings.lists:
            with st.expander(f"{dist.name}  ({len(dist.to)} to)"):
                st.markdown(f"<div class='tm-mono'>{'<br>'.join(dist.to)}</div>", unsafe_allow_html=True)
                if st.button("Delete", key=f"del_list_{dist.name}"):
                    settings.lists = [d for d in settings.lists if d.name != dist.name]
                    persist()
                    st.rerun()
        with st.form("new_list", clear_on_submit=True):
            name = st.text_input("List name")
            to = st.text_area("To", height=80)
            cc = st.text_area("Cc", height=60)
            bcc = st.text_area("Bcc", height=60)
            if st.form_submit_button("Save list", use_container_width=True) and name.strip():
                upsert_list(settings, DistributionList(
                    name=name.strip(),
                    to=gmail.parse_addresses(to),
                    cc=gmail.parse_addresses(cc),
                    bcc=gmail.parse_addresses(bcc),
                ))
                persist()
                st.rerun()

    with c2:
        st.markdown("#### Subject templates")
        for tpl in settings.templates:
            with st.expander(tpl.name):
                st.markdown(f"<div class='tm-mono'>{tpl.subject}</div>", unsafe_allow_html=True)
                st.text(tpl.body)
                if st.button("Delete", key=f"del_tpl_{tpl.name}"):
                    settings.templates = [t for t in settings.templates if t.name != tpl.name]
                    persist()
                    st.rerun()
        with st.form("new_tpl", clear_on_submit=True):
            name = st.text_input("Template name")
            subject = st.text_input("Subject", placeholder="{workbook} - {today:%d.%m.%Y}")
            body = st.text_area("Body", height=120)
            if st.form_submit_button("Save template", use_container_width=True) and name.strip():
                upsert_template(settings, SubjectTemplate(name.strip(), subject, body))
                persist()
                st.rerun()


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

with tab_history:
    entries = history.recent(100)
    if not entries:
        st.info("Nothing sent yet.")
    else:
        st.dataframe(
            [
                {
                    "When": e.get("timestamp", "")[:16].replace("T", " "),
                    "Status": e.get("status", ""),
                    "Workbook": e.get("workbook", ""),
                    "Subject": e.get("subject", ""),
                    "To": ", ".join(e.get("to", [])),
                    "Files": ", ".join(e.get("files", [])),
                    "KB": e.get("size_kb", ""),
                    "Error": e.get("error", ""),
                }
                for e in entries
            ],
            use_container_width=True, hide_index=True,
        )
