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
from datetime import datetime
import cv2 
from pyzbar.pyzbar import decode 
import os
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import io
import tempfile

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

script_dir =  script_dir = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(script_dir):
    os.chdir(script_dir)

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
def confirmation_code(tx_id: int) -> str:
    return f"CN-{tx_id:06d}"

def parse_issue_type(issue_text: str) -> str:
    return (issue_text.split(":", 1)[0] or "Issue").strip()

def create_pdf_receipt(tx_tuple, emp_name, emp_email, kind="Check-In"):
    tx_id, employee_id, asset_tag, issue, check_in, check_out, _ = tx_tuple
    cn = confirmation_code(tx_id)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(190, 10, txt=f"SLAC Service Desk - {kind} Receipt", ln=True, align='C')
    pdf.set_font("Arial", size=11)
    pdf.ln(6)
    pdf.cell(190, 8, txt=f"Confirmation Number: {cn}", ln=True)
    pdf.cell(190, 8, txt=f"Transaction ID: {tx_id}", ln=True)
    pdf.cell(190, 8, txt=f"Employee: {emp_name or ''} (ID: {employee_id})", ln=True)
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
    tx_id, employee_id, asset_tag, issue, check_in, check_out, _ = tx_tuple
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

# ---------------- App UI ----------------
def system():
    tables()
    stl.title("SLAC Service Desk System")

    menu = ["Check-In", "Check-Out", "Dashboard"]
    choice = stl.sidebar.selectbox("Menu", menu)

    if choice == "Check-In":
        stl.subheader("Laptop Check-In")
        if "scanned_asset_tag" not in stl.session_state:
            stl.session_state.scanned_asset_tag = ""
        if "asset_tag_input_key" not in stl.session_state:
            stl.session_state.asset_tag_input_key = 0
        
        employee_id = stl.text_input("Employee ID")
        asset_tag = stl.text_input(
            "Laptop Asset Tag",
            value=stl.session_state.scanned_asset_tag,
            key=f"asset_tag_input{stl.session_state.asset_tag_input_key}"
        )
        employee_name = stl.text_input("Employee Name")
        employee_email = stl.text_input("Employee Email", placeholder="name@domain.com")

        picture = stl.camera_input("Take a picture of the asset tag")
        
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
            ["Hardware Failure", "Software Request", "Performance Issue", "Account Lockout", "Other"]
        )
        issue_details = stl.text_area("Provide more details about the issue")
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

        if stl.button("Confirm Check-In"):
            if not (employee_id and asset_tag and issue_details):
                stl.error("Employee ID, Asset Tag, and Issue Details are required.")
                stl.stop()
            try:
                emp_id_int = int(str(employee_id).strip())
            except ValueError:
                stl.error("Employee ID must be a number.")
                stl.stop()

            if not (canvas_result.json_data and any(obj.get("path") for obj in canvas_result.json_data.get("objects", []))):
                stl.error("Signature is required. Please sign in the box above.")
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
                email_receipt(details, "Check-In")
    
            # On-screen receipt
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

    elif choice == "Check-Out":
        stl.subheader("Laptop Check-Out")
        active = view_active_transactions()
        
        if active.empty:
            stl.info("No laptops currently checked in.")
        else:
            search = stl.text_input(
                "Search for a device (by Asset Tag, Employee ID, or Issue)",
                placeholder="Type here and press Enter..."
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
                stl.dataframe(filtered, use_container_width=True)

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

                    if stl.button("Confirm Check-Out"):
                       if canvas_result.json_data and any(obj.get("path") for obj in canvas_result.json_data.get("objects", [])):
                           check_out(int(tx_id))
                           details = get_transaction_details(int(tx_id))

                           if details:
                                email_receipt(details, "Check-Out")

                           stl.success(f"Transaction {tx_id} checked out successfully.")
                           stl.balloons()

                           stl.markdown("---")
                           stl.subheader("Check-Out Confirmation Receipt")
                           
                           if details:
                                stl.markdown(f"**Confirmation #:** `{confirmation_code(details[0])}`")
                                stl.markdown(f"**Transaction ID:** `{details[0]}`")
                                stl.markdown(f"**Employee ID:** `{details[1]}`")
                                stl.markdown(f"**Asset Tag:** `{details[2]}`")
                                stl.markdown(f"**Check-In Time:** {details[4]}")
                                stl.markdown(f"**Check-Out Time:** {details[5]}")
                    else:
                        stl.warning("Please provide your signature before confirming check-out.")
            
    elif choice == "Dashboard":
        active_df = view_active_transactions()
        completed_df = view_completed_transactions()

        search_query = stl.text_input("Search Active Check-Ins by Employee ID or Asset Tag")
        active_df['employee_id'] = active_df['employee_id'].astype(str)
        active_df['asset_tag'] = active_df['asset_tag'].astype(str)
        
        if not active_df.empty:
            active_df = active_df.copy()
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
            active_df.rename(columns={
                'transaction_id': 'Tx ID',
                'employee_id': 'Employee ID',
                'asset_tag': 'Asset Tag',
                'issue': 'Issue Description',
                'check_in_time': 'Check-In Time'
            }, inplace=True)
            stl.dataframe(active_df, use_container_width=True)

        stl.subheader("Completed Transactions")
        if completed_df.empty:
            stl.info("No completed transactions yet.")
        else:
            completed_df.rename(columns={
                'transaction_id': 'Tx ID',
                'employee_id': 'Employee ID',
                'asset_tag': 'Asset Tag',
                'issue': 'Issue Description',
                'check_in_time': 'Check-In Time',
                'check_out_time': 'Check-Out Time'
            }, inplace=True)

            completed_df["Tx ID"] = completed_df["Tx ID"].astype(str)
            completed_df["Employee ID"] = completed_df["Employee ID"].astype(str)
            completed_df["Asset Tag"] = completed_df["Asset Tag"].astype(str)
            stl.dataframe(completed_df, use_container_width=True)


if __name__ == '__main__':
    system()
