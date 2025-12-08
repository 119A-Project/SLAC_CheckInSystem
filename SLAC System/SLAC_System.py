#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Sep 27 15:55:27 2025

@author: sachinkalahasti
"""
import sqlite3
import numpy as np
import streamlit as stl
import pandas as pd
from datetime import datetime, date, timedelta
import cv2 
from pyzbar.pyzbar import decode 
import os
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import io
import tempfile
import re
import math
import altair as alt

# PDF + Email
from fpdf import FPDF          # <- install package: fpdf2
import ssl
import certifi
import smtplib
from email.utils import formataddr
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------- UI chrome ----------------
stl.markdown(
    """
    <style>
        /* target the first image inside the sidebar */
        section[data-testid="stSidebar"] img {
            margin-top: -45px;
        }
    </style>
    """,
    unsafe_allow_html=True
)

with stl.sidebar:
    stl.image("static/logo.png", width=200)  # adjust width as needed
    stl.markdown("---")  # optional separator line.

# ---------------- Safe secrets helpers ----------------
def _get_secret(key, default=None):
    """Return a secret or default without crashing if secrets.toml is missing."""
    try:
        return stl.secrets.get(key, default)
    except Exception:
        return default

def _bool_secret(name, default=False):
    v = _get_secret(name, default)
    if isinstance(v, bool): return v
    if isinstance(v, (int, float)): return bool(v)
    if isinstance(v, str): return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)

# ---------------- DB helpers ----------------
def database_connection():
    connect = sqlite3.connect('checkin_system.db')
    return connect

def tables():
    connect = database_connection()
    cursor = connect.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS Employees (
            employee_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS Laptops (
            asset_tag INTEGER NOT NULL,
            model TEXT NOT NULL,
            description TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS Transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            asset_tag INTEGER NOT NULL,
            issue TEXT NOT NULL,
            check_in_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            check_out_time DATETIME,
            status TEXT CHECK(status IN ('Checked-In', 'Checked-Out')) DEFAULT 'Checked-In',
            FOREIGN KEY (employee_id) REFERENCES Employees(employee_id),
            FOREIGN KEY (asset_tag) REFERENCES Laptops(asset_tag)
        );
    """)
    connect.commit()
    connect.close()

def ensure_laptop_exists(asset_tag: str):
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

def check_in(employee_id, asset_tag, issue):
    ensure_laptop_exists(asset_tag)
    ensure_employee_exists(employee_id)  
    connect = database_connection()
    cur = connect.cursor()
    cur.execute("""
        INSERT INTO Transactions (employee_id, asset_tag, issue)
        VALUES (?, ?, ?)
    """, (int(employee_id), str(asset_tag), issue))
    new_id = cur.lastrowid
    connect.commit()
    connect.close()
    return new_id


def check_out(transaction_id):
    connect = database_connection()
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    connect.execute("""
        UPDATE Transactions
        SET check_out_time=?, status='Checked-Out'
        WHERE transaction_id=? AND status='Checked-In'
    """, (now, transaction_id))
    connect.commit()
    connect.close()


def view_active_transactions():
    connect = database_connection()
    df = pd.read_sql("""
        SELECT transaction_id, employee_id, asset_tag, issue, check_in_time
        FROM Transactions
        WHERE status='Checked-In'
        ORDER BY check_in_time DESC
    """, connect)
    df["check_in_time"] = pd.to_datetime(df["check_in_time"], errors="coerce")
    connect.close()
    return df


def view_completed_transactions():
    connect = database_connection()
    df = pd.read_sql("""
        SELECT transaction_id, employee_id, asset_tag, issue, check_in_time, check_out_time
        FROM Transactions
        WHERE status='Checked-Out'
        ORDER BY check_out_time DESC
    """, connect)
    connect.close()
    df["check_in_time"] = pd.to_datetime(df["check_in_time"], errors="coerce")
    df["check_out_time"] = pd.to_datetime(df["check_out_time"], errors="coerce")
    return df

def get_transaction_details(transaction_id):
    conn = database_connection()
    row = conn.execute("""
        SELECT transaction_id, employee_id, asset_tag, issue, check_in_time, check_out_time, status
        FROM Transactions WHERE transaction_id = ?
    """, (int(transaction_id),)).fetchone()
    conn.close()
    return row

def get_employee_meta(employee_id: int):
    conn = database_connection()
    row = conn.execute("SELECT name, email FROM Employees WHERE employee_id=?", (int(employee_id),)).fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return None, None

