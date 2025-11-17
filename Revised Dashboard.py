#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Revised Dashboard (prototype)
Base features:
- Safe secrets access (won't crash if secrets.toml missing)
- Uses fpdf2 (from fpdf import FPDF)
- Fixes FK by making Laptops.asset_tag TEXT PRIMARY KEY
- Auto-creates Laptops row if missing
- Ensures Employee row exists
- Emails PDF receipts on Check-In and Check-Out

SPRINT 3:
- Daily/Weekly reporting functions
- Export option: PDF-only (with charts & summary)
- Supports "Single day" selection in Reports
"""

import os
import io
import sqlite3
import tempfile
import streamlit as stl
import pandas as pd
from datetime import datetime, date, timedelta

# PDF + Email (existing)
from fpdf import FPDF          # pip install fpdf2
import ssl
import certifi                 # pip install certifi
import smtplib
from email.utils import formataddr
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# --- Charts for PDF (NEW) ---
# We render charts with matplotlib (non-interactive Agg backend)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ---------------- UI chrome ----------------
stl.markdown(
    """
    <style>
        section[data-testid="stSidebar"] img { margin-top: -45px; }
    </style>
    """,
    unsafe_allow_html=True
)
with stl.sidebar:
    # stl.image("static/logo.png", width=200)
    stl.markdown("---")


# ---------------- Safe secrets helpers ----------------
def _get_secret(key, default=None):
    """Return a secret or default without crashing if secrets.toml is missing."""
    try:
        return stl.secrets.get(key, default)
    except Exception:
        return default

def _bool_secret(name, default=False):
    """Parse boolean-like secrets (True/False, '1'/'0', 'yes'/'no', etc.)."""
    v = _get_secret(name, default)
    if isinstance(v, bool): return v
    if isinstance(v, (int, float)): return bool(v)
    if isinstance(v, str): return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


# ---------------- DB helpers ----------------
def database_connection():
    """Open SQLite with FK checks enabled."""
    conn = sqlite3.connect('checkin_system.db')
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def tables():
    """
    Create tables if they don't exist.
    NOTE: Laptops.asset_tag is TEXT PRIMARY KEY so we can support tags like 'PC-12345'.
    """
    conn = database_connection()
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON")
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS Employees (
            employee_id INTEGER PRIMARY KEY,
            name  TEXT NOT NULL,
            email TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS Laptops (
            asset_tag  TEXT PRIMARY KEY,
            model      TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id    INTEGER NOT NULL,
            asset_tag      TEXT    NOT NULL,
            issue          TEXT    NOT NULL,
            check_in_time  DATETIME DEFAULT CURRENT_TIMESTAMP,
            check_out_time DATETIME,
            status         TEXT CHECK(status IN ('Checked-In', 'Checked-Out')) DEFAULT 'Checked-In',
            FOREIGN KEY (employee_id) REFERENCES Employees(employee_id),
            FOREIGN KEY (asset_tag)   REFERENCES Laptops(asset_tag)
        );
    """)
    conn.commit()
    conn.close()

def ensure_laptop_exists(asset_tag: str):
    """Idempotent insert so Transactions FK never fails."""
    conn = database_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO Laptops (asset_tag, model, description) VALUES (?, '', '')",
        (str(asset_tag),)
    )
    conn.commit()
    conn.close()

def ensure_employee_exists(employee_id: int, name: str = "", email: str = ""):
    """Create a minimal employee row if it doesn't exist (name/email can be empty strings)."""
    conn = database_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO Employees (employee_id, name, email) VALUES (?, ?, ?)",
        (int(employee_id), name or "", email or "")
    )
    conn.commit()
    conn.close()

def upsert_employee(employee_id: int, name: str, email: str):
    """Update name/email if provided; create row if missing."""
    name = (name or "").strip()
    email = (email or "").strip()
    if not email:
        return
    conn = database_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Employees SET name=?, email=? WHERE employee_id=?", (name, email, int(employee_id)))
    if cur.rowcount == 0:
        cur.execute("INSERT INTO Employees (employee_id, name, email) VALUES (?, ?, ?)", (int(employee_id), name, email))
    conn.commit()
    conn.close()

