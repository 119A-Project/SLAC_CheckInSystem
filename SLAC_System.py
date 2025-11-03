#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import streamlit as stl
import pandas as pd
from datetime import datetime
import streamlit as st
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import io
import os

# Keep your existing CSS block "as is"
st.markdown(
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

# --- Sidebar Logo (kept "as is") ---
with st.sidebar:
    st.image("logo.png", width=200)  # adjust width as needed
    st.markdown("---")  # optional separator line. 

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
                   
                        CREATE TABLE IF NOT EXISTS Laptops(
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
    
def check_in(emp_id, asset_tag, issue):
    connect = database_connection()
    connect.execute("""
                    INSERT INTO Transactions (employee_id, asset_tag, issue)
                    VALUES (?, ?, ?)
                    """, (emp_id, asset_tag, issue))
    connect.commit()
    connect.close()

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

def system():
    tables()
    stl.title("SLAC Service Desk System")

    menu = ["Check-In", "Check-Out", "Dashboard"]
    choice = stl.sidebar.selectbox("Menu", menu)

    # -----------------------------
    # CHECK-IN (merged + fixed)
    # -----------------------------
    if choice == "Check-In":
        stl.subheader("Laptop Check-In")

        # From your first code: richer issue capture
        employee_id = stl.text_input("Employee ID")
        asset_tag = stl.text_input("Laptop Asset Tag")
        issue_type = stl.selectbox(
            "Issue Type",
            ["Hardware Failure", "Software Request", "Performance Issue", "Account Lockout", "Other"]
        )
        issue_details = stl.text_area("Provide more details about the issue")
        full_issue_description = f"{issue_type}: {issue_details}"

        # Signature Canvas — always render (not under a button)
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

        # Convert signature to bytes only if user actually drew something
        signature_data = None
        if canvas_result is not None and canvas_result.image_data is not None:
            arr = canvas_result.image_data
            # Normalize dtype/scale
            if arr.max() <= 1.0:
                arr = (arr * 255.0)
            rgb = arr[:, :, :3].astype("uint8")

            # Any non-white pixel => ink present
            if (rgb != 255).any():
                img = Image.fromarray(rgb)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                signature_data = buf.getvalue()

        # Single submit button for everything (avoid duplicate "Check-In" buttons)
        if stl.button("Submit Check-In"):
            if not (employee_id and asset_tag and issue_details):
                stl.error("Employee ID, Asset Tag, and Issue Details are required.")
            elif not signature_data:
                stl.error("Signature is required. Please sign in the box above.")
            else:
                check_in(employee_id, asset_tag, full_issue_description)

                os.makedirs("signatures", exist_ok=True)
                file_path = f"signatures/signature_{employee_id}_{asset_tag}.png"
                with open(file_path, "wb") as f:
                    f.write(signature_data)

                stl.success(f"Laptop {asset_tag} checked in for Employee {employee_id}")
                stl.info(f"Signature saved as {file_path}")

    # -----------------------------
    # CHECK-OUT (added search flow from first code)
    # -----------------------------
    elif choice == "Check-Out":
        stl.subheader("Laptop Check-Out")
        active = view_active_transactions()
        if active.empty:
            stl.info("No laptops currently checked in.")
        else:
            # Search bar like in your first code
            search = stl.text_input(
                "Search for a device (by Asset Tag, Employee ID, or Issue)",
                placeholder="Type here and press Enter..."
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

            if search and filtered.empty:
                stl.warning("No matching devices found.")
            elif search:
                stl.write("### Matching Devices")
                stl.dataframe(filtered, use_container_width=True)

                filtered = filtered.copy()
                filtered["label"] = filtered.apply(
                    lambda r: f"Tx#{r['transaction_id']} - {r['asset_tag']} (Employee {r['employee_id']})",
                    axis=1
                )

                selected = stl.selectbox(
                    "Select the device to Check-Out",
                    filtered["label"].tolist(),
                    index=None,
                    placeholder="Select or type to search for a device..."
                )

                if selected:
                    tx_id = filtered.loc[filtered["label"] == selected, "transaction_id"].values[0]
                    if stl.button("Confirm Check-Out"):
                        check_out(int(tx_id))
                        stl.success(f"Transaction {tx_id} checked out successfully.")
            else:
                # If no search yet, keep a simple quick-pick fallback
                active = active.copy()
                active["label"] = active.apply(
                    lambda r: f"Tx#{r['transaction_id']} - {r['asset_tag']} (Employee {r['employee_id']})",
                    axis=1
                )
                selected = stl.selectbox("Select Transaction to Check-Out", active["label"].tolist())
                tx_id = active.loc[active["label"] == selected, "transaction_id"].values[0]
                if stl.button("Confirm Check-Out"):
                    check_out(int(tx_id))
                    stl.success(f"Transaction {tx_id} checked out successfully.")

    # -----------------------------
    # DASHBOARD (added search & friendly headers from first code)
    # -----------------------------
    elif choice == "Dashboard":
        active_df = view_active_transactions()
        completed_df = view_completed_transactions()

        # Search Active Check-Ins by Employee ID or Asset Tag (from your first code)
        search_query = stl.text_input("Search Active Check-Ins by Employee ID or Asset Tag")

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
            stl.info("No active check-ins match your search." if search_query else "No active check-ins.")
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

if __name__ == '__main__':
    system()

