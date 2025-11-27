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
import altair as alt  

# ---------- Helpers added ----------
def normalize_date_range(date_input, fallback_start, fallback_end):
    """
    Accepts a datetime.date OR a (start, end) tuple (with possible Nones),
    returns (start_ts, end_ts_inclusive) as pandas Timestamps.
    Works for single-date selections, ranges, empty/cleared picks, and reversed order.
    """
    import pandas as _pd

    # No selection → fallback
    if date_input is None:
        s, e = fallback_start, fallback_end
    # Tuple (range) - may be length 1 or 2 depending on Streamlit behavior
    elif isinstance(date_input, tuple):
        dates = [d for d in date_input if d is not None]
        if len(dates) == 2:
            s, e = dates[0], dates[1]
        elif len(dates) == 1:
            s = e = dates[0]
        else:
            s, e = fallback_start, fallback_end
    # Single date
    else:
        s = e = date_input

    # Ensure they look like dates; if not, fall back
    if not hasattr(s, "year"):
        s = fallback_start
    if not hasattr(e, "year"):
        e = fallback_end

    # Order: start <= end
    if s > e:
        s, e = e, s

    start_ts = _pd.to_datetime(s)
    end_ts   = _pd.to_datetime(e) + _pd.Timedelta(days=1) - _pd.Timedelta(seconds=1)  # inclusive end of day
    return start_ts, end_ts

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
    import math
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
# ---------- End helpers ----------