def check_in(emp_id, asset_tag, issue):
    """Insert and return new transaction id (ensures FK parents exist)."""
    ensure_laptop_exists(asset_tag)
    ensure_employee_exists(emp_id)  # create minimal row if needed
    conn = database_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO Transactions (employee_id, asset_tag, issue)
        VALUES (?, ?, ?)
    """, (int(emp_id), str(asset_tag), issue))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id

def check_out(transaction_id):
    """Mark an open transaction as Checked-Out and timestamp it."""
    conn = database_connection()
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    conn.execute("""
        UPDATE Transactions
        SET check_out_time=?, status='Checked-Out'
        WHERE transaction_id=? AND status='Checked-In'
    """, (now, int(transaction_id)))
    conn.commit()
    conn.close()

def view_active_transactions():
    """Rows that are still Checked-In."""
    conn = database_connection()
    df = pd.read_sql("""
        SELECT transaction_id, employee_id, asset_tag, issue, check_in_time
        FROM Transactions
        WHERE status='Checked-In'
        ORDER BY check_in_time DESC
    """, conn)
    conn.close()
    return df

def view_completed_transactions():
    """Rows that have been Checked-Out."""
    conn = database_connection()
    df = pd.read_sql("""
        SELECT transaction_id, employee_id, asset_tag, issue, check_in_time, check_out_time
        FROM Transactions
        WHERE status='Checked-Out'
        ORDER BY check_out_time DESC
    """, conn)
    conn.close()
    return df

def get_transaction_details(transaction_id):
    """One row by id (used for receipts and emails)."""
    conn = database_connection()
    row = conn.execute("""
        SELECT transaction_id, employee_id, asset_tag, issue, check_in_time, check_out_time, status
        FROM Transactions WHERE transaction_id = ?
    """, (int(transaction_id),)).fetchone()
    conn.close()
    return row

def get_employee_meta(employee_id: int):
    """(name, email) for an employee id, or (None, None)."""
    conn = database_connection()
    row = conn.execute("SELECT name, email FROM Employees WHERE employee_id=?", (int(employee_id),)).fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return None, None


# ---------------- Email & PDF (existing) ----------------
def confirmation_code(tx_id: int) -> str:
    return f"CN-{tx_id:06d}"

def parse_issue_type(issue_text: str) -> str:
    return (issue_text.split(":", 1)[0] or "Issue").strip()

def create_pdf_receipt(tx_tuple, emp_name, emp_email, kind="Check-In"):
    """Create a simple one-page PDF receipt and return (filepath, confirmation_number)."""
    tx_id, emp_id, asset_tag, issue, check_in, check_out, _ = tx_tuple
    cn = confirmation_code(tx_id)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(190, 10, txt="SLAC Service Desk - {} Receipt".format(kind), ln=True, align='C')
    pdf.set_font("Arial", size=11)
    pdf.ln(6)
    pdf.cell(190, 8, txt=f"Confirmation Number: {cn}", ln=True)
    pdf.cell(190, 8, txt=f"Transaction ID: {tx_id}", ln=True)
    pdf.cell(190, 8, txt=f"Employee: {emp_name or ''} (ID: {emp_id})", ln=True)
    pdf.cell(190, 8, txt=f"Employee Email: {emp_email or '—'}", ln=True)
    pdf.cell(190, 8, txt=f"Asset Tag: {asset_tag}", ln=True)
    pdf.cell(190, 8, txt=f"Issue Type: {parse_issue_type(issue)}", ln=True)
    pdf.multi_cell(190, 8, txt=f"Issue Details: {issue}", align='L')
    pdf.cell(190, 8, txt=f"Check-In Time: {check_in}", ln=True)
    if kind == "Check-Out" and check_out:
        pdf.cell(190, 8, txt=f"Check-Out Time: {check_out}", ln=True)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_tx{tx_id}.pdf")
    tmp.close()
    pdf.output(tmp.name)
    return tmp.name, cn

def send_email_with_attachment_smtp(to_addr, subject, html_body, attachment_path):
    """SMTP send (Gmail-friendly, TLS with certifi CA)"""
    host        = _get_secret("SMTP_HOST")
    port        = int(_get_secret("SMTP_PORT", 587))
    use_tls     = _bool_secret("SMTP_USE_TLS", True)
    username    = _get_secret("SMTP_USERNAME")
    password    = _get_secret("SMTP_PASSWORD")
    sender_addr = _get_secret("SMTP_FROM", username or "no-reply@example.com")

    if not host:
        stl.warning("SMTP not configured (missing SMTP_HOST). Skipping email send.")
        return False

    msg = MIMEMultipart()
    msg["From"] = formataddr(("Service Desk General Inbox", sender_addr))
    msg["To"] = to_addr
    msg["Subject"] = subject

    cc_list = _get_secret("CC_RECIPIENTS", [])
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    recipients = [to_addr] + cc_list

    msg.attach(MIMEText(html_body, "html"))

    with open(attachment_path, "rb") as f:
        part = MIMEBase("application", "pdf")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(attachment_path)}"')
    msg.attach(part)

    try:
        tls_ctx = ssl.create_default_context(cafile=certifi.where())
        server = smtplib.SMTP(host, port, timeout=20)
        if use_tls:
            server.starttls(context=tls_ctx)
        if username and password:
            server.login(username, password)
        server.sendmail(sender_addr, recipients, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        stl.warning(f"SMTP send failed: {e}")
        return False

def build_email_html(emp_name, emp_id, asset_tag, issue, check_in, check_out, cn, kind):
    """Simple HTML body used for both check-in and check-out emails."""
    return f"""
    <p>Hi {emp_name or 'there'},</p>
    <p>This is a confirmation that your device was <b>{kind.lower()}</b> at the Service Desk.</p>
    <table cellspacing="0" cellpadding="4" border="0">
      <tr><td><b>Employee</b></td><td>{emp_name or ''} (ID: {emp_id})</td></tr>
      <tr><td><b>Asset Tag</b></td><td>{asset_tag}</td></tr>
      <tr><td><b>Issue Type</b></td><td>{parse_issue_type(issue)}</td></tr>
      <tr><td><b>Check-In Time</b></td><td>{check_in}</td></tr>
      {f'<tr><td><b>Check-Out Time</b></td><td>{check_out}</td></tr>' if (kind=='Check-Out' and check_out) else ''}
      <tr><td><b>Confirmation #</b></td><td>{cn}</td></tr>
    </table>
    <p>The PDF receipt is attached for your records.</p>
    <p>— Service Desk</p>
    """

def email_receipt(tx_tuple, kind="Check-In"):
    """Lookup email, generate PDF, and send the message (if email exists)."""
    tx_id, emp_id, asset_tag, issue, check_in, check_out, _ = tx_tuple
    emp_name, emp_email = get_employee_meta(emp_id)
    if not emp_email:
        stl.warning(f"No email on file for employee {emp_id}. Skipping {kind} email.")
        return False
    pdf_path, cn = create_pdf_receipt(tx_tuple, emp_name, emp_email, kind)
    try:
        subject = "From the Service Desk General Inbox"
        html = build_email_html(emp_name, emp_id, asset_tag, issue, check_in, check_out, cn, kind)
        ok = send_email_with_attachment_smtp(emp_email, subject, html, pdf_path)
        if ok:
            stl.info(f"{kind} confirmation emailed to {emp_email}.")
        return ok
    finally:
        try: os.remove(pdf_path)
        except Exception: pass


# ---------------- SPRINT 3: Reporting + PDF Export ----------------
def _fetch_all_transactions() -> pd.DataFrame:
    """Read ALL transactions and convert timestamps to pandas datetime."""
    conn = database_connection()
    df = pd.read_sql(
        """
        SELECT transaction_id, employee_id, asset_tag, issue,
               check_in_time, check_out_time, status
        FROM Transactions
        """,
        conn
    )
    conn.close()
    df["check_in_time"] = pd.to_datetime(df["check_in_time"], errors="coerce")
    df["check_out_time"] = pd.to_datetime(df["check_out_time"], errors="coerce")
    return df

def report_dataframe(period: str, kind: str, start_d: date, end_d: date):
    """
    Build aggregated (daily/weekly) counts + raw rows for
    'Check-Ins' / 'Check-Outs' / 'Both' between start_d and end_d.
    """
    df = _fetch_all_transactions()

    # Inclusive end-of-day filter window
    start_dt = pd.to_datetime(start_d)
    end_dt = pd.to_datetime(end_d) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

    # Build filtered "raw" datasets for each event with a common 'timestamp' column
    df_in = df[df["check_in_time"].between(start_dt, end_dt, inclusive="both")][
        ["transaction_id", "employee_id", "asset_tag", "issue", "check_in_time"]
    ].rename(columns={"check_in_time": "timestamp"})
    df_in["event"] = "Check-In"

    df_out = df.dropna(subset=["check_out_time"])
    df_out = df_out[df_out["check_out_time"].between(start_dt, end_dt, inclusive="both")][
        ["transaction_id", "employee_id", "asset_tag", "issue", "check_out_time"]
    ].rename(columns={"check_out_time": "timestamp"})
    df_out["event"] = "Check-Out"

    # Select events
    if kind == "Check-Ins":
        raw = df_in.copy()
    elif kind == "Check-Outs":
        raw = df_out.copy()
    else:  # Both
        raw = pd.concat([df_in, df_out], ignore_index=True).sort_values("timestamp")

    if raw.empty:
        empty_agg = pd.DataFrame(columns=["Period", "Check-In", "Check-Out", "Total"])
        empty_raw = pd.DataFrame(columns=["transaction_id", "employee_id", "asset_tag", "issue", "timestamp", "event", "Period"])
        return empty_agg, empty_raw, f"{kind} - {period} ({start_d} to {end_d})"

    # Period key
    if period == "Daily":
        raw["Period"] = raw["timestamp"].dt.strftime("%Y-%m-%d")
        title = f"{kind} - Daily ({start_d} to {end_d})"
    else:
        # Week starts Monday; label by week start date
        raw["Period"] = raw["timestamp"].dt.to_period("W-MON").apply(lambda p: p.start_time.strftime("%Y-%m-%d"))
        title = f"{kind} - Weekly (Mon-start) ({start_d} to {end_d})"

    # Aggregate counts by Period and Event
    agg = (
        raw.groupby(["Period", "event"])
           .size()
           .unstack(fill_value=0)   # columns: 'Check-In' and/or 'Check-Out'
           .reset_index()
    )
    for col in ("Check-In", "Check-Out"):
        if col not in agg.columns:
            agg[col] = 0
    agg["Total"] = agg["Check-In"] + agg["Check-Out"]
    agg = agg.sort_values("Period").reset_index(drop=True)

    # Order raw rows for nicer reading
    raw = raw.sort_values(["Period", "timestamp", "transaction_id"]).reset_index(drop=True)
    return agg, raw, title


# --- Chart helpers (png files) ---
def _chart_counts_by_period(agg_df: pd.DataFrame, title: str) -> str:
    """Create a side-by-side bar chart of Check-Ins vs Check-Outs per Period; return PNG filepath."""
    periods = agg_df["Period"].tolist()
    ins = agg_df["Check-In"].tolist()
    outs = agg_df["Check-Out"].tolist()
    n = len(periods)
    x = list(range(n))
    width = 0.4
    x1 = [i - width/2 for i in x]
    x2 = [i + width/2 for i in x]

    fig = plt.figure(figsize=(10, 4))
    plt.bar(x1, ins, width=width, label="Check-Ins")
    plt.bar(x2, outs, width=width, label="Check-Outs")
    plt.xticks(x, periods, rotation=45, ha="right")
    plt.ylabel("Count")
    plt.title(f"Counts by Period - {title}")
    plt.legend()
    plt.tight_layout()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix="_counts.png")
    fig.savefig(tmp.name, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return tmp.name

def _chart_top_issue_types(raw_df: pd.DataFrame, title: str, top_n: int = 10) -> str:
    """Create a bar chart of top issue types; return PNG filepath."""
    if raw_df.empty:
        fig = plt.figure(figsize=(8, 3))
        plt.text(0.5, 0.5, "No issue data", ha="center", va="center")
        plt.axis("off")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix="_issues.png")
        fig.savefig(tmp.name, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return tmp.name

    issue_types = raw_df["issue"].fillna("").astype(str).map(parse_issue_type)
    counts = issue_types.value_counts().head(top_n)
    fig = plt.figure(figsize=(8, 4))
    plt.barh(counts.index.tolist()[::-1], counts.values.tolist()[::-1])
    plt.xlabel("Count")
    plt.title(f"Top Issue Types - {title}")
    plt.tight_layout()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix="_issues.png")
    fig.savefig(tmp.name, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return tmp.name


# --- PDF text sanitizer (prevents FPDFUnicodeEncodingException) ---
def _pdf_text(s) -> str:
    """
    Replace common Unicode punctuation with ASCII so FPDF's core fonts accept it.
    Falls back to latin-1 replacement if anything remains non-encodable.
    """
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    repl = {
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2018": "'", "\u2019": "'",  # quotes
        "\u201c": '"', "\u201d": '"',
        "\u2026": "...",             # ellipsis
        "\u00a0": " ",               # nbsp
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    try:
        s.encode("latin-1")
        return s
    except UnicodeEncodeError:
        return s.encode("latin-1", "replace").decode("latin-1")


# --- Build a PDF report (bytes) ---
def build_report_pdf_bytes(display_title: str,
                           agg_df: pd.DataFrame,
                           raw_df: pd.DataFrame,
                           period: str,
                           kind: str,
                           start_d: date,
                           end_d: date) -> bytes:
    """
    Assemble a multi-page PDF with:
      - Cover summary (range, period, totals)
      - Counts-by-period chart
      - Top issue types chart
    Returns bytes suitable for Streamlit download_button.
    """
    # If no data, emit a simple one-page PDF and return
    if agg_df.empty and raw_df.empty:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, _pdf_text("SLAC Service Desk - Report"), ln=True, align="C")
        pdf.set_font("Arial", "", 12)
        pdf.ln(6)
        pdf.multi_cell(0, 8, _pdf_text(f"No data for {kind} in the selected range: {start_d} to {end_d}."), align="L")
        tmp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf"); tmp_pdf.close()
        pdf.output(tmp_pdf.name)
        with open(tmp_pdf.name, "rb") as f:
            data = f.read()
        os.remove(tmp_pdf.name)
        return data

    # Totals
    total_ins = int(agg_df["Check-In"].sum()) if not agg_df.empty else 0
    total_outs = int(agg_df["Check-Out"].sum()) if not agg_df.empty else 0
    total_events = total_ins + total_outs

    # Charts
    chart_paths = []
    try:
        chart_paths.append(_chart_counts_by_period(agg_df, display_title))
        chart_paths.append(_chart_top_issue_types(raw_df, display_title))

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)

        # Page 1: Summary
        pdf.add_page()
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, _pdf_text("SLAC Service Desk - Report"), ln=True, align="C")
        pdf.set_font("Arial", "", 12)
        pdf.ln(4)
        pdf.cell(0, 8, _pdf_text(f"Title: {display_title}"), ln=True)
        pdf.cell(0, 8, _pdf_text(f"Range: {start_d} to {end_d}"), ln=True)
        pdf.cell(0, 8, _pdf_text(f"Period: {period}"), ln=True)
        pdf.cell(0, 8, _pdf_text(f"Event Type: {kind}"), ln=True)
        pdf.ln(2)
        pdf.cell(0, 8, _pdf_text(f"Totals - Check-Ins: {total_ins}   Check-Outs: {total_outs}   All Events: {total_events}"), ln=True)
        pdf.ln(6)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, _pdf_text("Counts by Period"), ln=True)
        pdf.image(chart_paths[0], w=180)

        # Page 2: Top Issue Types
        pdf.add_page()
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, _pdf_text("Top Issue Types"), ln=True)
        pdf.image(chart_paths[1], w=180)

        # Output to bytes via temp file
        tmp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp_pdf.close()
        pdf.output(tmp_pdf.name)
        with open(tmp_pdf.name, "rb") as f:
            data = f.read()
        os.remove(tmp_pdf.name)
        return data
    finally:
        for p in chart_paths:
            try: os.remove(p)
            except Exception: pass


# ---------------- App UI ----------------
def system():
    tables()
    stl.title("SLAC Service Desk System")

    # Reports export to PDF only
    menu = ["Check-In", "Check-Out", "Dashboard", "Reports"]
    choice = stl.sidebar.selectbox("Menu", menu)

    # ---------- Check-In ----------
    if choice == "Check-In":
        stl.subheader("Laptop Check-In")
        employee_id = stl.text_input("Employee ID (numbers only)")
        asset_tag = stl.text_input("Laptop Asset Tag (letters/numbers allowed)")
        employee_name = stl.text_input("Employee Name (optional)")
        employee_email = stl.text_input("Employee Email (for receipt, optional)", placeholder="name@domain.com")

        issue_type = stl.selectbox(
            "Issue Type",
            ["Hardware Failure", "Software Request", "Performance Issue", "Account Lockout", "Other"]
        )
        issue_details = stl.text_area("Provide more details about the issue")
        full_issue_description = f"{issue_type}: {issue_details}"

        if stl.button("Check-In"):
            if employee_id and asset_tag and issue_details:
                try:
                    emp_id_int = int(str(employee_id).strip())
                except ValueError:
                    stl.error("Employee ID must be a number.")
                    stl.stop()

                ensure_employee_exists(emp_id_int, employee_name or "", employee_email or "")
                ensure_laptop_exists(asset_tag)

                new_tx_id = check_in(emp_id_int, asset_tag, full_issue_description)
                details = get_transaction_details(new_tx_id)

                if employee_email:
                    upsert_employee(emp_id_int, employee_name, employee_email)

                if details:
                    email_receipt(details, "Check-In")

                stl.success(f"Laptop {asset_tag} checked in for Employee {emp_id_int}")
                stl.markdown("---")
                stl.subheader("Check-In Confirmation Receipt")
                if details:
                    stl.markdown(f"**Confirmation #:** `{confirmation_code(details[0])}`")
                    stl.markdown(f"**Transaction ID:** `{details[0]}`")
                    stl.markdown(f"**Employee ID:** `{details[1]}`")
                    stl.markdown(f"**Asset Tag:** `{details[2]}`")
                    stl.markdown(f"**Issue Reported:** {details[3]}")
                    stl.markdown(f"**Check-In Time:** {details[4]}")
                stl.balloons()
            else:
                stl.error("Employee ID, Asset Tag, and Issue Details are required.")

    # ---------- Check-Out ----------
    elif choice == "Check-Out":
        stl.subheader("Laptop Check-Out")

        active = view_active_transactions()
        if active.empty:
            stl.info("No laptops currently checked in.")
        else:
            search = stl.text_input(
                "Search for a device (by Tx ID, Asset Tag, Employee ID, Issue, or Time)",
                placeholder="Type here to filter the list below..."
            )
            filtered = active
            if search:
                s = search.lower()
                filtered = active[
                    active.apply(
                        lambda row: s in str(row["transaction_id"]).lower()
                        or s in str(row["asset_tag"]).lower()
                        or s in str(row["employee_id"]).lower()
                        or s in str(row["issue"]).lower()
                        or s in str(row["check_in_time"]).lower(),
                        axis=1
                    )
                ]

            stl.write("### Devices Ready for Check-Out")
            stl.dataframe(filtered, use_container_width=True)

            if not filtered.empty:
                filtered["label"] = filtered.apply(
                    lambda r: f"Tx#{r['transaction_id']} - {r['asset_tag']} (Employee {r['employee_id']})",
                    axis=1
                )

                selected = stl.selectbox(
                    "Select the device to Check-Out",
                    filtered["label"].tolist(),
                    index=None,
                    placeholder="Select a device from the list..."
                )

                if selected:
                    tx_id = filtered.loc[filtered["label"] == selected, "transaction_id"].values[0]
                    if stl.button("Confirm Check-Out"):
                        check_out(int(tx_id))
                        details = get_transaction_details(int(tx_id))

                        if details:
                            email_receipt(details, "Check-Out")

                        stl.success(f"Transaction {tx_id} checked out successfully.")
                        stl.markdown("---")
                        stl.subheader("Check-Out Confirmation Receipt")
                        if details:
                            stl.markdown(f"**Confirmation #:** `{confirmation_code(details[0])}`")
                            stl.markdown(f"**Transaction ID:** `{details[0]}`")
                            stl.markdown(f"**Employee ID:** `{details[1]}`")
                            stl.markdown(f"**Asset Tag:** `{details[2]}`")
                            stl.markdown(f"**Check-In Time:** {details[4]}")
                            stl.markdown(f"**Check-Out Time:** {details[5]}")
                        stl.balloons()
            else:
                stl.warning("No devices match your search.")

    # ---------- Dashboard ----------
    elif choice == "Dashboard":
        stl.subheader("Overview")
        active_df = view_active_transactions()
        completed_df = view_completed_transactions()

        search_query = stl.text_input("Search Active Check-Ins by Employee ID or Asset Tag")
        active_df['employee_id'] = active_df['employee_id'].astype(str)
        active_df['asset_tag'] = active_df['asset_tag'].astype(str)

        if search_query:
            active_df = active_df[
                active_df['employee_id'].str.contains(search_query, case=False, na=False) |
                active_df['asset_tag'].str.contains(search_query, case=False, na=False)
            ]

        stl.metric("Active Items at Service Desk", len(active_df))
        stl.markdown("---")

        stl.subheader("Active Transactions")
        if active_df.empty:
            stl.info("No active check-ins match your search.")
        else:
            active_df = active_df.rename(columns={
                'transaction_id': 'Tx ID',
                'employee_id': 'Employee ID',
                'asset_tag': 'Asset Tag',
                'issue': 'Issue Description',
                'check_in_time': 'Check-In Time'
            })
            stl.dataframe(active_df, use_container_width=True)

        stl.subheader("Completed Transactions")
        if completed_df.empty:
            stl.info("No completed transactions yet.")
        else:
            completed_df = completed_df.rename(columns={
                'transaction_id': 'Tx ID',
                'employee_id': 'Employee ID',
                'asset_tag': 'Asset Tag',
                'issue': 'Issue Description',
                'check_in_time': 'Check-In Time',
                'check_out_time': 'Check-Out Time'
            })
            stl.dataframe(completed_df, use_container_width=True)

    # ---------- Reports (with Single Day + PDF export) ----------
    elif choice == "Reports":
        stl.subheader("Daily & Weekly Reports")

        col1, col2, col3 = stl.columns([1, 1, 2])
        with col1:
            period = stl.radio("Period", ["Daily", "Weekly"], horizontal=True)
        with col2:
            kind = stl.selectbox("Event Type", ["Both", "Check-Ins", "Check-Outs"])
        with col3:
            single_day = stl.checkbox("Single day", value=False, help="Check to report for exactly one day")
            if single_day:
                picked_day = stl.date_input("Pick a day", value=date.today())
                if isinstance(picked_day, tuple):
                    start_d = end_d = picked_day[0]
                else:
                    start_d = end_d = picked_day
            else:
                default_start = date.today() - timedelta(days=6)
                default_end = date.today()
                date_range = stl.date_input("Date Range", (default_start, default_end))
                if isinstance(date_range, tuple) and len(date_range) == 2:
                    start_d, end_d = date_range
                else:
                    stl.warning("Please pick a start and end date.")
                    stl.stop()

        agg_df, raw_df, title = report_dataframe(period, kind, start_d, end_d)
        display_title = title
        if period == "Daily" and start_d == end_d:
            display_title = f"{kind} - {start_d} (Daily)"

        stl.markdown(f"### {display_title}")
        stl.dataframe(agg_df, use_container_width=True)

        total_ins = int(agg_df["Check-In"].sum()) if not agg_df.empty else 0
        total_outs = int(agg_df["Check-Out"].sum()) if not agg_df.empty else 0
        colA, colB, colC = stl.columns(3)
        colA.metric("Total Check-Ins", total_ins)
        colB.metric("Total Check-Outs", total_outs)
        colC.metric("Total Events", total_ins + total_outs)

        stl.markdown("#### Raw Rows")
        stl.dataframe(raw_df, use_container_width=True)

        # --- PDF Export ONLY ---
        pdf_bytes = build_report_pdf_bytes(
            display_title=display_title,
            agg_df=agg_df,
            raw_df=raw_df,
            period=period,
            kind=kind,
            start_d=start_d,
            end_d=end_d
        )
        fname_base = f"report_{period}_{kind}_{start_d}_{end_d}".replace(" ", "_")
        stl.download_button(
            "🖨️ Download Report (PDF)",
            data=pdf_bytes,
            file_name=f"{fname_base}.pdf",
            mime="application/pdf",
            use_container_width=True
        )


if __name__ == '__main__':
    system()