def scan_asset_tags():
    # Open the default camera (index 0)
    # Use cv2.CAP_DSHOW on Windows if you experience issues
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) 

    if not cap.isOpened():
        print("Error: Could not open camera.")
        return

    print("Camera opened successfully. Point the camera at an asset tag.")
    print("Press 'q' or 'Esc' to exit.")

    while True:
        # Read a frame from the camera
        ret, frame = cap.read()
        if not ret:
            print("Error: Failed to capture image.")
            break

        # Decode any barcodes/QR codes present in the frame
        decoded_objects = decode(frame)

        # Process detected objects
        for obj in decoded_objects:
            # Print the decoded data and type
            print(f"Detected Type: {obj.type}, Data: {obj.data.decode('utf-8')}")
            
            # Optionally draw a rectangle around the detected code
            points = obj.polygon
            if points:
                # If the points are not a list of lists, convert to numpy array for cv2.polylines
                if len(points) > 4: 
                    hull = cv2.convexHull(np.array([point for point in points], dtype=np.int32))
                    cv2.polylines(frame, [hull], True, (0, 255, 0), 2)
                else:
                    for i in range(len(points)):
                        cv2.line(frame, points[i], points[(i+1) % len(points)], (0, 255, 0), 2)

            # Put the data text near the barcode
            cv2.putText(frame, obj.data.decode('utf-8'), (obj.rect.left, obj.rect.top - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)


        # Display the frame
        cv2.imshow("Asset Tag Scanner", frame)

        # Break the loop when 'q' or 'Esc' key is pressed
        key = cv2.waitKey(1)
        if key & 0xFF == ord('q') or key == 27:
            break

    # Release the camera and close all windows
    cap.release()
    cv2.destroyAllWindows()

# ---------------- Email & PDF ----------------
def confirmation_code(transaction_id: int) -> str:
    return f"CN-{transaction_id:06d}"

def parse_issue_type(issue_text: str) -> str:
    return (issue_text.split(":", 1)[0] or "Issue").strip()

def create_pdf_receipt(tx_tuple, emp_name, emp_email, kind="Check-In"):
    transaction_id, employee_id, asset_tag, issue, check_in, check_out, status = tx_tuple
    cn = confirmation_code(transaction_id)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(190, 10, txt=f"SLAC Service Desk - {kind} Receipt", ln=True, align='C')
    pdf.set_font("Arial", size=11)
    pdf.ln(6)
    pdf.cell(190, 8, txt=f"Confirmation Number: {cn}", ln=True)
    pdf.cell(190, 8, txt=f"Transaction ID: {transaction_id}", ln=True)
    pdf.cell(190, 8, txt=f"Employee: {emp_name or ''} (ID: {employee_id})", ln=True)
    pdf.cell(190, 8, txt=f"Employee Email: {emp_email or '—'}", ln=True)
    pdf.cell(190, 8, txt=f"Asset Tag: {asset_tag}", ln=True)
    pdf.cell(190, 8, txt=f"Issue Type: {parse_issue_type(issue)}", ln=True)
    pdf.multi_cell(190, 8, txt=f"Issue Details: {issue}", align='L')
    pdf.cell(190, 8, txt=f"Check-In Time: {check_in}", ln=True)
    if kind == "Check-Out" and check_out:
        pdf.cell(190, 8, txt=f"Check-Out Time: {check_out}", ln=True)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_tx{transaction_id}.pdf")
    tmp.close()
    pdf.output(tmp.name)
    return tmp.name, cn

def send_email_with_attachment_smtp(to_addr, subject, html_body, attachment_path):
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
        # Use certifi CA bundle for TLS so macOS trust works reliably
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

def build_email_html(emp_name, employee_id, asset_tag, issue, check_in, check_out, cn, kind):
    return f"""
    <p>Hi {emp_name or 'there'},</p>
    <p>This is a confirmation that your device was <b>{kind.lower()}</b> at the Service Desk.</p>
    <table cellspacing="0" cellpadding="4" border="0">
      <tr><td><b>Employee</b></td><td>{emp_name or ''} (ID: {employee_id})</td></tr>
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
    transaction_id, employee_id, asset_tag, issue, check_in, check_out, check_out = tx_tuple
    emp_name, emp_email = get_employee_meta(employee_id)
    if not emp_email:
        stl.warning(f"No email on file for employee {employee_id}. Skipping {kind} email.")
        return False
    pdf_path, cn = create_pdf_receipt(tx_tuple, emp_name, emp_email, kind)
    try:
        subject = "From the Service Desk General Inbox"
        html = build_email_html(emp_name, employee_id, asset_tag, issue, check_in, check_out, cn, kind)
        ok = send_email_with_attachment_smtp(emp_email, subject, html, pdf_path)
        if ok:
            stl.info(f"{kind} confirmation emailed to {emp_email}.")
        return ok
    finally:
        try: os.remove(pdf_path)
        except Exception: pass

# ---------------- Reporting + PDF Export ----------------
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

    for col in ["transaction_id", "employee_id", "asset_tag"]:
        raw[col] = raw[col].astype(str)

    raw = raw.rename(columns={
        "transaction_id": "Transaction ID",
        "employee_id": "Employee ID",
        "asset_tag": "Asset Tag",
        "issue": "Issue Description",
        "timestamp": "Date/Time",
        "event": "Event Type",
    })

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

    issue_types = raw_df["Issue Description"].fillna("").astype(str).map(parse_issue_type)
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

# ---------------- Status tag helpers ----------------
GREEN_BG  = "#e8f5e9"   # ✅ Resolved
YELLOW_BG = "#fff8e1"   # 🟡 Waiting
RED_BG    = "#ffebee"   # 🔴 Overdue
DEFAULT_BG = ""         # 🆕 New — no tint

def _ensure_dt(df: pd.DataFrame, cols):
    """
    Parse timestamps as UTC-aware to avoid negative ages from TZ mismatches.
    """
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", utc=True)
    return df

def compute_status_tag(df: pd.DataFrame, *, completed: bool) -> pd.DataFrame:
    """
    Completed -> ✅ Resolved (green)
    Active:
      < 4h     -> 🆕 New (no tint)
      4–24h    -> 🟡 Waiting (yellow)
      ≥ 24h    -> 🔴 Overdue (red)
    Age (hrs) is hours since check-in, displayed as WHOLE numbers (no decimals).
    """
    df = _ensure_dt(df, ["check_in_time", "check_out_time"])

    if completed:
        out = df.copy()
        # raw fractional hours, clamp >= 0
        age_hours = np.maximum(
            (out["check_out_time"] - out["check_in_time"]).dt.total_seconds() / 3600.0,
            0
        )
        # display as whole numbers
        out["Age (hrs)"] = age_hours.astype(int)
        out["Status"] = "✅ Resolved"
        return out

    now = pd.Timestamp.now(tz="UTC")
    out = df.copy()
    # raw fractional hours, clamp >= 0
    age_hours = np.maximum(
        (now - out["check_in_time"]).dt.total_seconds() / 3600.0,
        0
    )
    # status uses precise age (not rounded) for correct thresholds
    out["Status"] = np.select(
        [
            age_hours < 4,
            (age_hours >= 4) & (age_hours < 24),
            age_hours >= 24
        ],
        ["🆕 New", "🟡 Waiting", "🔴 Overdue"],
        default="🆕 New"
    )
    # display as whole numbers
    out["Age (hrs)"] = age_hours.astype(int)
    return out

def style_by_status(df: pd.DataFrame):
    """Tint rows by status; 'New' stays default background."""
    def row_bg(row):
        s = str(row.get("Status", ""))
        if "Overdue" in s:
            bg = RED_BG
        elif "Waiting" in s:
            bg = YELLOW_BG
        elif "Resolved" in s:
            bg = GREEN_BG
        elif "New" in s:
            bg = DEFAULT_BG
        else:
            bg = ""
        return [f"background-color: {bg}" if bg else "" for _ in row]
    return df.style.apply(row_bg, axis=1)

def color_counts(df: pd.DataFrame):
    """Return (reds, yellows, news) for the small summary meters."""
    vc = df.get("Status", pd.Series(dtype=str)).value_counts()
    reds    = int(vc.get("🔴 Overdue", 0))
    yellows = int(vc.get("🟡 Waiting", 0))
    news    = int(vc.get("🆕 New", 0))
    return reds, yellows, news


# ---------------- Simple validators (added) ----------------
def _is_valid_email(addr: str) -> bool:
    """Very basic email shape check. Accepts 'name@domain.tld' with a 2+ char TLD."""
    if not addr:
        return False
    addr = addr.strip()
    return re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", addr) is not None        

# --- PAGINATION HELPER (Dashboard only) ---
def paginated_table(df, key: str, rename_cols=None, default_page_size: int = 25, height: int = 420):
    """
    Render a paginated (and scrollable) dataframe on the Dashboard.
    - df: DataFrame to render
    - key: unique key prefix (each table needs its own)
    - rename_cols: optional display-only rename mapping
    - default_page_size: initial rows per page
    - height: table pixel height (internal scrollbar)
    """
    if df is None or df.empty:
        stl.info("No rows to display.")
        return

    view = df.rename(columns=rename_cols) if rename_cols else df

    left, mid, right = stl.columns([1, 1, 2])
    with left:
        page_size = stl.selectbox(
            "Rows / page",
            options=[10, 25, 50, 100],
            index=[10, 25, 50, 100].index(default_page_size) if default_page_size in [10, 25, 50, 100] else 1,
            key=f"{key}_page_size"
        )
    total = len(view)
    total_pages = max(1, math.ceil(total / page_size))

    if f"{key}_page" not in stl.session_state:
        stl.session_state[f"{key}_page"] = 1

    with mid:
        current_page = stl.number_input(
            "Page",
            min_value=1, max_value=total_pages,
            value=stl.session_state[f"{key}_page"],
            step=1,
            key=f"{key}_page_input"
        )
        stl.session_state[f"{key}_page"] = int(current_page)

    with right:
        c1, c2 = stl.columns(2)
        with c1:
            if stl.button("⟵ Prev", disabled=stl.session_state[f"{key}_page"] <= 1, key=f"{key}_prev"):
                stl.session_state[f"{key}_page"] -= 1
        with c2:
            if stl.button("Next ⟶", disabled=stl.session_state[f"{key}_page"] >= total_pages, key=f"{key}_next"):
                stl.session_state[f"{key}_page"] += 1

    p = stl.session_state[f"{key}_page"]
    start = (p - 1) * page_size
    end = start + page_size
    page_df = view.iloc[start:end].copy()

    stl.caption(f"Showing {start+1}-{min(end, total)} of {total} rows")
    stl.dataframe(page_df, use_container_width=True, height=height)

# ---------------- App UI ----------------
def system():
    tables()
    stl.title("SLAC Service Desk System")

    menu = ["Check-In", "Check-Out", "Dashboard", "Reports"]
    choice = stl.sidebar.selectbox("Menu", menu, help="Choose a section of the Service Desk system.")

    if choice == "Check-In":
        stl.subheader("Laptop Check-In")
        if "scanned_asset_tag" not in stl.session_state:
            stl.session_state.scanned_asset_tag = ""
        if "asset_tag_input_key" not in stl.session_state:
            stl.session_state.asset_tag_input_key = 0
        
        if "checkin_canvas_key" not in stl.session_state:
            stl.session_state.checkin_canvas_key = 0

        # ---------------- CALLBACK FUNCTION ----------------
        def clear_checkin_form():
            """Reset all Check-In session states here"""
            stl.session_state["checkin_emp_id"] = ""
            stl.session_state["checkin_emp_name"] = ""
            stl.session_state["checkin_emp_email"] = ""
            stl.session_state["checkin_issue_details"] = ""
            stl.session_state.scanned_asset_tag = ""
            stl.session_state.asset_tag_input_key += 1
            stl.session_state.checkin_canvas_key += 1

        employee_id = stl.text_input("Employee ID",  help="Enter the internal Employee ID number (required).", key="checkin_emp_id")
        asset_tag = stl.text_input(
            "Laptop Asset Tag",
            value=stl.session_state.scanned_asset_tag,
            key=f"asset_tag_input{stl.session_state.asset_tag_input_key}", 
            help="Enter the laptop's asset tag (required)."
        )
        employee_name = stl.text_input("Employee Name", help="Enter your name (required).", key="checkin_emp_name")
        employee_email = stl.text_input("Employee Email (Optional)", placeholder="name@domain.com", 
                                        help="Enter your email address (used for sending receipt).", key="checkin_emp_email")

        picture = stl.camera_input("Take a picture of the asset tag", help="Use your camera to scan the laptop's asset tag barcode/QR code.")
        
        if picture:
            bytes_data = picture.getvalue()
            img = cv2.imdecode(np.frombuffer(bytes_data, np.uint8), cv2.IMREAD_COLOR)
            decoded = decode(img)

            found_new_tag = False
            value = None

            if decoded:
                value = decoded[0].data.decode('utf-8')

            if value != stl.session_state.scanned_asset_tag:
                stl.session_state.scanned_asset_tag = value
                stl.session_state.asset_tag_input_key += 1  # triggers new input render
                stl.success(f"Scanned Asset Tag: {value}")
                found_new_tag = True
                stl.rerun() 
            
            if not found_new_tag and not decoded:
                stl.warning("No barcode detected. Try again.")

        issue_type = stl.selectbox(
            "Issue Type",
            ["Hardware Failure", "Software Request", "Performance Issue", "Account Lockout", "Other"], 
            help="Choose the closest category that matches the reported issue.", 
            key="checkin_issue_type"
        )
        issue_details = stl.text_area("Provide more details about the issue", 
                                      help="Include symptoms, error messages, when it started, and anything else that might help.", key="checkin_issue_details")
        full_issue_description = f"{issue_type}: {issue_details}"

        stl.write("Please provide your digital signature below:")
        
        canvas_result = st_canvas(
            fill_color="rgba(255, 255, 255, 0)",
            stroke_width=2,
            stroke_color="black",
            background_color="white",
            height=150,
            width=400,
            drawing_mode="freedraw",
            key="signature_canvas",
        )

        signature_data = None
        if canvas_result is not None and canvas_result.image_data is not None:
            arr = canvas_result.image_data
            if arr.max() <= 1.0:
                arr = (arr * 255.0)
            rgb = arr[:, :, :3].astype("uint8")

            if (rgb != 255).any():
                img = Image.fromarray(rgb)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                signature_data = buf.getvalue()

        # Layout for Confirm and Clear buttons side by side
        col1, col2 = stl.columns([1, 1])

        with col1:
            confirm_btn = stl.button("Confirm Check-In", use_container_width=True)
        
        with col2:
            # We use on_click to fire the callback BEFORE the rerun happens
            stl.button("Clear Form", on_click=clear_checkin_form, use_container_width=True)

        if confirm_btn:
            if not (employee_id and asset_tag and issue_details):
                stl.error("Employee ID, Asset Tag, and Issue Details are required.")
                stl.stop()
        
            try:
                emp_id_int = int(str(employee_id).strip())
                if emp_id_int <= 0:
                    stl.error("Employee ID must be a positive number.")
                    stl.stop()
            except ValueError:
                stl.error("Employee ID must be a number.")
                stl.stop()

            if not (canvas_result.json_data and any(obj.get("path") for obj in canvas_result.json_data.get("objects", []))):
                stl.error("Signature is required. Please sign in the box above.")
                stl.stop()

            if employee_email and (not _is_valid_email(employee_email)):
                stl.error("Please enter a valid email address (e.g., name@example.com).")
                stl.stop()

            conn = database_connection()
            exists = conn.execute(
                "SELECT 1 FROM Transactions WHERE asset_tag=? AND status='Checked-In' LIMIT 1",
                (str(asset_tag),)
            ).fetchone()
            conn.close()
            if exists:
                stl.error(f"Device {asset_tag} is already checked in. Check it out before creating a new check-in.")
                stl.stop()
                
            ensure_employee_exists(emp_id_int, employee_name or "", employee_email or "")
            ensure_laptop_exists(asset_tag)

            new_tx_id = check_in(emp_id_int, asset_tag, full_issue_description)

            os.makedirs("signatures", exist_ok=True)
            file_path = f"signatures/signature_{employee_id}_{asset_tag}.png"
            with open(file_path, "wb") as f:
                f.write(signature_data)

            details = get_transaction_details(new_tx_id)

            # Update name/email if provided (optional)
            if employee_email:
                upsert_employee(emp_id_int, employee_name, employee_email)

            # Email + PDF
            if details:
                email_sent = email_receipt(details, "Check-In")
                tx_id = details[0]
                conf = confirmation_code(tx_id)
                summary = f"Tx #{tx_id} · {conf} · Emp {emp_id_int} · Asset {asset_tag}"
                if email_sent:
                    stl.success(f"Check-In complete. {summary}. Receipt emailed.")
                else:
                    stl.info(f"Check-In complete (email not sent). {summary}.")
            else:
                stl.error("Check-In created, but details could not be retrieved.")

            # On-screen receipt
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

    elif choice == "Check-Out":
        stl.subheader("Laptop Check-Out")
        active = view_active_transactions()

        if "checkout_canvas_key" not in stl.session_state:
            stl.session_state.checkout_canvas_key = 0
        
        if active.empty:
            stl.info("No laptops currently checked in.")
        else:
            # ---------------- CALLBACK FUNCTION ----------------
            def clear_checkout_form():
                """Reset all Check-Out session states here"""
                stl.session_state["checkout_search"] = ""
                stl.session_state["checkout_select"] = None
                stl.session_state.checkout_canvas_key += 1    

            search = stl.text_input(
                "Search for a device (by Asset Tag, Employee ID, or Issue)",
                placeholder="Type here and press Enter...", 
                help="Start typing to filter active tickets. Press Enter to update the list.",
                key="checkout_search"
            )

            filtered = active
            if search:
                filtered = active[
                    active.apply(
                        lambda row: search.lower() in str(row["transaction_id"]).lower()
                        or search.lower() in str(row["asset_tag"]).lower()
                        or search.lower() in str(row["employee_id"]).lower()
                        or search.lower() in str(row["issue"]).lower()
                        or search.lower() in str(row["check_in_time"]).lower(),
                        axis=1
                    )
                ]

            if search and filtered.empty:
                stl.warning("No matching devices found.")
            elif search:
                stl.write("### Matching Devices")

                filtered = compute_status_tag(filtered, completed=False)
                nice = filtered.rename(columns={
                    "transaction_id": "Transaction ID",
                    "employee_id": "Employee ID",
                    "asset_tag": "Asset Tag",
                    "issue": "Issue Description",
                    "check_in_time": "Check-In Time"
                })[["Transaction ID", "Employee ID", "Asset Tag", "Issue Description", "Check-In Time", "Age (hrs)", "Status"]]
                stl.dataframe(style_by_status(nice), use_container_width=True)
                
                filtered["label"] = filtered.apply(
                    lambda r: f"Transaction {r['transaction_id']} - {r['asset_tag']} (Employee {r['employee_id']})",
                    axis=1
                )

                selected = stl.selectbox(
                    "Select the device to Check-Out",
                    filtered["label"].tolist(),
                    index=None,
                    placeholder="Select a device from the list...", 
                    help="Select the correct transaction, then click 'Confirm Check-Out'.", 
                    key="checkout_select"
                )

                if selected:
                    transaction_id = filtered.loc[filtered["label"] == selected, "transaction_id"].values[0]

                    stl.markdown("### Please sign below to confirm the check-out:")
                    
                    canvas_result = st_canvas(
                       fill_color="white",
                       stroke_width=2,
                       stroke_color="black",
                       background_color="white",
                       width=400,
                       height=150,
                       drawing_mode="freedraw",
                       key="signature_canvas"
                    )

                    col1, col2 = stl.columns([1, 1])

                    with col1:
                        confirm_btn = stl.button("Confirm Check-Out", use_container_width=True)
                    with col2:
                        # Use on_click callback to prevent StreamlitAPIException
                        stl.button("Clear Form", on_click=clear_checkout_form, use_container_width=True)

                    if confirm_btn:
                        if not (canvas_result.json_data and any(obj.get("path") for obj in canvas_result.json_data.get("objects", []))):
                            stl.error("Signature is required. Please sign in the box above.")
                            stl.stop()

                        # ---- Your actual checkout logic goes here ----
                        check_out(int(transaction_id))
    
                        details = get_transaction_details(int(transaction_id))

                        if details:
                            email_sent = email_receipt(details, "Check-Out")
                            tid = details[0]
                            conf = confirmation_code(tid)
                            summary = f"Tx #{tid} · {conf} · Emp {details[1]} · Asset {details[2]}"
                            if email_sent:
                                stl.success(f"Check-Out complete. {summary}. Receipt emailed.")
                            else:
                                stl.info(f"Check-Out complete (email not sent). {summary}.")
                        else:
                            stl.error("Check-Out updated, but details could not be retrieved.")

                        stl.markdown("---")
                        stl.subheader("Check-Out Confirmation Receipt")
                            
                        if details:
                            stl.markdown(f"**Confirmation #:** `{confirmation_code(details[0])}`")
                            stl.markdown(f"**Transaction ID:** `{details[0]}`")
                            stl.markdown(f"**Employee ID:** `{details[1]}`")
                            stl.markdown(f"**Asset Tag:** `{details[2]}`")
                            stl.markdown(f"**Check-In Time:** {details[4]}")
                            stl.markdown(f"**Check-Out Time:** {details[5]}")

    elif choice == "Dashboard":
        active_df = view_active_transactions()
        completed_df = view_completed_transactions()

        search_query = stl.text_input("Search Active Check-Ins by Employee ID or Asset Tag")
        
        if not active_df.empty:
            active_df = active_df.copy()
            active_df['employee_id'] = active_df['employee_id'].astype(str)
            active_df['asset_tag'] = active_df['asset_tag'].astype(str)
            active_f = active_df.copy()

            if search_query:
                active_f = active_df[
                    active_df['employee_id'].str.contains(search_query, case=False, na=False) |
                    active_df['asset_tag'].str.contains(search_query, case=False, na=False)
                ]

            # Additional processing for Dashboard tabs (Issue Type, Duration)
            active_f["issue_type"] = active_f["issue"].apply(parse_issue_type)
        else:
             active_f = pd.DataFrame()
        
        if not completed_df.empty:
            completed_f = completed_df.copy()
            completed_f = compute_status_tag(completed_f, completed=True)
            completed_f["duration_hours"] = completed_f["Age (hrs)"] # Duration is 'Age' when completed=True
            completed_f["issue_type"] = completed_f["issue"].apply(parse_issue_type)
        else:
            completed_f = pd.DataFrame()

        stl.metric("Active Items at Service Desk", len(active_f))
        stl.subheader("Active Transactions")
        if active_f.empty:
            stl.info("No active check-ins match your search.")
        else:
            active_f = compute_status_tag(active_f, completed=False)

            r, y, n = color_counts(active_f)
            c1, c2, c3 = stl.columns(3)
            c1.metric("🔴 Overdue", r)
            c2.metric("🟡 Waiting", y)
            c3.metric("🆕 New", n)

            active_pretty = active_f.rename(columns={
                'transaction_id': 'Transaction ID',
                'employee_id': 'Employee ID',
                'asset_tag': 'Asset Tag',
                'issue': 'Issue Description',
                'check_in_time': 'Check-In Time'
            })[["Transaction ID", "Employee ID", "Asset Tag", "Issue Description", "Check-In Time", "Age (hrs)", "Status"]]
            stl.dataframe(style_by_status(active_pretty).set_properties(
                **{'color': 'black'}), use_container_width=True)
        stl.subheader("Completed Transactions")
        if completed_df.empty:
            stl.info("No completed transactions yet.")
        else:
            completed_df = compute_status_tag(completed_df, completed=True)

            completed_pretty = completed_df.rename(columns={
                'transaction_id': 'Transaction ID',
                'employee_id': 'Employee ID',
                'asset_tag': 'Asset Tag',
                'issue': 'Issue Description',
                'check_in_time': 'Check-In Time',
                'check_out_time': 'Check-Out Time'
            })[["Transaction ID", "Employee ID", "Asset Tag", "Issue Description", "Check-In Time", "Check-Out Time", "Age (hrs)", "Status"]]

            completed_pretty["Transaction ID"] = completed_pretty["Transaction ID"].astype(str)
            completed_pretty["Employee ID"] = completed_pretty["Employee ID"].astype(str)
            completed_pretty["Asset Tag"] = completed_pretty["Asset Tag"].astype(str)
            stl.dataframe(style_by_status(completed_pretty).set_properties(
                **{'color': 'black'}), use_container_width=True)

    elif choice == "Reports":
        stl.subheader("Daily & Weekly Reports")

        active_df_all = view_active_transactions()
        completed_df_all = view_completed_transactions()

        if not active_df_all.empty:
            active_df_all = active_df_all.copy()
            active_df_all['employee_id'] = active_df_all['employee_id'].astype(str)
            active_df_all['asset_tag'] = active_df_all['asset_tag'].astype(str)
            active_f = active_df_all.copy()
            active_f["issue_type"] = active_f["issue"].apply(parse_issue_type)
        else:
             active_f = pd.DataFrame()
        
        # Calculate completed_f (all completed transactions for dashboard tabs)
        if not completed_df_all.empty:
            completed_f = completed_df_all.copy()
            completed_f = compute_status_tag(completed_f, completed=True)
            completed_f["duration_hours"] = completed_f["Age (hrs)"] # Duration is 'Age' when completed=True
            completed_f["issue_type"] = completed_f["issue"].apply(parse_issue_type)
        else:
            completed_f = pd.DataFrame()

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

        # Spinner while building the report
        with stl.spinner("Building report…"):
            agg_df, raw_df, title = report_dataframe(period, kind, start_d, end_d)

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

                # ----- Dashboard Tabs -----
        stl.markdown("---")
        tab_overview, tab_activity, tab_issues, tab_logs = stl.tabs(
            ["Overview", "Daily Activity", "Issue Types", "Logs"]
        )

        # ========== TAB 1: OVERVIEW ==========
        with tab_overview:
            stl.subheader("At a Glance")
            k1, k2, k3 = stl.columns(3)
            k1.metric("Active Items at Service Desk", len(active_f) if not active_f.empty else 0)
            k2.metric("Completed in Range", len(completed_f) if not completed_f.empty else 0)
            avg_turn = (
                completed_f["duration_hours"].mean()
                if (not completed_f.empty and "duration_hours" in completed_f.columns)
                else float("nan")
            )
            k3.metric("Avg Turnaround (hrs)", f"{avg_turn:.2f}" if pd.notna(avg_turn) else "—")

            stl.markdown(
                """
                - **Active Items at Service Desk**: Open tickets that are still checked in.
                - **Completed in Range**: Tickets checked out whose check-in dates fall within your selected range.
                - **Avg Turnaround**: Average time from check-in to check-out, in hours.
                """
            )

        # ========== TAB 2: DAILY ACTIVITY ==========
        with tab_activity:
            stl.subheader("Daily Activity")

            # --- Daily check-ins
            if not active_f.empty and "check_in_time" in active_f.columns:
                ci_daily = (
                    active_f.assign(date=active_f["check_in_time"].dt.date)
                            .groupby("date").size().rename("check_ins").to_frame()
                )
                stl.write("**Check-Ins per Day**")
                _ci = ci_daily.reset_index().rename(columns={"date": "Date", "check_ins": "Check-Ins"})
                _ci["Check-Ins"] = _ci["Check-Ins"].astype(int)

                max_ci = int(_ci["Check-Ins"].max()) if len(_ci) else 0
                domain_max_ci = max(1, max_ci)

                # X-only zoom/pan, lock Y >= 0
                x_zoom_ci = alt.selection_interval(bind='scales', encodings=['x'])

                chart_ci = (
                    alt.Chart(_ci)
                    .transform_calculate(Clipped="max(datum['Check-Ins'], 0)")
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("Date:T", title="Date"),
                        y=alt.Y(
                            "Clipped:Q",
                            title="Count",
                            scale=alt.Scale(domainMin=0, domainMax=domain_max_ci, clamp=True, nice=False),
                            axis=alt.Axis(format="d", tickMinStep=1)
                        ),
                        tooltip=[alt.Tooltip("Date:T"), alt.Tooltip("Clipped:Q", title="Check-Ins", format="d")]
                    )
                    .properties(height=260, width=1200)
                    .add_params(x_zoom_ci)
                    .configure_scale(clamp=True)
                )

                stl.altair_chart(chart_ci, use_container_width=True)
            else:
                stl.info("No active check-ins for the selected filters.")

            # --- Daily check-outs
            if not completed_f.empty and "check_out_time" in completed_f.columns:
                co_daily = (
                    completed_f.assign(date=completed_f["check_out_time"].dt.date)
                               .dropna(subset=["date"])
                               .groupby("date").size().rename("check_outs").to_frame()
                )
                stl.write("**Check-Outs per Day**")
                _co = co_daily.reset_index().rename(columns={"date": "Date", "check_outs": "Check-Outs"})
                _co["Check-Outs"] = _co["Check-Outs"].astype(int)

                max_co = int(_co["Check-Outs"].max()) if len(_co) else 0
                domain_max_co = max(1, max_co)

                x_zoom_co = alt.selection_interval(bind='scales', encodings=['x'])

                chart_co = (
                    alt.Chart(_co)
                    .transform_calculate(Clipped="max(datum['Check-Outs'], 0)")
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("Date:T", title="Date"),
                        y=alt.Y(
                            "Clipped:Q",
                            title="Count",
                            scale=alt.Scale(domainMin=0, domainMax=domain_max_co, clamp=True, nice=False),
                            axis=alt.Axis(format="d", tickMinStep=1)
                        ),
                        tooltip=[alt.Tooltip("Date:T"), alt.Tooltip("Clipped:Q", title="Check-Outs", format="d")]
                    )
                    .properties(height=260, width=1200)
                    .add_params(x_zoom_co)
                    .configure_scale(clamp=True)
                )

                stl.altair_chart(chart_co, use_container_width=True)
            else:
                stl.info("No completed check-outs for the selected filters.")

        # ========== TAB 3: ISSUE TYPES ==========
        with tab_issues:
            stl.subheader("Top Issue Types")
            issue_src = pd.concat(
                [
                    active_f[["issue_type"]] if ("issue_type" in active_f.columns and not active_f.empty) else pd.DataFrame(columns=["issue_type"]),
                    completed_f[["issue_type"]] if ("issue_type" in completed_f.columns and not completed_f.empty) else pd.DataFrame(columns=["issue_type"]),
                ],
                axis=0,
                ignore_index=True
            )
            if not issue_src.empty:
                top_issues = (
                    issue_src["issue_type"]
                    .value_counts()
                    .rename_axis("Issue Type")
                    .to_frame("Count")
                    .reset_index()
                )
                top_issues["Count"] = top_issues["Count"].astype(int)

                max_count = int(top_issues["Count"].max()) if len(top_issues) else 0
                domain_max_count = max(1, max_count)

                x_zoom_issues = alt.selection_interval(bind='scales', encodings=['x'])

                chart_issues = (
                    alt.Chart(top_issues)
                    .transform_calculate(Clipped="max(datum['Count'], 0)")
                    .mark_bar()
                    .encode(
                        x=alt.X("Issue Type:N", sort="-y", title="Issue Type"),
                        y=alt.Y(
                            "Clipped:Q",
                            title="Count",
                            scale=alt.Scale(domainMin=0, domainMax=domain_max_count, clamp=True, nice=False),
                            axis=alt.Axis(format="d", tickMinStep=1)
                        ),
                        tooltip=[alt.Tooltip("Issue Type:N"), alt.Tooltip("Clipped:Q", title="Count", format="d")]
                    )
                    .properties(height=300, width=1200)
                    .add_params(x_zoom_issues)
                    .configure_scale(clamp=True)
                )

                stl.altair_chart(chart_issues, use_container_width=True)
            else:
                stl.info("No issues to summarize for the selected filters.")

        # ========== TAB 4: LOGS ==========
        with tab_logs:
            stl.subheader("Recent Completed (with duration)")
            if not completed_f.empty:
                cols = ["transaction_id", "employee_id", "asset_tag", "issue", "issue_type",
                        "check_in_time", "check_out_time"]
                if "duration_hours" in completed_f.columns:
                    cols.append("duration_hours")
                show = completed_f[cols].sort_values("check_out_time", ascending=False).copy()

                rename_map = {
                    "transaction_id": "Tx ID",
                    "employee_id": "Employee ID",
                    "asset_tag": "Asset Tag",
                    "issue": "Issue Description",
                    "issue_type": "Issue Type",
                    "check_in_time": "Check-In Time",
                    "check_out_time": "Check-Out Time",
                    "duration_hours": "Duration (hrs)"
                }
                if "duration_hours" in show.columns:
                    show["duration_hours"] = show["duration_hours"].round(2)

                    show["transaction_id"] = show["transaction_id"].astype(str)
                    show["employee_id"] = show["employee_id"].astype(str)
                    show["asset_tag"] = show["asset_tag"].astype(str)

                paginated_table(
                    show,
                    key="dash_completed_recent",
                    rename_cols=rename_map,
                    default_page_size=25,
                    height=420
                )
            else:
                stl.info("No completed transactions in the selected range.")
        stl.markdown("---")

        # --- Spinner while rendering PDF ---
        with stl.spinner("Rendering PDF…"):
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
            use_container_width=True, 
            help="Export the currently filtered completed tickets as a PDF file."
        )


if __name__ == '__main__':
    system()
