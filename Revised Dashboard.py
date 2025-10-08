#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Sep 27 15:55:27 2025

@author: sachinkalahasti
"""
import sqlite3
import streamlit as stl
import pandas as pd
from datetime import datetime

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
                   
                        CREATE TABLE IF NOT EXISTS transactions (
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
#1. Added employee name 
def view_active_transactions():
    connect = database_connection()
    df = pd.read_sql("""
                    SELECT 
                     t.transaction_id, 
                     t.employee_id, 
                     e.name AS employee_name,
                     t.asset_tag, 
                     t.issue, 
                     t.check_in_time
                    FROM Transactions t
                    JOIN Employees e ON t.employee_id = e.employee_id
                    WHERE t.status = 'Checked-In'
                    ORDER BY t.check_in_time DESC
                    """, connect)
    connect.close()
    return df

def view_completed_transactions():
    connect = database_connection()
    df = pd.read_sql("""
                    SELECT 
                     t.transaction_id, 
                     t.employee_id, 
                     e.name AS employee_name,
                     t.asset_tag, 
                     t.issue, 
                     t.check_in_time, 
                     t.check_out_time
                    FROM Transactions t
                    JOIN Employees e ON t.employee_id = e.employee_id
                    WHERE status='Checked-Out'
                    ORDER BY check_out_time DESC
                    """, connect)
    connect.close()
    return df

# 4. New functions added to prevent check-ins with invalid Employee IDs or Asset Tags
def validate_employee(employee_id):
    """Checks if an employee ID exists in the Employees table."""
    connect = database_connection()
    cursor = connect.cursor()
    # The [0] is needed to get the first element of the single-row result
    cursor.execute("SELECT COUNT(1) FROM Employees WHERE employee_id = ?", (employee_id,))
    exists = cursor.fetchone()[0]
    connect.close()
    return exists > 0

def validate_asset(asset_tag):
    """Checks if an asset tag exists in the Laptops table."""
    connect = database_connection()
    cursor = connect.cursor()
    cursor.execute("SELECT COUNT(1) FROM Laptops WHERE asset_tag = ?", (asset_tag,))
    exists = cursor.fetchone()[0]
    connect.close()
    return exists > 0


def system():
    tables()
    stl.title("SLAC Service Desk System")

    menu = ["Check-In", "Check-Out", "Dashboard"]
    choice = stl.sidebar.selectbox("Menu", menu)

    if choice == "Check-In":
        stl.subheader("Laptop Check-In")
        employee_id = stl.text_input("Employee ID")
        asset_tag = stl.text_input("Laptop Asset Tag")
#2. new code starts for Better Issue Descriptions
        issue_type = stl.selectbox (
            "Issue Type",
            ["Hardware Failure", "Software Request", "Performance Issue", "Account Lockout", "Etc"]
        )
        issue_details = stl.text_area("Provide more details about the issue")
        full_issue_description = f"{issue_type}: {issue_details}"
        
        if stl.button("Check-In"):
            # Check that all fields are filled first
            if not employee_id or not asset_tag or not issue_details:
                stl.error("Employee ID, Asset Tag, and Issue Details are required.")
        # VALIDATION LOGIC STARTS HERE
            elif not validate_employee(employee_id):
                stl.error(f"Validation Failed: Employee ID '{employee_id}' not found in the database.")
            elif not validate_asset(asset_tag):
                stl.error(f"Validation Failed: Asset Tag '{asset_tag}' not found in the database.")
        # VALIDATION LOGIC ENDS HERE
            else:
                # If all checks pass, proceed with the check-in
                check_in(employee_id, asset_tag, full_issue_description)
                stl.success(f"Laptop {asset_tag} checked in for Employee {employee_id}")  
              
    elif choice == "Check-Out":
        stl.subheader("Laptop Check-Out")
        active = view_active_transactions()
        if active.empty:
            stl.info("No laptops currently checked in.")
        else:
            active["label"] = active.apply(
                lambda r: f"Tx#{r['transaction_id']} - {r['asset_tag']} (Employee {r['employee_id']})",
                axis=1
            )
            selected = stl.selectbox("Select Transaction to Check-Out", active["label"].tolist())
            tx_id = active.loc[active["label"] == selected, "transaction_id"].values[0]
            if stl.button("Confirm Check-Out"):
                check_out(int(tx_id))
                stl.success(f"Transaction {tx_id} checked out successfully.")
#3. Updated Dashboard (employee name & search bar)
    elif choice == "Dashboard":
        stl.subheader("Service Desk Dashboard")
        
        # --- SEARCH BAR CODE STARTS HERE ---
        search_query = stl.text_input("Search Active Check-Ins by Employee Name or Asset Tag")
        # --- SEARCH BAR CODE ENDS HERE ---

        active_df = view_active_transactions()
        # Filter the dataframe based on the search query before displaying anything
        if search_query:
            active_df = active_df[
                active_df['employee_name'].str.contains(search_query, case=False, na=False) |
                active_df['asset_tag'].str.contains(search_query, case=False, na=False)
            ]

        stl.metric("Active Items at Service Desk", len(active_df))
        stl.markdown("---")

        stl.subheader("Active Check-Ins")
        if active_df.empty:
            stl.info("No active check-ins.")
        else:
            #Added 'employee_id' to the rename dictionary
            active_df.rename(columns={
                'transaction_id': 'Tx ID',
                'employee_id': 'Employee ID',
                'employee_name': 'Employee Name',
                'asset_tag': 'Asset Tag',
                'issue': 'Issue Description',
                'check_in_time': 'Check-In Time'
            }, inplace=True)
            stl.dataframe(active_df, use_container_width=True)

        stl.subheader("Completed Transactions")
        completed_df = view_completed_transactions()
        if completed_df.empty:
            stl.info("No completed transactions yet.")
        else:
            completed_df.rename(columns={
                'transaction_id': 'Tx ID',
                'employee_id': 'Employee ID',
                'employee_name': 'Employee Name',
                'asset_tag': 'Asset Tag',
                'issue': 'Issue Description',
                'check_in_time': 'Check-In Time',
                'check_out_time': 'Check-Out Time'
            }, inplace=True)
            stl.dataframe(completed_df, use_container_width=True)


if __name__ == '__main__':
    system()
