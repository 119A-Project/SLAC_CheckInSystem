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

    if choice == "Check-In":
        stl.subheader("Laptop Check-In")
        employee_id = stl.text_input("Employee ID")
        asset_tag = stl.text_input("Laptop Asset Tag")
        issue = stl.text_input("Issue")
        if stl.button("Check-In"):
            if employee_id and asset_tag:
                check_in(employee_id, asset_tag, issue)
                stl.success(f"Laptop {asset_tag} checked in for Employee {employee_id}")
            else:
                stl.error("Employee ID and Asset Tag are required.")

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

    elif choice == "Dashboard":
        stl.subheader("Active Transactions")
        active_df = view_active_transactions()
        if active_df.empty:
            stl.info("No active check-ins.")
        else:
            stl.dataframe(active_df, use_container_width=True)

        stl.subheader("Completed Transactions")
        completed_df = view_completed_transactions()
        if completed_df.empty:
            stl.info("No completed transactions yet.")
        else:
            stl.dataframe(completed_df, use_container_width=True)


if __name__ == '__main__':
    system()