# Keep your existing CSS block "as is"
st.markdown(
    """
    <style>
        /* target the first image inside the sidebar */
        section[data-testid="stSidebar"] img {
            margin-top: -45px;
        }

        /* Big title styling */
        .big-title {
            font-size: 32px;
            font-weight: 800;
            margin-bottom: 0.5rem;
        }

        /* Reusable section box */
        .section-box {
            padding: 0.75rem 1rem;
            border-radius: 0.75rem;
            background-color: rgba(0, 0, 0, 0.03);
            border: 1px solid rgba(0, 0, 0, 0.08);
            margin-bottom: 0.75rem;
        }

        /* Make dashboard tabs a bit bolder */
        div[data-baseweb="tab-list"] button {
            font-weight: 600 !important;
            padding: 0.35rem 0.8rem !important;
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

    # Big custom title instead of st.title
    stl.markdown('<div class="big-title">SLAC Service Desk System</div>', unsafe_allow_html=True)

    menu = ["Check-In", "Check-Out", "Dashboard"]
    choice = stl.sidebar.selectbox("Menu", menu, help="Choose a section of the Service Desk system.")

    # -----------------------------
    # CHECK-IN (merged + fixed)
    # -----------------------------
    if choice == "Check-In":
        stl.subheader("Laptop Check-In")

        stl.markdown(
            """
            <div class="section-box">
                Use this form to <b>create a new service ticket</b> when a laptop is dropped off at the desk.
                Make sure the <b>Employee ID</b>, <b>Asset Tag</b>, issue details, and <b>signature</b> are all filled in.
            </div>
            """,
            unsafe_allow_html=True
        )

        # From your first code: richer issue capture
        employee_id = stl.text_input(
            "Employee ID",
            help="Enter the internal Employee ID number (required)."
        )
        asset_tag = stl.text_input(
            "Laptop Asset Tag",
            help="Enter the laptop's asset tag or inventory number (required)."
        )
        issue_type = stl.selectbox(
            "Issue Type",
            ["Hardware Failure", "Software Request", "Performance Issue", "Account Lockout", "Other"],
            help="Choose the closest category that matches the reported issue."
        )
        issue_details = stl.text_area(
            "Provide more details about the issue",
            help="Include symptoms, error messages, when it started, and anything else that might help."
        )
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
                stl.error("❌ Employee ID, Asset Tag, and Issue Details are required to create a ticket.")
            elif not signature_data:
                stl.error("✍️ Signature is required. Please sign in the box above before submitting.")
            else:
                check_in(employee_id, asset_tag, full_issue_description)

                os.makedirs("signatures", exist_ok=True)
                file_path = f"signatures/signature_{employee_id}_{asset_tag}.png"
                with open(file_path, "wb") as f:
                    f.write(signature_data)

                stl.success(f"✅ Laptop {asset_tag} checked in for Employee {employee_id}.")
                stl.info(f"🖊 Signature saved as {file_path}")

    # -----------------------------
    # CHECK-OUT (added search flow from first code)
    # -----------------------------
    elif choice == "Check-Out":
        stl.subheader("Laptop Check-Out")

        stl.markdown(
            """
            <div class="section-box">
                Use this screen when a <b>device is being returned to the employee</b>.
                Search by Asset Tag, Employee ID, or keywords from the issue, then confirm the correct ticket.
            </div>
            """,
            unsafe_allow_html=True
        )

        active = view_active_transactions()
        if active.empty:
            stl.info("No laptops are currently checked in. You're all caught up! ✅")
        else:
            # Search bar with help
            search = stl.text_input(
                "Search for a device",
                placeholder="Type Asset Tag, Employee ID, Transaction ID, or keywords from Issue...",
                help="Start typing to filter active tickets. Press Enter to update the list."
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
                stl.warning("No matching devices found. Try a different ID, asset tag, or keyword.")
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
                    placeholder="Pick a device to check out...",
                    help="Select the correct transaction, then click 'Confirm Check-Out'."
                )

                if selected:
                    tx_id = filtered.loc[filtered["label"] == selected, "transaction_id"].values[0]
                    if stl.button("Confirm Check-Out"):
                        check_out(int(tx_id))
                        stl.success(f"✅ Transaction {tx_id} checked out successfully.")
            else:
                # If no search yet, keep a simple quick-pick fallback
                active = active.copy()
                active["label"] = active.apply(
                    lambda r: f"Tx#{r['transaction_id']} - {r['asset_tag']} (Employee {r['employee_id']})",
                    axis=1
                )
                selected = stl.selectbox(
                    "Select Transaction to Check-Out",
                    active["label"].tolist(),
                    help="You can also use the search box above for easier filtering."
                )
                tx_id = active.loc[active["label"] == selected, "transaction_id"].values[0]
                if stl.button("Confirm Check-Out"):
                    check_out(int(tx_id))
                    stl.success(f"✅ Transaction {tx_id} checked out successfully.")

    # -----------------------------
    # DASHBOARD (robust + visuals + single/range date + pagination + tabs)
    # -----------------------------
    elif choice == "Dashboard":
        stl.subheader("Service Desk Dashboard")

        stl.markdown(
            """
            <div class="section-box">
                This dashboard lets you <b>monitor workload, trends, and performance</b> across the service desk.
                Use the filters below to narrow by <b>date range</b> and <b>issue types</b>. All tabs update together.
            </div>
            """,
            unsafe_allow_html=True
        )

        active_df = view_active_transactions()
        completed_df = view_completed_transactions()

        # --- Ensure expected columns exist even if queries return 0 rows
        def ensure_cols(df, cols):
            for c in cols:
                if c not in df.columns:
                    df[c] = pd.Series(dtype="object")
            return df

        active_df   = ensure_cols(active_df,   ["transaction_id", "employee_id", "asset_tag", "issue", "check_in_time"])
        completed_df= ensure_cols(completed_df,["transaction_id", "employee_id", "asset_tag", "issue", "check_in_time", "check_out_time"])

        # --- Safe datetime conversion (only if column exists)
        if "check_in_time" in active_df.columns:
            active_df["check_in_time"] = pd.to_datetime(active_df["check_in_time"], errors="coerce")
        if "check_in_time" in completed_df.columns:
            completed_df["check_in_time"] = pd.to_datetime(completed_df["check_in_time"], errors="coerce")
        if "check_out_time" in completed_df.columns:
            completed_df["check_out_time"] = pd.to_datetime(completed_df["check_out_time"], errors="coerce")

        # --- Derive issue_type from "Type: details"
        def issue_type_from(issue: str) -> str:
            if isinstance(issue, str) and ":" in issue:
                return issue.split(":", 1)[0].strip()
            return issue if isinstance(issue, str) and issue else "Unknown"

        if not active_df.empty:
            active_df = active_df.copy()
            active_df["issue_type"] = active_df["issue"].apply(issue_type_from)
        if not completed_df.empty:
            completed_df = completed_df.copy()
            completed_df["issue_type"] = completed_df["issue"].apply(issue_type_from)
            # Compute turnaround duration in hours when both timestamps are present
            if "check_in_time" in completed_df.columns and "check_out_time" in completed_df.columns:
                completed_df["duration_hours"] = (
                    (completed_df["check_out_time"] - completed_df["check_in_time"]).dt.total_seconds() / 3600.0
                )

        # --- Filters (inside a UI box)
        stl.markdown("### Dashboard Filters")
        stl.markdown(
            """
            <div class="section-box">
                <b>Tip:</b> Pick a single day or a range, and narrow down by issue type to focus on specific patterns.
            </div>
            """,
            unsafe_allow_html=True
        )

        colf1, colf2 = stl.columns(2)

        # Date range over available check_in_time across both tables
        def minmax_date():
            dates = []
            if "check_in_time" in active_df.columns and not active_df.empty:
                dates.append(active_df["check_in_time"].min())
                dates.append(active_df["check_in_time"].max())
            if "check_in_time" in completed_df.columns and not completed_df.empty:
                dates.append(completed_df["check_in_time"].min())
                dates.append(completed_df["check_in_time"].max())
            dates = [d for d in dates if pd.notna(d)]
            if not dates:
                today = pd.Timestamp.today().date()
                return today, today
            return pd.to_datetime(min(dates)).date(), pd.to_datetime(max(dates)).date()

        all_min, all_max = minmax_date()
        with colf1:
            date_pick = stl.date_input(
                "Date range (by Check-In Date)",
                value=(all_min, all_max),
                min_value=all_min,
                max_value=all_max,
                help="Pick a single day or a start–end range. This filters tickets by their check-in date."
            )

        # Normalize single date OR range to inclusive timestamps
        start_date, end_date = normalize_date_range(date_pick, all_min, all_max)

        # Issue type multiselect
        with colf2:
            all_types = []
            if "issue_type" in active_df.columns and not active_df.empty:
                all_types.extend(active_df["issue_type"].dropna().unique().tolist())
            if "issue_type" in completed_df.columns and not completed_df.empty:
                all_types.extend(completed_df["issue_type"].dropna().unique().tolist())
            all_types = sorted(pd.unique(pd.Series(all_types)).tolist()) if all_types else []
            selected_types = stl.multiselect(
                "Issue types",
                options=all_types,
                default=all_types,
                help="Limit the dashboard to specific issue categories (for example, only Hardware Failures)."
            )

        # Apply filters
        def in_range(series):
            if series is None:
                return pd.Series([], dtype=bool)
            return (series >= start_date) & (series <= end_date)

        if not active_df.empty:
            mask_a = in_range(active_df.get("check_in_time"))
            if selected_types:
                mask_a &= active_df.get("issue_type").isin(selected_types)
            active_f = active_df.loc[mask_a].copy()
        else:
            active_f = active_df

        if not completed_df.empty:
            mask_c = in_range(completed_df.get("check_in_time"))
            if selected_types:
                mask_c &= completed_df.get("issue_type").isin(selected_types)
            completed_f = completed_df.loc[mask_c].copy()
        else:
            completed_f = completed_df

        if active_f.empty and completed_f.empty:
            stl.warning("No matching records for the current filters. Try expanding the date range or issue types.")

        # ----- Dashboard Tabs -----
        stl.markdown("---")
        tab_overview, tab_activity, tab_issues, tab_logs = stl.tabs(
            ["📊 Overview", "📈 Daily Activity", "🧩 Issue Types", "📋 Logs"]
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
                - **Active Items**: Open tickets that are still checked in.
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

                paginated_table(
                    show,
                    key="dash_completed_recent",
                    rename_cols=rename_map,
                    default_page_size=25,
                    height=420
                )

                csv = show.rename(columns=rename_map).to_csv(index=False).encode("utf-8")
                stl.download_button(
                    "Download Completed Logs (CSV)",
                    data=csv,
                    file_name="completed_logs.csv",
                    mime="text/csv",
                    help="Export the currently filtered completed tickets as a CSV file."
                )
            else:
                stl.info("No completed transactions in the selected range.")

            stl.markdown("---")
            stl.subheader("Raw Tables (All Data Within Filters)")
            with stl.expander("Raw Active Transactions"):
                if active_df.empty:
                    stl.info("No active check-ins.")
                else:
                    paginated_table(
                        active_df.rename(columns={
                            'transaction_id': 'Tx ID',
                            'employee_id': 'Employee ID',
                            'asset_tag': 'Asset Tag',
                            'issue': 'Issue Description',
                            'check_in_time': 'Check-In Time'
                        }),
                        key="raw_active",
                        default_page_size=25,
                        height=360
                    )

            with stl.expander("Raw Completed Transactions"):
                if completed_df.empty:
                    stl.info("No completed transactions yet.")
                else:
                    paginated_table(
                        completed_df.rename(columns={
                            'transaction_id': 'Tx ID',
                            'employee_id': 'Employee ID',
                            'asset_tag': 'Asset Tag',
                            'issue': 'Issue Description',
                            'check_in_time': 'Check-In Time',
                            'check_out_time': 'Check-Out Time'
                        }),
                        key="raw_completed",
                        default_page_size=25,
                        height=360
                    )

if __name__ == '__main__':
    system()
