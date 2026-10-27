import sqlite3
from pathlib import Path
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from io import StringIO
import html
import math
import hashlib
import secrets
import sys
import shutil
from getpass import getpass

import numpy as np
import pandas as pd
import streamlit as st


def _reset_admin_from_cli(db_path):
    """Safely reset an existing Admin password from the local command line."""
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        print("Keep this file in the same folder as hcm_lite.db.")
        return 1
    try:
        with sqlite3.connect(db_path, timeout=20) as conn:
            conn.row_factory = sqlite3.Row
            admins = conn.execute(
                "SELECT user_code, display_name, active FROM user_accounts WHERE role='Admin' ORDER BY user_code"
            ).fetchall()
            if not admins:
                print("No Admin account found. No changes made; do not delete the database.")
                return 1
            print("Existing Admin accounts:")
            for row in admins:
                print(f" - {row['user_code']} | {row['display_name']} | {'active' if row['active'] else 'inactive'}")
            code = input("Enter the Admin user code to reset: ").strip().upper()
            selected = next((row for row in admins if row['user_code'].upper() == code), None)
            if selected is None:
                print("That user code is not an Admin account. No changes made.")
                return 1
            password = getpass("Enter new password (minimum 10 characters): ")
            confirm = getpass("Confirm new password: ")
            if len(password) < 10:
                print("Password must be at least 10 characters. No changes made.")
                return 1
            if password != confirm:
                print("Passwords do not match. No changes made.")
                return 1
            salt = secrets.token_hex(16)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240000).hex()
            conn.execute(
                "UPDATE user_accounts SET password_hash=?, password_salt=?, active=1 WHERE user_code=? AND role='Admin'",
                (digest, salt, selected['user_code']),
            )
            conn.commit()
            print("Admin password reset successfully. Employee/payroll data was not changed.")
            print(f"Sign in with user code: {selected['user_code']} and the new password.")
            return 0
    except sqlite3.Error as exc:
        print(f"Database error: {exc}")
        print("Close Streamlit first and ensure hcm_lite.db is backed up.")
        return 1


def _reset_company_data_from_cli(db_path):
    """Back up the current database, then clear all company records but keep the schema."""
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        print("No changes made. Start the app normally to create a new database.")
        return 1
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"hcm_lite_backup_before_reset_{stamp}.db")
    try:
        shutil.copy2(db_path, backup_path)
        print("A backup has been created:")
        print(backup_path)
        print("WARNING: this clears company settings, Admin/HR/Employee accounts, employees,")
        print("attendance, leave requests, payroll, holidays, announcements and audit logs.")
        print("The database structure and app code will remain in place.")
        confirmation = input("To permanently reset all company data, type RESET MY HCM: ").strip()
        if confirmation != "RESET MY HCM":
            print("Cancelled. Database was not changed. Backup remains available.")
            return 1
        with sqlite3.connect(db_path, timeout=20) as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()]
            for table in tables:
                # Table names come only from this database's sqlite_master metadata.
                safe_table = '"' + table.replace('"', '""') + '"'
                conn.execute(f"DELETE FROM {safe_table}")
            conn.execute("DELETE FROM sqlite_sequence")
            conn.commit()
        print("Reset complete. All company data was cleared.")
        print("Next, start the app normally; Register Your Company will appear.")
        print("Keep the backup file until you confirm the new setup is correct.")
        return 0
    except Exception as exc:
        print(f"Reset failed: {exc}")
        print(f"Your backup should still be here: {backup_path}")
        return 1


# Local-only recovery/reset commands. These run before Streamlit starts.
if __name__ == "__main__" and "--reset-admin" in sys.argv:
    raise SystemExit(_reset_admin_from_cli(Path(__file__).resolve().parent / "hcm_lite.db"))
if __name__ == "__main__" and "--reset-company-data" in sys.argv:
    raise SystemExit(_reset_company_data_from_cli(Path(__file__).resolve().parent / "hcm_lite.db"))

# ============================================================
# HCM LITE — STREAMLIT + PANDAS + NUMPY + SQLITE
# NO PLOTLY. Charts use Streamlit's built-in chart components.
# Replace the entire content of app.py with this file.
# ============================================================

st.set_page_config(
    page_title="HCM Lite | People Dashboard",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = Path(__file__).resolve().parent / "hcm_lite.db"
PKT = ZoneInfo("Asia/Karachi")
def pkt_now():
    return datetime.now(PKT)

TODAY = pkt_now().date().isoformat()


# ============================= DATABASE =============================
def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_code TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                department TEXT NOT NULL,
                designation TEXT NOT NULL DEFAULT '',
                joining_date TEXT NOT NULL,
                basic_salary REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'Active',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                work_date TEXT NOT NULL,
                check_in TEXT,
                check_out TEXT,
                status TEXT NOT NULL DEFAULT 'Present',
                work_hours REAL NOT NULL DEFAULT 0,
                UNIQUE(employee_id, work_date),
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            );

            CREATE TABLE IF NOT EXISTS leave_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                leave_type TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                days REAL NOT NULL DEFAULT 1,
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT NOT NULL,
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            );

            CREATE TABLE IF NOT EXISTS holidays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                holiday_date TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_code TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Admin','HR','Employee')),
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                employee_code TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payroll_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                pay_year INTEGER NOT NULL,
                pay_month INTEGER NOT NULL,
                basic_salary REAL NOT NULL DEFAULT 0,
                allowances REAL NOT NULL DEFAULT 0,
                bonus REAL NOT NULL DEFAULT 0,
                deductions REAL NOT NULL DEFAULT 0,
                net_salary REAL NOT NULL DEFAULT 0,
                payment_status TEXT NOT NULL DEFAULT 'Draft',
                created_at TEXT NOT NULL,
                paid_at TEXT,
                UNIQUE(employee_id, pay_year, pay_month),
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            );

            INSERT OR IGNORE INTO settings(key, value)
            VALUES ('company_name', 'HCM Lite Workspace');
            """
        )


def query_df(sql, params=()):
    with connect_db() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def execute(sql, params=()):
    with connect_db() as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid


def log_action(action, details=""):
    execute(
        "INSERT INTO audit_logs(action, details, created_at) VALUES (?, ?, ?)",
        (action, details, pkt_now().isoformat(timespec="seconds")),
    )


def get_company_name():
    df = query_df("SELECT value FROM settings WHERE key='company_name'")
    return str(df.iloc[0]["value"]) if not df.empty else "HCM Lite Workspace"


def safe_count(df):
    return int(len(df))


init_db()


def seed_demo_data():
    """Insert clearly labelled synthetic demo data once, without duplicates."""
    with connect_db() as conn:
        seeded = conn.execute(
            "SELECT value FROM settings WHERE key='demo_data_seeded'"
        ).fetchone()
        if seeded and seeded[0] == 'yes':
            return

        departments = [
            "Human Resources", "Engineering", "Finance",
            "Marketing", "Operations", "Sales"
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO departments(name) VALUES(?)",
            [(name,) for name in departments],
        )

        first_names = [
            "Ayesha", "Hamza", "Fatima", "Ali", "Zara", "Ahmed",
            "Hira", "Usman", "Mariam", "Bilal", "Sana", "Daniyal",
            "Iqra", "Hassan", "Noor", "Saad", "Laiba", "Omar",
            "Mehak", "Taha", "Anaya", "Zain", "Eman", "Rayan",
            "Amna", "Fahad", "Mahnoor", "Adnan", "Areeba", "Sameer"
        ]
        last_names = [
            "Khan", "Ahmed", "Malik", "Sheikh", "Hussain", "Raza",
            "Iqbal", "Siddiqui", "Abbas", "Farooq"
        ]
        roles = [
            "HR Executive", "Software Engineer", "Accountant",
            "Marketing Specialist", "Operations Officer", "Sales Executive"
        ]
        base_date = pkt_now().date()
        for i, first in enumerate(first_names):
            full_name = f"{first} {last_names[i % len(last_names)]}"
            dept = departments[i % len(departments)]
            email = f"demo.employee{i+1:02d}@example.com"
            code = f"DEMO-{i+1:03d}"
            joined = (base_date - pd.Timedelta(days=40 + i * 13)).isoformat()
            salary = float(55000 + (i % 10) * 8500)
            conn.execute(
                """INSERT OR IGNORE INTO employees
                (employee_code, full_name, email, department, designation,
                 joining_date, basic_salary, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Active', ?)""",
                (code, full_name, email, dept, roles[i % len(roles)],
                 joined, salary, pkt_now().isoformat(timespec='seconds')),
            )

        employee_rows = conn.execute(
            "SELECT id, employee_code FROM employees WHERE employee_code LIKE 'DEMO-%' ORDER BY employee_code"
        ).fetchall()
        # Populate weekdays over the last 30 days; deterministic sample data.
        for employee_id, employee_code in employee_rows:
            offset = int(employee_code.split('-')[1])
            for day_offset in range(29, -1, -1):
                work_day = base_date - pd.Timedelta(days=day_offset)
                work_day = work_day.date() if hasattr(work_day, 'date') else work_day
                if work_day.weekday() >= 5:
                    continue
                status = ["Present", "Present", "Present", "Late", "Absent", "Half Day"][
                    (offset + day_offset) % 6
                ]
                check_in = "09:00" if status == "Present" else ("09:25" if status == "Late" else None)
                check_out = "17:30" if status in ("Present", "Late") else None
                hours = 8.5 if status == "Present" else (8.0 if status == "Late" else (4.0 if status == "Half Day" else 0.0))
                conn.execute(
                    """INSERT OR IGNORE INTO attendance
                    (employee_id, work_date, check_in, check_out, status, work_hours)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (employee_id, work_day.isoformat(), check_in, check_out, status, hours),
                )

        leave_examples = [
            (0, "Annual", 2, "Family commitment", "Pending"),
            (1, "Sick", 0, "Medical rest", "Pending"),
            (2, "Casual", 5, "Personal work", "Approved"),
            (3, "Annual", 8, "Planned vacation", "Pending"),
            (4, "Sick", 1, "Recovery", "Approved"),
            (5, "Casual", 3, "Personal appointment", "Rejected"),
        ]
        for index, leave_type, start_offset, reason, status in leave_examples:
            if index >= len(employee_rows):
                continue
            employee_id = employee_rows[index][0]
            start = base_date + pd.Timedelta(days=start_offset)
            start = start.date() if hasattr(start, 'date') else start
            end = start + pd.Timedelta(days=1)
            end = end.date() if hasattr(end, 'date') else end
            conn.execute(
                """INSERT INTO leave_requests
                (employee_id, leave_type, start_date, end_date, days, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (employee_id, leave_type, start.isoformat(), end.isoformat(), 2, reason, status,
                 pkt_now().isoformat(timespec='seconds')),
            )

        conn.execute(
            "INSERT OR IGNORE INTO holidays(holiday_date, title) VALUES(?, ?)",
            ((base_date + pd.Timedelta(days=14)).isoformat(), "Demo Company Day"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO announcements(title, message, created_at) VALUES(?, ?, ?)",
            ("Welcome to HCM Lite", "This is synthetic demo content. Replace it with your organisation's real records.", pkt_now().isoformat(timespec='seconds')),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('demo_data_seeded', 'yes')"
        )
        conn.commit()


# Step 5 intentionally does not seed synthetic employees, attendance, leave, or announcements.
# Existing rows in an existing database are preserved; use the Admin cleanup action if needed.


# ================================ CSS ================================
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background: #555860; color: #f5f5f7; }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stToolbar"] { display: none; }
    #MainMenu, footer { visibility: hidden; }
    [data-testid="stAppViewBlockContainer"] {
        max-width: 1800px; margin: 18px auto; padding: 24px !important;
        background: #101010; border: 1px solid #292929; border-radius: 21px;
        min-height: calc(100vh - 36px);
    }
    [data-testid="stSidebar"] { background: #1e1e1f; border-right: 1px solid #343436; }
    [data-testid="stSidebar"] > div:first-child { padding: 1.4rem 1rem; }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
        color: #85858a; font-size: .75rem; letter-spacing: 1px;
    }
    [data-testid="stSidebar"] div[role="radiogroup"] { gap: .35rem; }
    [data-testid="stSidebar"] div[role="radiogroup"] label {
        background: transparent; border: 1px solid transparent; border-radius: 10px;
        padding: .55rem .7rem; color: #eeeeef;
    }
    [data-testid="stSidebar"] div[role="radiogroup"] label:hover {
        background: #303033; border-color: #424247;
    }
    [data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
        background: #e1ff6a; color: #111; font-weight: 700;
    }
    [data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) * { color: #111 !important; }
    [data-testid="stSidebar"] div[role="radiogroup"] label > div:first-child { display: none; }
    /* High-contrast calendar/date picker for the dark interface */
    [data-baseweb="calendar"], [data-baseweb="popover"] [role="dialog"] {
        background: #ffffff !important; color: #171717 !important;
    }
    [data-baseweb="calendar"] *, [data-baseweb="popover"] [role="dialog"] * {
        color: #171717 !important;
    }
    [data-baseweb="calendar"] button { background: #ffffff !important; border-radius: 7px !important; }
    [data-baseweb="calendar"] button:hover { background: #e8f4bf !important; }
    [data-baseweb="calendar"] button[aria-selected="true"] { background: #d8f56a !important; color: #111111 !important; font-weight: 800 !important; }
    [data-testid="stDateInput"] input { color: #f7f7f7 !important; background: #202023 !important; }
    .brand { font-size: 29px; font-weight: 800; letter-spacing: -1.2px; color: #f6f6f6; }
    .brand span { color: #e1ff6a; }
    .eyebrow { color: #8e8e95; font-size: 10px; letter-spacing: 1.5px; margin-top: 2px; }
    .search-look { background: #202021; border: 1px solid #2b2b2d; border-radius: 12px;
        padding: 13px 16px; color: #9c9ca2; font-size: 12px; max-width: 400px; width: 100%; }
    .user-look { color: #c6c6cb; text-align: right; font-size: 12px; white-space: nowrap; }
    .welcome { display: flex; align-items: center; justify-content: space-between;
        gap: 18px; flex-wrap: wrap; margin: 7px 0 25px; }
    .main-title { color: #f5f5f7; font-size: clamp(24px, 2.7vw, 34px); font-weight: 800;
        letter-spacing: -1.2px; line-height: 1.25; }
    .main-title span { color: #e1ff6a; }
    .subtitle { color: #a5a5ac; font-size: 12px; margin-top: 8px; }
    .date-line { color: #7f7f87; font-size: 11px; margin-top: 7px; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .action { border: 1px solid #343438; background: #202021; border-radius: 10px;
        padding: 11px 13px; color: #f3f3f5; font-size: 11px; font-weight: 600; }
    .action-purple { border: 1px solid #784df1; background: #6938ec; border-radius: 10px;
        padding: 11px 13px; color: white; font-size: 11px; font-weight: 600; }
    .section-label { color: #f4f4f6; font-size: 17px; font-weight: 700; margin: 20px 0 12px; }
    .metric-card { border: 1px solid #29292b; border-radius: 16px; padding: 18px;
        background: #202021; min-height: 145px; }
    .metric-purple { background: linear-gradient(140deg, #7540f0, #6333df); border-color: #7849ef; }
    .metric-lime { background: #e1ff6a; border-color: #e1ff6a; }
    .metric-label { font-size: 12px; font-weight: 600; color: #c7c7cd; }
    .metric-lime .metric-label { color: #30351e; }
    .metric-value { color: #f8f8fa; font-size: 30px; font-weight: 800; line-height: 1.1; margin-top: 17px; }
    .metric-lime .metric-value { color: #111; }
    .metric-note { color: #a8a8b0; font-size: 10px; margin-top: 9px; }
    .metric-purple .metric-note { color: #eee6ff; }
    .metric-lime .metric-note { color: #353b20; }
    .panel-title { color: #f4f4f6; font-size: 14px; font-weight: 700; }
    .lime { color: #e1ff6a; }
    .muted { color: #888890; font-size: 11px; line-height: 1.7; }
    .empty { border: 1px solid #303034; border-radius: 14px; background: #181819;
        padding: 30px 18px; text-align: center; }
    .empty-icon { color: #e1ff6a; font-size: 25px; margin-bottom: 8px; }
    .empty-title { color: #f4f4f6; font-size: 13px; font-weight: 700; }
    .empty-text { color: #909098; font-size: 11px; margin: 7px auto 0; max-width: 350px; line-height: 1.7; }
    .footer-line { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 10px;
        border-top: 1px solid #2a2a2c; margin-top: 22px; padding-top: 15px;
        color: #77777e; font-size: 9px; }
    .footer-line b { color: #e1ff6a; letter-spacing: 1px; }
    div[data-testid="stDataFrame"] { border: 1px solid #333338; border-radius: 12px; overflow: hidden; }
    div[data-testid="stForm"] { border: 1px solid #333338; border-radius: 14px; background: #181819; padding: 18px; }
    .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
        border-radius: 9px; font-weight: 600;
    }
    /* High-contrast widget styling: prevent labels/inputs from disappearing */
    .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background: #171719 !important; color: #f5f5f7 !important; }
    [data-testid="stAppViewBlockContainer"] { background: #171719 !important; color: #f5f5f7 !important; }
    [data-testid="stMarkdownContainer"], [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] li, [data-testid="stText"], label,
    .stCaption, [data-testid="stCaptionContainer"] { color: #ededf0; }
    [data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label { color: #f0f0f3 !important; }
    input, textarea, [data-baseweb="input"] input, [data-baseweb="textarea"] textarea {
        color: #f7f7fa !important; background: #242428 !important; caret-color: #e1ff6a !important;
        -webkit-text-fill-color: #f7f7fa !important;
    }
    [data-baseweb="select"] > div, [data-baseweb="popover"] > div,
    [data-baseweb="datepicker"] { background: #242428 !important; color: #f7f7fa !important; border-color: #49494f !important; }
    [data-baseweb="select"] span, [data-baseweb="select"] input { color: #f7f7fa !important; -webkit-text-fill-color: #f7f7fa !important; }
    [role="listbox"], [role="option"], [data-baseweb="menu"] { background: #242428 !important; color: #f7f7fa !important; }
    [role="option"]:hover { background: #38383f !important; }
    [data-testid="stDataFrame"], [data-testid="stTable"] { background: #202023 !important; color: #f7f7fa !important; }
    [data-testid="stMetric"] { background: #222226; border: 1px solid #38383e; padding: 12px 14px; border-radius: 12px; }
    [data-testid="stMetricLabel"], [data-testid="stMetricValue"], [data-testid="stMetricDelta"] { color: #f7f7fa !important; }
    .stAlert, [data-testid="stAlert"] { color: #f7f7fa !important; border-radius: 10px; }
    .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
        background: #29292f; color: #f7f7fa; border: 1px solid #4a4a52; border-radius: 9px; font-weight: 650;
    }
    .stButton > button:hover, .stDownloadButton > button:hover { border-color: #e1ff6a; color: #e1ff6a; }
    .stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] { background: #7140ed; border-color: #7140ed; color: white; }
    [data-testid="stSidebar"] { background: #1e1e1f !important; }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] .stCaption { color: #eeeeef !important; }
    [data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) { background: #e1ff6a !important; }
    [data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) * { color: #111 !important; }
    div[data-testid="stForm"] { border: 1px solid #3a3a40; background: #202023; }
    hr { border-color: #39393f !important; }

    /* FINAL ACCESSIBILITY / CONTRAST PASS
       Keep all Streamlit widgets readable regardless of their default theme. */
    html, body, .stApp, [data-testid="stAppViewContainer"],
    [data-testid="stMain"], [data-testid="stMainBlockContainer"],
    [data-testid="stAppViewBlockContainer"] {
        color: #f5f5f7 !important;
    }
    [data-testid="stMarkdownContainer"],
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] span,
    [data-testid="stMarkdownContainer"] li,
    [data-testid="stMarkdownContainer"] strong,
    [data-testid="stMarkdownContainer"] em,
    [data-testid="stText"],
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p,
    [data-testid="stWidgetLabel"],
    [data-testid="stWidgetLabel"] *,
    label, legend, small, .stCaption {
        color: #f0f0f3 !important;
    }
    h1, h2, h3, h4, h5, h6 { color: #ffffff !important; }
    p, li { line-height: 1.55; }
    input, textarea,
    [data-baseweb="input"] input,
    [data-baseweb="textarea"] textarea,
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stDateInput"] input,
    [data-testid="stTimeInput"] input {
        color: #ffffff !important;
        background-color: #242428 !important;
        -webkit-text-fill-color: #ffffff !important;
        caret-color: #e1ff6a !important;
        border-color: #56565f !important;
        opacity: 1 !important;
    }
    input::placeholder, textarea::placeholder,
    [data-baseweb="input"] input::placeholder,
    [data-baseweb="textarea"] textarea::placeholder {
        color: #b9b9c2 !important;
        -webkit-text-fill-color: #b9b9c2 !important;
        opacity: 1 !important;
    }
    [data-baseweb="select"] > div,
    [data-testid="stSelectbox"] [data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] [data-baseweb="select"] > div {
        background-color: #242428 !important;
        color: #ffffff !important;
        border-color: #56565f !important;
    }
    [data-baseweb="select"] *,
    [data-testid="stSelectbox"] *,
    [data-testid="stMultiSelect"] * {
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }
    [role="listbox"], [role="option"], [data-baseweb="popover"],
    [data-baseweb="menu"], [data-testid="stDateInput"] [data-baseweb="calendar"] {
        background-color: #242428 !important;
        color: #ffffff !important;
    }
    [role="option"]:hover, [role="option"][aria-selected="true"] {
        background-color: #3a3a42 !important;
    }
    [data-testid="stForm"], [data-testid="stExpander"],
    [data-testid="stExpander"] details, [data-testid="stTabs"] {
        color: #f5f5f7 !important;
        border-color: #42424a !important;
    }
    [data-testid="stForm"] { background-color: #202023 !important; }
    [data-testid="stRadio"] label, [data-testid="stCheckbox"] label,
    [data-testid="stToggle"] label, [data-testid="stSlider"] label {
        color: #f0f0f3 !important;
    }
    [data-testid="stDataFrame"], [data-testid="stTable"] {
        color: #f7f7fa !important;
        border-color: #42424a !important;
    }
    [data-testid="stDataFrame"] *, [data-testid="stTable"] * {
        color: #f7f7fa !important;
    }
    [data-testid="stMetricLabel"], [data-testid="stMetricValue"],
    [data-testid="stMetricDelta"] { color: #ffffff !important; }
    [data-testid="stAlert"] p, [data-testid="stAlert"] div,
    [data-testid="stNotification"] p { color: #f7f7fa !important; }
    .stButton > button, .stDownloadButton > button,
    [data-testid="stFormSubmitButton"] button {
        min-height: 2.65rem;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }
    .stButton > button[kind="primary"],
    [data-testid="stFormSubmitButton"] button[kind="primary"] {
        background-color: #7140ed !important;
        border-color: #8b6af2 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] *,
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
    [data-testid="stSidebar"] label { color: #eeeeef !important; }
    [data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked),
    [data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) * {
        color: #151515 !important;
        -webkit-text-fill-color: #151515 !important;
    }
    /* Login/setup screens: clear readable panel and high-contrast fields. */
    [data-testid="stForm"] [data-testid="stWidgetLabel"] *,
    [data-testid="stForm"] label,
    [data-testid="stForm"] p,
    [data-testid="stForm"] span { color: #f5f5f7 !important; }
    [data-testid="stForm"] input {
        background: #171719 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }
    /* INPUT VISIBILITY FIX — final override for text while typing */
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stDateInput"] input,
    [data-testid="stTimeInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-baseweb="input"] input,
    [data-baseweb="textarea"] textarea,
    input:not([type="checkbox"]):not([type="radio"]),
    textarea {
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #171719 !important;
        -webkit-text-fill-color: #171719 !important;
        caret-color: #5b35d5 !important;
        opacity: 1 !important;
        text-shadow: none !important;
        border: 1px solid #a9a9b3 !important;
        box-shadow: none !important;
    }
    [data-testid="stTextInput"] input:focus,
    [data-testid="stNumberInput"] input:focus,
    [data-testid="stDateInput"] input:focus,
    [data-testid="stTimeInput"] input:focus,
    [data-testid="stTextArea"] textarea:focus,
    [data-baseweb="input"] input:focus,
    [data-baseweb="textarea"] textarea:focus,
    input:focus, textarea:focus {
        background: #ffffff !important;
        color: #111111 !important;
        -webkit-text-fill-color: #111111 !important;
        caret-color: #5b35d5 !important;
        border-color: #7140ed !important;
        outline: 2px solid rgba(113, 64, 237, .22) !important;
        outline-offset: 1px !important;
    }
    input::placeholder, textarea::placeholder,
    [data-baseweb="input"] input::placeholder,
    [data-baseweb="textarea"] textarea::placeholder {
        color: #666673 !important;
        -webkit-text-fill-color: #666673 !important;
        opacity: 1 !important;
    }
    /* Keep selectbox values and opened menu options readable as well. */
    [data-baseweb="select"] input,
    [data-baseweb="select"] [role="combobox"],
    [data-baseweb="select"] span,
    [data-baseweb="select"] div[data-testid] {
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }
    [role="listbox"], [role="option"], [data-baseweb="menu"] {
        background-color: #242428 !important;
        color: #ffffff !important;
    }
    [role="option"] *, [data-baseweb="menu"] * {
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }
    /* Disabled fields remain visibly distinct but legible. */
    input:disabled, textarea:disabled {
        background-color: #e7e7eb !important;
        color: #555560 !important;
        -webkit-text-fill-color: #555560 !important;
        opacity: 1 !important;
    }
    @media (max-width: 900px) {
        [data-testid="stAppViewBlockContainer"] { margin: 8px; padding: 14px !important; }
        .search-look { max-width: 100%; } .user-look { text-align: left; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================ UI HELPERS ============================
def empty_state(title, description):
    st.markdown(
        f'<div class="empty"><div class="empty-icon">✦</div>'
        f'<div class="empty-title">{html.escape(title)}</div>'
        f'<div class="empty-text">{html.escape(description)}</div></div>',
        unsafe_allow_html=True,
    )


def show_table(df, empty_message="No records found."):
    """Display consistently numbered tables; keep serial numbers centered."""
    if df.empty:
        st.info(empty_message)
        return
    display_df = df.copy().reset_index(drop=True)
    if "S.No." not in display_df.columns:
        display_df.insert(0, "S.No.", np.arange(1, len(display_df) + 1, dtype=int))
    try:
        styled = display_df.style.set_properties(subset=["S.No."], **{"text-align": "center"})
        st.dataframe(styled, use_container_width=True, hide_index=True)
    except Exception:
        st.dataframe(display_df, use_container_width=True, hide_index=True)



def render_donut_chart(title, counts, palette=None):
    """Render a labelled, accessible SVG donut using real database counts."""
    palette = palette or ["#8B5CF6", "#A3E635", "#38BDF8", "#FB7185", "#F59E0B", "#14B8A6"]
    pairs = [(str(k), int(v)) for k, v in counts.items() if pd.notna(v) and int(v) > 0]
    total = sum(v for _, v in pairs)
    st.markdown(f"#### {html.escape(title)}")
    if total <= 0:
        st.info("Chart data will appear when records are available.")
        return
    radius, circumference = 54, 2 * math.pi * 54
    offset = 0
    segments = []
    legend = []
    for idx, (label, value) in enumerate(pairs):
        length = circumference * value / total
        color = palette[idx % len(palette)]
        segments.append(
            f'<circle cx="80" cy="80" r="{radius}" fill="none" stroke="{color}" '
            f'stroke-width="22" stroke-dasharray="{length:.3f} {circumference-length:.3f}" '
            f'stroke-dashoffset="{-offset:.3f}" transform="rotate(-90 80 80)"/>'
        )
        offset += length
        pct = value * 100 / total
        legend.append(
            f'<div style="display:flex;align-items:center;gap:8px;margin:7px 0;font-size:13px">'
            f'<span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:{color}"></span>'
            f'<span style="flex:1;color:#d8d8e0">{html.escape(label)}</span>'
            f'<b style="color:#fff">{value:,}</b><span style="color:#a1a1aa;min-width:48px;text-align:right">{pct:.1f}%</span></div>'
        )
    svg = (
        '<svg viewBox="0 0 160 160" role="img" aria-label="' + html.escape(title) + '" '
        'style="width:170px;max-width:100%;height:auto;display:block;margin:auto">'
        '<circle cx="80" cy="80" r="54" fill="none" stroke="#34343d" stroke-width="22"/>'
        + ''.join(segments) +
        f'<text x="80" y="76" fill="#ffffff" font-size="23" font-weight="800" text-anchor="middle">{total:,}</text>'
        '<text x="80" y="96" fill="#a1a1aa" font-size="10" text-anchor="middle">TOTAL</text></svg>'
    )
    st.markdown(
        '<div style="display:grid;grid-template-columns:minmax(150px,0.85fr) minmax(160px,1fr);'
        'gap:16px;align-items:center;padding:14px;border:1px solid #34343d;border-radius:16px;'
        'background:linear-gradient(135deg,#1b1b22,#24242d)">' + svg +
        '<div>' + ''.join(legend) + '</div></div>',
        unsafe_allow_html=True,
    )

def download_csv(df, filename, label="Download CSV"):
    if df.empty:
        st.caption("CSV export available when records exist.")
    else:
        st.download_button(
            label=label,
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name=filename,
            mime="text/csv",
        )


# ============================ DATABASE AUTH / PORTALS ====================
def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240000).hex()
    return salt, digest


def account_count():
    return int(query_df("SELECT COUNT(*) AS n FROM user_accounts").iloc[0]["n"])


def admin_account_count():
    # Company first-time setup should depend on whether an Admin exists,
    # not whether any Employee/HR account happens to exist in the database.
    return int(query_df("SELECT COUNT(*) AS n FROM user_accounts WHERE role='Admin'").iloc[0]["n"])


def verify_password(password, salt, expected_hash):
    return secrets.compare_digest(hash_password(password, salt)[1], expected_hash)


if "hcm_user" not in st.session_state:
    st.session_state.hcm_user = None

if admin_account_count() == 0:
    st.markdown(f'<div class="brand">{html.escape(get_company_name())}</div>', unsafe_allow_html=True)
    st.title("Register Your Company")
    st.info("Welcome! Complete this one-time setup to create your company and first Admin account. The app starts with a clean workspace and does not add fake employees or sample attendance.")
    with st.form("first_admin_setup"):
        setup_company = st.text_input("Company name", placeholder="e.g. ABC Technologies")
        setup_name = st.text_input("Administrator full name")
        setup_code = st.text_input("Admin user code", placeholder="ADMIN-001").strip().upper()
        setup_password = st.text_input("Create password (minimum 10 characters)", type="password")
        setup_confirm = st.text_input("Confirm password", type="password")
        setup_submit = st.form_submit_button("Register Company & Create Admin", type="primary", use_container_width=True)
    if setup_submit:
        if not setup_company.strip() or not setup_name.strip() or not setup_code or len(setup_password) < 10:
            st.error("Enter a company name, your name, and user code; password must be at least 10 characters.")
        elif setup_password != setup_confirm:
            st.error("Passwords do not match.")
        elif not query_df("SELECT user_code FROM user_accounts WHERE user_code=?", (setup_code,)).empty:
            st.error("That Admin user code already exists. Choose another one.")
        else:
            execute("INSERT INTO settings(key,value) VALUES('company_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (setup_company.strip(),))
            salt, digest = hash_password(setup_password)
            execute("INSERT INTO user_accounts(user_code,display_name,role,password_hash,password_salt,created_at) VALUES(?,?,?,?,?,?)",
                    (setup_code, setup_name.strip(), "Admin", digest, salt, pkt_now().isoformat(timespec="seconds")))
            log_action("Registered company and created first administrator", setup_company.strip() + " | " + setup_code)
            st.success("Company registered and Admin account created. Sign in with your new credentials.")
            st.rerun()
    st.stop()

if st.session_state.hcm_user is None:
    st.markdown(f'<div class="brand">{html.escape(get_company_name())}</div>', unsafe_allow_html=True)
    st.title("HCM Workspace Login")
    st.caption("Sign in using an account created by your administrator.")
    st.caption("Company registration appears automatically when this database has no Admin account. This app currently uses one company per database; registering another company in the same database could mix company data.")
    left, center, right = st.columns([1, 1.2, 1])
    with center:
        with st.form("hcm_login_form"):
            login_code = st.text_input("User code").strip().upper()
            login_password = st.text_input("Password", type="password")
            login_submit = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if login_submit:
            account_df = query_df("SELECT * FROM user_accounts WHERE user_code=? AND active=1", (login_code,))
            if not account_df.empty:
                row = account_df.iloc[0]
                if verify_password(login_password, str(row["password_salt"]), str(row["password_hash"])):
                    st.session_state.hcm_user = {"code": str(row["user_code"]), "name": str(row["display_name"]), "role": str(row["role"]), "employee_code": str(row["employee_code"] or "")}
                    log_action("Successful login", login_code)
                    st.rerun()
                else:
                    st.error("User code or password is incorrect.")
            else:
                st.error("User code or password is incorrect, or the account is inactive.")
    st.stop()
current_user = st.session_state.hcm_user
current_role = current_user["role"]

# ============================ SIDEBAR ===============================
with st.sidebar:
    st.markdown(f'<div class="brand">{html.escape(get_company_name())}</div>', unsafe_allow_html=True)
    st.markdown('<div class="eyebrow">HUMAN CAPITAL MANAGEMENT</div>', unsafe_allow_html=True)
    st.divider()
    st.markdown("### MENU")

    # `page` is created here before any page conditions use it.
    if current_role == "Admin":
        allowed_pages = ["Dashboard", "Organization Setup", "Employees", "User Accounts", "Attendance", "Leave Management", "Payroll", "Analytics", "AI Assistant", "Communications & Audit"]
    elif current_role == "HR":
        allowed_pages = ["Dashboard", "Employees", "Attendance", "Leave Management", "Payroll", "Analytics", "Communications & Audit"]
    else:
        allowed_pages = ["Employee Home", "My Attendance", "Leave & Requests", "Announcements"]
    page = st.radio("Navigation", allowed_pages, key="hcm_page_navigation", label_visibility="collapsed")
    st.divider()
    st.markdown("### SIGNED IN")
    st.caption(f"{current_user['name']} · {current_user['role']}")
    st.code(current_user["code"], language=None)
    if st.button("Log out", use_container_width=True):
        st.session_state.hcm_user = None
        st.rerun()
    st.divider()
    st.markdown("### SETTINGS")
    st.caption(get_company_name())
    st.caption("Python · Streamlit · Pandas · NumPy · SQLite")
    st.caption("Version 0.4.0 · Live database mode")


# =============================== HEADER =============================
now = pkt_now()
head_left, head_right = st.columns([3, 1])
with head_left:
    st.markdown('<div class="search-look">⌕ &nbsp; Search across your workspace...</div>', unsafe_allow_html=True)
with head_right:
    st.markdown(
        f'<div class="user-look">🌐 EN &nbsp;&nbsp; ◐ &nbsp;&nbsp; ♧<br>'
        f'<span style="color:#85858c;font-size:10px">{now.strftime("%d %B %Y · %I:%M %p")}</span></div>',
        unsafe_allow_html=True,
    )


# =========================== EMPLOYEE PORTAL =========================
if page == "Employee Home":
    st.title(f"Welcome, {current_user['name']}")
    st.caption(f"Employee self-service portal · User code: {current_user['code']}")
    latest_news = query_df("SELECT title, message, created_at FROM announcements ORDER BY id DESC LIMIT 3")
    with st.container(border=True):
        st.markdown("### 📣 Latest company announcements")
        if latest_news.empty:
            st.caption("No announcements have been published yet.")
        else:
            for idx, notice in enumerate(latest_news.itertuples(index=False)):
                with st.expander(f"📣 {str(notice.title)}  ·  Open announcement", expanded=False):
                    st.markdown(f"### {html.escape(str(notice.title))}")
                    st.write(str(notice.message))
                    st.caption(f"Published: {str(notice.created_at)} · Pakistan Standard Time")
    linked_employee_code = current_user.get("employee_code") or current_user["code"]
    employee = query_df("SELECT id, employee_code, full_name, email, department, designation, joining_date, status FROM employees WHERE employee_code=?", (linked_employee_code,))
    if employee.empty:
        st.warning("Your account is not linked to an employee record yet. Ask Admin/HR to link your account to your employee code.")
    else:
        st.subheader("My profile")
        profile = employee.rename(columns={"employee_code":"Employee Code", "full_name":"Full Name", "email":"Email", "department":"Department", "designation":"Designation", "joining_date":"Joining Date", "status":"Status"}).drop(columns=["id"], errors="ignore")
        show_table(profile)
        # Simple employee self-service time clock, using Pakistan Standard Time.
        st.subheader("⏱️ Today's attendance")
        st.info("One tap records the current time automatically in Pakistan Standard Time (PKT). No manual time entry is needed.")
        pkt_today = pkt_now().date().isoformat()
        employee_id = int(employee.iloc[0]["id"])
        today_clock = query_df("SELECT * FROM attendance WHERE employee_id=? AND work_date=?", (employee_id, pkt_today))
        if today_clock.empty:
            clock_in_done = False
            clock_out_done = False
            st.info("You have not checked in today.")
        else:
            clock_row = today_clock.iloc[0]
            clock_in_done = bool(clock_row.get("check_in"))
            clock_out_done = bool(clock_row.get("check_out"))
            c1, c2, c3 = st.columns(3)
            c1.metric("Check-in", str(clock_row.get("check_in") or "—"))
            c2.metric("Check-out", str(clock_row.get("check_out") or "—"))
            c3.metric("Hours worked", f"{float(clock_row.get('work_hours') or 0):.2f} h")
        a, b = st.columns(2)
        with a:
            if st.button("🟢 CHECK IN — Start work", type="primary", use_container_width=True, disabled=clock_in_done):
                stamp = pkt_now()
                try:
                    execute("INSERT INTO attendance(employee_id, work_date, check_in, check_out, status, work_hours) VALUES (?, ?, ?, NULL, 'Present', 0)", (employee_id, stamp.date().isoformat(), stamp.strftime("%H:%M:%S")))
                    log_action("Employee clocked in", f"{linked_employee_code} | {stamp.isoformat(timespec='seconds')}")
                    st.success(f"Checked in at {stamp.strftime('%I:%M:%S %p')} PKT")
                    st.rerun()
                except Exception:
                    st.error("Check-in could not be saved. Refresh and check whether today's attendance already exists.")
        with b:
            if st.button("🔴 CHECK OUT — Finish work", use_container_width=True, disabled=(not clock_in_done or clock_out_done)):
                stamp = pkt_now()
                row = query_df("SELECT check_in FROM attendance WHERE employee_id=? AND work_date=?", (employee_id, stamp.date().isoformat()))
                if row.empty or not row.iloc[0]["check_in"]:
                    st.error("Please check in first.")
                else:
                    start = datetime.strptime(str(row.iloc[0]["check_in"]), "%H:%M:%S") if len(str(row.iloc[0]["check_in"])) == 8 else datetime.strptime(str(row.iloc[0]["check_in"]), "%H:%M")
                    end = datetime.strptime(stamp.strftime("%H:%M:%S"), "%H:%M:%S")
                    seconds = (end - start).total_seconds()
                    if seconds < 0:
                        st.error("Check-out time is earlier than check-in. Please contact HR to correct the record.")
                    else:
                        hours = round(seconds / 3600, 2)
                        execute("UPDATE attendance SET check_out=?, work_hours=? WHERE employee_id=? AND work_date=? AND check_out IS NULL", (stamp.strftime("%H:%M:%S"), hours, employee_id, stamp.date().isoformat()))
                        log_action("Employee clocked out", f"{linked_employee_code} | {hours:.2f} hours | {stamp.isoformat(timespec='seconds')}")
                        st.success(f"Checked out at {stamp.strftime('%I:%M:%S %p')} PKT · Total: {hours:.2f} hours")
                        st.rerun()
        if clock_in_done and not clock_out_done:
            st.success("You are checked in. When your workday finishes, press CHECK OUT.")
        elif clock_out_done:
            st.success("Your attendance for today is complete. Contact HR if a correction is needed.")
        else:
            st.caption("Start your workday by pressing CHECK IN. Your computer time is not typed manually; the app records its server-side Pakistan time when you press the button.")
        st.caption("Times use Asia/Karachi (PKT, UTC+05:00). Overnight shifts crossing midnight need HR review.")
        attendance = query_df("SELECT work_date AS Date, check_in AS Check_In, check_out AS Check_Out, status AS Status, work_hours AS Work_Hours FROM attendance WHERE employee_id=(SELECT id FROM employees WHERE employee_code=?) ORDER BY work_date DESC LIMIT 30", (linked_employee_code,))
        st.subheader("My recent attendance")
        show_table(attendance, "No attendance records found for your account.")
        leave = query_df("SELECT leave_type AS Leave_Type, start_date AS Start_Date, end_date AS End_Date, days AS Days, reason AS Reason, status AS Status FROM leave_requests WHERE employee_id=(SELECT id FROM employees WHERE employee_code=?) ORDER BY id DESC", (linked_employee_code,))
        st.subheader("My leave requests")
        show_table(leave, "You have not submitted any leave requests yet.")
        st.subheader("Submit leave request")
        with st.form("employee_self_leave_form", clear_on_submit=True):
            a, b = st.columns(2)
            with a:
                self_leave_type = st.selectbox("Leave type", ["Annual", "Casual", "Sick", "Unpaid", "Maternity/Paternity", "Other"])
                self_start = st.date_input("Start date", value=pkt_now().date())
            with b:
                self_end = st.date_input("End date", value=pkt_now().date())
                self_reason = st.text_input("Reason")
            self_submit = st.form_submit_button("Submit leave request", type="primary")
        if self_submit:
            if self_end < self_start:
                st.error("End date cannot be earlier than start date.")
            elif not self_reason.strip():
                st.error("Please enter a reason.")
            else:
                days = (self_end - self_start).days + 1
                execute("INSERT INTO leave_requests(employee_id,leave_type,start_date,end_date,days,reason,status,created_at) VALUES((SELECT id FROM employees WHERE employee_code=?),?,?,?,?,?,?,?)", (linked_employee_code, self_leave_type, self_start.isoformat(), self_end.isoformat(), days, self_reason.strip(), "Pending", pkt_now().isoformat(timespec="seconds")))
                log_action("Employee submitted leave request", f"{linked_employee_code} | {self_leave_type}")
                st.success("Leave request submitted to HR.")
                st.rerun()

# ============================= DASHBOARD ============================
elif page == "Dashboard":
    active_employees = query_df("SELECT * FROM employees WHERE status='Active'")
    all_attendance_today = query_df("SELECT * FROM attendance WHERE work_date=?", (TODAY,))
    leave_today = query_df(
        "SELECT * FROM leave_requests WHERE status='Approved' AND start_date<=? AND end_date>=?",
        (TODAY, TODAY),
    )
    pending_df = query_df("SELECT * FROM leave_requests WHERE status='Pending'")

    headcount = len(active_employees)
    present_today = int(np.sum(all_attendance_today["status"].isin(["Present", "Late", "Half Day"]))) if not all_attendance_today.empty else 0
    late_today = int(np.sum(all_attendance_today["status"].eq("Late"))) if not all_attendance_today.empty else 0
    on_leave_today = int(leave_today["employee_id"].nunique()) if not leave_today.empty else 0
    pending_requests = len(pending_df)

    st.markdown(
        f'<div class="welcome"><div><div class="main-title">Good Morning, <span>Team!</span></div>'
        f'<div class="subtitle">Your people. Your insights. All in one place. · Demo data loaded</div>'
        f'<div class="date-line">{now.strftime("%d %B %Y")} &nbsp; · &nbsp; {html.escape(get_company_name())}</div></div>'
        f'<div class="actions"><div class="action">▦ &nbsp; Overview</div>'
        f'<div class="action">⇧ &nbsp; Analytics</div><div class="action-purple">＋ &nbsp; Add Employee → Employees</div></div></div>'
        f'<div class="section-label">Workforce Snapshot</div>',
        unsafe_allow_html=True,
    )

    metrics = st.columns(4)
    cards = [
        ("♙ &nbsp; Total Employees", str(headcount) if headcount else "—", "Active employee records", "purple"),
        ("✓ &nbsp; Present Today", str(present_today) if present_today else "—", "Attendance recorded today", "lime"),
        ("◷ &nbsp; On Leave", str(on_leave_today) if on_leave_today else "—", "Approved leave covering today", ""),
        ("▤ &nbsp; Pending Requests", str(pending_requests) if pending_requests else "—", f"Late check-ins today: {late_today}", ""),
    ]
    for col, (label, value, note, variant) in zip(metrics, cards):
        css_class = "metric-card" + (f" metric-{variant}" if variant else "")
        with col:
            st.markdown(
                f'<div class="{css_class}"><div class="metric-label">{label}</div>'
                f'<div class="metric-value">{value}</div><div class="metric-note">{note}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div class="section-label">Analytics & Insights</div>', unsafe_allow_html=True)
    chart_col, department_col = st.columns([1.7, 1])

    with chart_col:
        st.markdown('<div class="panel-title"><span class="lime">●</span> Attendance <span class="lime">Overview</span></div><div class="muted">Monthly attendance records by status</div>', unsafe_allow_html=True)
        attendance_history = query_df("SELECT work_date, status FROM attendance ORDER BY work_date")
        if attendance_history.empty:
            empty_state("Your analytics start here", "Attendance trends will appear after real attendance records are entered. No sample data is shown.")
        else:
            attendance_history["work_date"] = pd.to_datetime(attendance_history["work_date"], errors="coerce")
            attendance_history = attendance_history.dropna(subset=["work_date"])
            attendance_history["Month"] = attendance_history["work_date"].dt.strftime("%Y-%m")
            monthly = pd.crosstab(attendance_history["Month"], attendance_history["status"])
            for expected in ["Present", "Late", "Half Day", "Absent"]:
                if expected not in monthly.columns:
                    monthly[expected] = 0
            monthly = monthly[["Present", "Late", "Half Day", "Absent"]]
            st.bar_chart(monthly, height=300, color=["#7140ed", "#e1ff6a", "#aeb1b8", "#e87979"])

    with department_col:
        st.markdown('<div class="panel-title"><span class="lime">●</span> Department <span class="lime">Overview</span></div><div class="muted">Active employees by department</div>', unsafe_allow_html=True)
        if active_employees.empty:
            empty_state("No employee data yet", "Department analytics appear after employees are registered.")
        else:
            dept_counts = active_employees.groupby("department").size().sort_values(ascending=False)
            st.bar_chart(dept_counts, height=300, color="#e1ff6a")

    dash_att = query_df("SELECT status FROM attendance")
    dash_leave = query_df("SELECT status FROM leave_requests")
    pie_left, pie_right = st.columns(2)
    with pie_left:
        if not dash_att.empty:
            render_donut_chart("Attendance status · full circle", dash_att["status"].fillna("Unknown").value_counts(), ["#A3E635","#F59E0B","#FB7185","#8B5CF6","#38BDF8"])
    with pie_right:
        if not dash_leave.empty:
            render_donut_chart("Leave request status · full circle", dash_leave["status"].fillna("Unknown").value_counts(), ["#A3E635","#F59E0B","#38BDF8","#FB7185","#8B5CF6"])

    left_bottom, right_bottom = st.columns(2)
    with left_bottom:
        st.markdown('<div class="section-label">Upcoming Events</div>', unsafe_allow_html=True)
        holidays = query_df("SELECT holiday_date AS Date, title AS Event FROM holidays WHERE holiday_date>=? ORDER BY holiday_date LIMIT 5", (TODAY,))
        show_table(holidays, "No upcoming events. Add holidays in Organization Setup.")
    with right_bottom:
        st.markdown('<div class="section-label">Latest Announcements</div>', unsafe_allow_html=True)
        announcements = query_df("SELECT title AS Title, message AS Message, created_at AS Published FROM announcements ORDER BY id DESC LIMIT 5")
        show_table(announcements, "No announcements have been published yet.")

    st.markdown('<div class="footer-line"><b>HCM LITE</b><span>Human Capital Management · Python + Streamlit</span></div>', unsafe_allow_html=True)


# ========================== ORGANIZATION SETUP ======================
elif page == "Organization Setup":
    st.title("Organization Setup")
    st.caption("Configure company details, departments and holidays.")

    with st.form("company_settings_form"):
        company_value = st.text_input("Company name", value=get_company_name())
        save_company = st.form_submit_button("Save company name")
    if save_company:
        if not company_value.strip():
            st.error("Company name cannot be empty.")
        else:
            execute("INSERT INTO settings(key,value) VALUES('company_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (company_value.strip(),))
            log_action("Updated company name", company_value.strip())
            st.success("Company name saved.")
            st.rerun()

    st.subheader("Departments")
    with st.form("department_form", clear_on_submit=True):
        department_name = st.text_input("Department name", placeholder="e.g. Engineering")
        add_department = st.form_submit_button("Add department")
    if add_department:
        if not department_name.strip():
            st.error("Enter a department name.")
        else:
            try:
                execute("INSERT INTO departments(name) VALUES(?)", (department_name.strip(),))
                log_action("Created department", department_name.strip())
                st.success("Department added.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("This department already exists.")
    show_table(query_df("SELECT id AS ID, name AS Department FROM departments ORDER BY name"))

    st.subheader("Public Holidays")
    with st.form("holiday_form", clear_on_submit=True):
        holiday_title = st.text_input("Holiday name")
        holiday_date = st.date_input("Holiday date", value=pkt_now().date())
        add_holiday = st.form_submit_button("Add holiday")
    if add_holiday:
        if not holiday_title.strip():
            st.error("Enter a holiday name.")
        else:
            try:
                execute("INSERT INTO holidays(holiday_date,title) VALUES(?,?)", (holiday_date.isoformat(), holiday_title.strip()))
                log_action("Created holiday", holiday_title.strip())
                st.success("Holiday added.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("A holiday already exists on that date.")
    show_table(query_df("SELECT holiday_date AS Date, title AS Holiday FROM holidays ORDER BY holiday_date"))


# ================================ EMPLOYEES ==========================
elif page == "Employees":
    st.title("Employees")
    st.caption("Employee directory backed by SQLite.")

    departments_df = query_df("SELECT name FROM departments ORDER BY name")
    department_options = departments_df["name"].tolist() if not departments_df.empty else ["General"]

    with st.expander("＋ Register employee", expanded=False):
        with st.form("employee_form", clear_on_submit=True):
            left, right = st.columns(2)
            with left:
                employee_code = st.text_input("Employee ID", placeholder="EMP-001")
                full_name = st.text_input("Full name")
                email = st.text_input("Email address")
                designation = st.text_input("Designation")
            with right:
                department = st.selectbox("Department", department_options)
                joining_date = st.date_input("Joining date", value=pkt_now().date())
                basic_salary = st.number_input("Basic salary", min_value=0.0, step=1000.0)
                status = st.selectbox("Employment status", ["Active", "Probation", "Inactive"])
            save_employee = st.form_submit_button("Save employee")
        if save_employee:
            email_value = email.strip().lower()
            if not employee_code.strip() or not full_name.strip() or not email_value:
                st.error("Employee ID, full name and email are required.")
            elif "@" not in email_value or "." not in email_value.split("@")[-1]:
                st.error("Enter a valid email address.")
            else:
                try:
                    execute(
                        """INSERT INTO employees(employee_code,full_name,email,department,designation,joining_date,basic_salary,status,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?)""",
                        (employee_code.strip(), full_name.strip(), email_value, department, designation.strip(), joining_date.isoformat(), float(basic_salary), status, pkt_now().isoformat(timespec="seconds")),
                    )
                    log_action("Registered employee", f"{employee_code.strip()} - {full_name.strip()}")
                    st.success("Employee registered successfully.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("Employee ID or email already exists.")

    employee_data = query_df("SELECT employee_code AS Employee_ID, full_name AS Name, email AS Email, department AS Department, designation AS Designation, joining_date AS Joining_Date, basic_salary AS Basic_Salary, status AS Status FROM employees ORDER BY id DESC")
    search = st.text_input("Search employees", placeholder="Name, ID, email or department")
    if search.strip() and not employee_data.empty:
        mask = employee_data.astype(str).apply(lambda col: col.str.contains(search.strip(), case=False, regex=False, na=False)).any(axis=1)
        employee_data = employee_data.loc[mask]
    show_table(employee_data, "No employees registered yet. Use Register employee to add the first record.")

    current_employees = query_df("SELECT id, employee_code, full_name, email, department, designation, joining_date, basic_salary, status FROM employees ORDER BY full_name")
    if not current_employees.empty:
        st.subheader("Edit employee record")
        edit_labels = {int(r.id): f"{r.employee_code} — {r.full_name}" for r in current_employees.itertuples(index=False)}
        chosen_edit_id = st.selectbox("Choose employee", list(edit_labels.keys()), format_func=lambda v: edit_labels[v], key="employee_edit_choice")
        emp_row = current_employees[current_employees["id"] == chosen_edit_id].iloc[0]
        dept_choices = sorted(set(department_options + [str(emp_row["department"])]))
        with st.form("edit_employee_form"):
            e1, e2 = st.columns(2)
            with e1:
                edit_name = st.text_input("Full name", value=str(emp_row["full_name"]))
                edit_email = st.text_input("Email", value=str(emp_row["email"]))
                edit_designation = st.text_input("Designation", value=str(emp_row["designation"] or ""))
                edit_department = st.selectbox("Department", dept_choices, index=dept_choices.index(str(emp_row["department"])))
            with e2:
                edit_joining = st.date_input("Joining date", value=date.fromisoformat(str(emp_row["joining_date"])))
                edit_salary = st.number_input("Basic salary", min_value=0.0, value=float(emp_row["basic_salary"]), step=1000.0)
                edit_status = st.selectbox("Employment status", ["Active", "Probation", "Inactive"], index=["Active", "Probation", "Inactive"].index(str(emp_row["status"])))
            save_edit = st.form_submit_button("Save employee changes", type="primary")
        if save_edit:
            if not edit_name.strip() or "@" not in edit_email:
                st.error("Enter a full name and valid email address.")
            else:
                try:
                    execute("UPDATE employees SET full_name=?,email=?,department=?,designation=?,joining_date=?,basic_salary=?,status=? WHERE id=?",
                            (edit_name.strip(), edit_email.strip().lower(), edit_department, edit_designation.strip(), edit_joining.isoformat(), float(edit_salary), edit_status, int(chosen_edit_id)))
                    log_action("Updated employee", f"{emp_row['employee_code']} — {edit_name.strip()}")
                    st.success("Employee record updated.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("That email address is already assigned to another employee.")
    download_csv(employee_data, "hcm_employees.csv", "Export employees to CSV")


# ============================== USER ACCOUNTS =========================
elif page == "User Accounts":
    st.title("User Accounts & Portal Access")
    st.caption("Create real accounts for Admin, HR, and employees. Passwords are stored as salted PBKDF2 hashes.")
    employee_df = query_df("SELECT employee_code, full_name FROM employees WHERE status!='Inactive' ORDER BY full_name")
    with st.form("create_user_account_form", clear_on_submit=True):
        u1, u2 = st.columns(2)
        with u1:
            new_code = st.text_input("Login user code", placeholder="HR-002 or EMP-101").strip().upper()
            new_name = st.text_input("Display name")
            new_role = st.selectbox("Portal role", ["HR", "Employee", "Admin"])
        with u2:
            new_password = st.text_input("Temporary password (min 10 characters)", type="password")
            new_password_confirm = st.text_input("Confirm temporary password", type="password")
            employee_map = {str(r.employee_code): f"{r.employee_code} — {r.full_name}" for r in employee_df.itertuples(index=False)}
            link_options = [""] + list(employee_map.keys())
            linked_code = st.selectbox("Link employee record (required for Employee role)", link_options, format_func=lambda v: "— Not linked —" if not v else employee_map[v])
        create_account = st.form_submit_button("Create account", type="primary")
    if create_account:
        if not new_code or not new_name.strip():
            st.error("Login code and display name are required.")
        elif len(new_password) < 10:
            st.error("Use a temporary password of at least 10 characters.")
        elif new_password != new_password_confirm:
            st.error("Passwords do not match.")
        elif new_role == "Employee" and not linked_code:
            st.error("Register an employee first, then create/link the employee portal account.")
        else:
            salt, digest = hash_password(new_password)
            try:
                execute("INSERT INTO user_accounts(user_code,display_name,role,password_hash,password_salt,employee_code,active,created_at) VALUES(?,?,?,?,?,?,1,?)",
                        (new_code, new_name.strip(), new_role, digest, salt, linked_code or None, pkt_now().isoformat(timespec="seconds")))
                log_action("Created user account", f"{new_code} | {new_role} | linked employee: {linked_code or 'none'}")
                st.success(f"Account {new_code} created. Share the temporary password securely and ask the user to change it through an approved process.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("That login user code already exists.")

    st.subheader("Existing accounts")
    accounts = query_df("SELECT id AS Account_ID, user_code AS User_Code, display_name AS Name, role AS Role, employee_code AS Employee_Code, CASE WHEN active=1 THEN 'Active' ELSE 'Inactive' END AS Status, created_at AS Created FROM user_accounts ORDER BY id")
    show_table(accounts, "No user accounts found.")
    if current_role == "Admin" and not accounts.empty:
        st.subheader("Activate or deactivate account")
        account_ids = query_df("SELECT id, user_code, active FROM user_accounts ORDER BY user_code")
        labels = {int(r.id): f"{r.user_code} ({'Active' if r.active else 'Inactive'})" for r in account_ids.itertuples(index=False)}
        with st.form("account_status_form"):
            target_account = st.selectbox("Account", list(labels.keys()), format_func=lambda v: labels[v])
            target_status = st.selectbox("Set status", ["Active", "Inactive"])
            update_status = st.form_submit_button("Update account status")
        if update_status:
            target_row = account_ids[account_ids["id"] == target_account].iloc[0]
            if str(target_row["user_code"]) == current_user["code"] and target_status == "Inactive":
                st.error("You cannot deactivate your currently signed-in account.")
            else:
                execute("UPDATE user_accounts SET active=? WHERE id=?", (1 if target_status == "Active" else 0, int(target_account)))
                log_action("Changed account status", f"{target_row['user_code']} -> {target_status}")
                st.success("Account status updated.")
                st.rerun()
    download_csv(accounts, "hcm_user_accounts.csv", "Export account list")
    st.divider()
    st.subheader("Optional demo-data cleanup")
    st.caption("Only use this if your existing database contains the old synthetic DEMO-### records. It will delete their attendance and leave records too.")
    confirm_cleanup = st.checkbox("I understand this permanently deletes records for employee codes beginning DEMO-", key="confirm_demo_cleanup")
    if st.button("Remove old DEMO records", disabled=not confirm_cleanup):
        demo_ids = query_df("SELECT id FROM employees WHERE employee_code LIKE 'DEMO-%'")
        with connect_db() as conn:
            conn.execute("DELETE FROM leave_requests WHERE employee_id IN (SELECT id FROM employees WHERE employee_code LIKE 'DEMO-%')")
            conn.execute("DELETE FROM attendance WHERE employee_id IN (SELECT id FROM employees WHERE employee_code LIKE 'DEMO-%')")
            conn.execute("DELETE FROM user_accounts WHERE employee_code LIKE 'DEMO-%' OR user_code LIKE 'DEMO-%'")
            conn.execute("DELETE FROM employees WHERE employee_code LIKE 'DEMO-%'")
            conn.execute("DELETE FROM announcements WHERE title='Welcome to HCM Lite' AND message LIKE '%synthetic demo content%'")
            conn.execute("DELETE FROM holidays WHERE title='Demo Company Day'")
            conn.execute("DELETE FROM settings WHERE key='demo_data_seeded'")
            conn.commit()
        log_action("Removed old synthetic demo records", f"Deleted {len(demo_ids)} DEMO employee records and related data")
        st.success(f"Removed {len(demo_ids)} old DEMO employee records and related attendance/leave data.")
        st.rerun()


elif page == "My Attendance":
    st.title("📅 My Attendance")
    st.caption("Your latest attendance records and working hours.")
    linked_code = current_user.get("employee_code") or current_user["code"]
    att = query_df("SELECT work_date AS Date, check_in AS Check_In, check_out AS Check_Out, status AS Status, work_hours AS Hours FROM attendance WHERE employee_id=(SELECT id FROM employees WHERE employee_code=?) ORDER BY work_date DESC LIMIT 60", (linked_code,))
    if not att.empty:
        c1, c2 = st.columns(2)
        c1.metric("Recorded days", len(att))
        c2.metric("Hours recorded", f"{pd.to_numeric(att['Hours'], errors='coerce').fillna(0).sum():.2f}")
        st.line_chart(att.sort_values("Date").set_index("Date")["Hours"], height=300)
    show_table(att, "No attendance records found for your employee account.")
elif page == "Leave & Requests":
    st.title("🗓️ Leave & Requests")
    st.caption("Apply for leave and track the decision from HR.")
    linked_code = current_user.get("employee_code") or current_user["code"]
    emp = query_df("SELECT id FROM employees WHERE employee_code=?", (linked_code,))
    if emp.empty:
        st.warning("Your account is not linked to an employee record yet. Ask Admin/HR to link it.")
    else:
        with st.form("employee_leave_requests_page", clear_on_submit=True):
            a, b = st.columns(2)
            with a:
                lt = st.selectbox("Leave type", ["Annual", "Casual", "Sick", "Unpaid", "Maternity/Paternity", "Other"])
                ls = st.date_input("Start date", value=pkt_now().date(), key="employee_leave_start_page")
            with b:
                le = st.date_input("End date", value=pkt_now().date(), key="employee_leave_end_page")
                reason = st.text_input("Purpose / reason")
            submitted = st.form_submit_button("Send leave request to HR", type="primary", use_container_width=True)
        if submitted:
            if le < ls:
                st.error("End date cannot be earlier than start date.")
            elif not reason.strip():
                st.error("Please enter the purpose of your leave.")
            else:
                days = (le-ls).days+1
                execute("INSERT INTO leave_requests(employee_id,leave_type,start_date,end_date,days,reason,status,created_at) VALUES(?,?,?,?,?,?,?,?)", (int(emp.iloc[0]["id"]), lt, ls.isoformat(), le.isoformat(), days, reason.strip(), "Pending", pkt_now().isoformat(timespec="seconds")))
                log_action("Employee submitted leave request", f"{linked_code} | {lt}")
                st.success("Leave request submitted successfully.")
                st.rerun()
        my_leave = query_df("SELECT leave_type AS Leave_Type, start_date AS Start_Date, end_date AS End_Date, days AS Days, reason AS Purpose, status AS Status FROM leave_requests WHERE employee_id=? ORDER BY id DESC", (int(emp.iloc[0]["id"]),))
        show_table(my_leave, "You have not submitted any leave requests yet.")
elif page == "Announcements":
    st.title("📣 Company Announcements")
    st.caption("Official updates published by your Admin/HR team.")
    news = query_df("SELECT title, message, created_at FROM announcements ORDER BY id DESC")
    if news.empty:
        st.info("There are no announcements yet. New notices from Admin/HR will appear here.")
    else:
        for idx, item in enumerate(news.itertuples(index=False)):
            with st.container(border=True):
                st.markdown("**📣 OFFICIAL COMPANY NOTICE**")
                with st.expander(f"{str(item.title)}  ·  Click to read full message", expanded=False):
                    st.markdown(f"### {html.escape(str(item.title))}")
                    st.write(str(item.message))
                    st.divider()
                    st.caption(f"Published: {str(item.created_at)} · Pakistan Standard Time")

# ============================== ATTENDANCE ===========================
elif page == "Attendance":
    st.title("Attendance Management")
    st.caption("Quick clock-in/out and attendance review · all clock times use Pakistan Standard Time (PKT, UTC+05:00).")

    # Admin/HR can clock in/out on behalf of an employee where needed.
    st.subheader("Quick check-in / check-out")
    st.info("Select an active employee, then use one button. The app records the current Pakistan time automatically. Use Manual attendance correction below only for genuine corrections.")
    clock_employees = query_df("SELECT id, employee_code, full_name FROM employees WHERE status='Active' ORDER BY full_name")
    if clock_employees.empty:
        st.info("Add an active employee first to start tracking attendance.")
    else:
        clock_labels = {int(row.id): f"{row.employee_code} — {row.full_name}" for row in clock_employees.itertuples(index=False)}
        selected_clock_id = st.selectbox("Choose employee", list(clock_labels.keys()), format_func=lambda value: clock_labels[value], key="quick_clock_employee")
        clock_day = pkt_now().date().isoformat()
        existing_clock = query_df("SELECT check_in, check_out, work_hours, status FROM attendance WHERE employee_id=? AND work_date=?", (selected_clock_id, clock_day))
        if existing_clock.empty:
            st.info("No attendance punch recorded for this employee today.")
        else:
            r = existing_clock.iloc[0]
            m1, m2, m3 = st.columns(3)
            m1.metric("Check-in (PKT)", str(r.get("check_in") or "—"))
            m2.metric("Check-out (PKT)", str(r.get("check_out") or "—"))
            m3.metric("Worked", f"{float(r.get('work_hours') or 0):.2f} h")
        ca, cb = st.columns(2)
        with ca:
            if st.button("▶ Check in selected employee", type="primary", use_container_width=True, key="hr_clock_in"):
                stamp = pkt_now()
                current = query_df("SELECT id, check_in FROM attendance WHERE employee_id=? AND work_date=?", (selected_clock_id, stamp.date().isoformat()))
                if not current.empty and current.iloc[0]["check_in"]:
                    st.warning("This employee is already checked in today.")
                elif not current.empty:
                    execute("UPDATE attendance SET check_in=?, status='Present' WHERE employee_id=? AND work_date=?", (stamp.strftime("%H:%M:%S"), selected_clock_id, stamp.date().isoformat()))
                    log_action("HR/Admin clocked employee in", f"{clock_labels[selected_clock_id]} | {stamp.isoformat(timespec='seconds')}")
                    st.success(f"Checked in at {stamp.strftime('%I:%M:%S %p')} PKT")
                    st.rerun()
                else:
                    execute("INSERT INTO attendance(employee_id, work_date, check_in, status, work_hours) VALUES (?, ?, ?, 'Present', 0)", (selected_clock_id, stamp.date().isoformat(), stamp.strftime("%H:%M:%S")))
                    log_action("HR/Admin clocked employee in", f"{clock_labels[selected_clock_id]} | {stamp.isoformat(timespec='seconds')}")
                    st.success(f"Checked in at {stamp.strftime('%I:%M:%S %p')} PKT")
                    st.rerun()
        with cb:
            if st.button("■ Check out selected employee", use_container_width=True, key="hr_clock_out"):
                stamp = pkt_now()
                current = query_df("SELECT check_in, check_out FROM attendance WHERE employee_id=? AND work_date=?", (selected_clock_id, stamp.date().isoformat()))
                if current.empty or not current.iloc[0]["check_in"]:
                    st.error("Employee must check in before checking out.")
                elif current.iloc[0]["check_out"]:
                    st.warning("This employee has already checked out today.")
                else:
                    in_text = str(current.iloc[0]["check_in"])
                    start = datetime.strptime(in_text, "%H:%M:%S") if len(in_text) == 8 else datetime.strptime(in_text, "%H:%M")
                    end = datetime.strptime(stamp.strftime("%H:%M:%S"), "%H:%M:%S")
                    seconds = (end-start).total_seconds()
                    if seconds < 0:
                        st.error("Check-out time is earlier than check-in. Overnight shift requires HR correction.")
                    else:
                        hours = round(seconds/3600, 2)
                        execute("UPDATE attendance SET check_out=?, work_hours=? WHERE employee_id=? AND work_date=?", (stamp.strftime("%H:%M:%S"), hours, selected_clock_id, stamp.date().isoformat()))
                        log_action("HR/Admin clocked employee out", f"{clock_labels[selected_clock_id]} | {hours:.2f} hours")
                        st.success(f"Checked out at {stamp.strftime('%I:%M:%S %p')} PKT · {hours:.2f} hours worked")
                        st.rerun()
    st.divider()
    st.subheader("Manual attendance correction")

    employee_options = query_df(
        "SELECT id, employee_code, full_name FROM employees WHERE status='Active' ORDER BY full_name"
    )
    if employee_options.empty:
        st.warning("Register an employee first.")
    else:
        with st.form("attendance_entry_form", clear_on_submit=False):
            labels = {
                int(row.id): f"{row.employee_code} — {row.full_name}"
                for row in employee_options.itertuples(index=False)
            }
            chosen_id = st.selectbox(
                "Employee", list(labels.keys()),
                format_func=lambda value: labels[value]
            )
            work_date = st.date_input("Attendance date", value=pkt_now().date())
            a, b, c = st.columns(3)
            with a:
                check_in = st.time_input("Check-in time", value=datetime.strptime("09:00", "%H:%M").time())
            with b:
                check_out = st.time_input("Check-out time", value=datetime.strptime("17:30", "%H:%M").time())
            with c:
                attendance_status = st.selectbox("Status", ["Present", "Late", "Half Day", "Absent", "On Leave"])
            save_attendance = st.form_submit_button("Save attendance", type="primary")

        if save_attendance:
            if attendance_status in ("Present", "Late", "Half Day") and check_out < check_in:
                st.error("Check-out time must be after check-in time.")
            else:
                work_hours = 0.0
                in_text = None
                out_text = None
                if attendance_status in ("Present", "Late", "Half Day"):
                    in_text = check_in.strftime("%H:%M")
                    out_text = check_out.strftime("%H:%M")
                    start_minutes = check_in.hour * 60 + check_in.minute
                    end_minutes = check_out.hour * 60 + check_out.minute
                    work_hours = round((end_minutes - start_minutes) / 60, 2)
                execute(
                    """INSERT INTO attendance(employee_id, work_date, check_in, check_out, status, work_hours)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(employee_id, work_date) DO UPDATE SET
                    check_in=excluded.check_in, check_out=excluded.check_out,
                    status=excluded.status, work_hours=excluded.work_hours""",
                    (int(chosen_id), work_date.isoformat(), in_text, out_text, attendance_status, work_hours),
                )
                log_action("Saved attendance", f"{labels[int(chosen_id)]} | {work_date.isoformat()} | {attendance_status}")
                st.success("Attendance saved to the database.")
                st.rerun()

    st.subheader("Attendance records")
    filter_date = st.date_input("Filter by date", value=pkt_now().date(), key="attendance_filter_date")
    attendance_data = query_df(
        """SELECT e.employee_code AS Employee_ID, e.full_name AS Employee, e.department AS Department,
        a.work_date AS Date, a.check_in AS Check_In, a.check_out AS Check_Out, a.status AS Status, a.work_hours AS Work_Hours
        FROM attendance a JOIN employees e ON e.id=a.employee_id
        WHERE a.work_date=? ORDER BY e.full_name""",
        (filter_date.isoformat(),)
    )
    show_table(attendance_data, "No attendance records for the selected date.")
    download_csv(attendance_data, "hcm_attendance.csv", "Export selected date to CSV")

    with st.expander("View all attendance history"):
        all_attendance = query_df(
            """SELECT e.employee_code AS Employee_ID, e.full_name AS Employee, e.department AS Department,
            a.work_date AS Date, a.check_in AS Check_In, a.check_out AS Check_Out, a.status AS Status, a.work_hours AS Work_Hours
            FROM attendance a JOIN employees e ON e.id=a.employee_id
            ORDER BY a.work_date DESC, e.full_name"""
        )
        show_table(all_attendance, "No attendance history available.")
        download_csv(all_attendance, "hcm_attendance_history.csv", "Export all attendance history")


# ============================ LEAVE MANAGEMENT =======================
elif page == "Leave Management":
    st.title("Leave Management")
    st.caption("Submit leave requests, review pending applications, and approve or reject requests.")

    employee_options = query_df("SELECT id, employee_code, full_name FROM employees WHERE status!='Inactive' ORDER BY full_name")
    with st.expander("＋ Submit a leave request", expanded=False):
        if employee_options.empty:
            st.info("Register an employee before submitting a leave request.")
        else:
            emp_labels = {int(r.id): f"{r.employee_code} — {r.full_name}" for r in employee_options.itertuples(index=False)}
            with st.form("leave_request_form", clear_on_submit=True):
                selected_emp = st.selectbox("Employee", list(emp_labels.keys()), format_func=lambda v: emp_labels[v])
                l1, l2 = st.columns(2)
                with l1:
                    leave_type = st.selectbox("Leave type", ["Annual", "Casual", "Sick", "Unpaid", "Maternity/Paternity", "Other"])
                    leave_start = st.date_input("Start date", value=pkt_now().date())
                with l2:
                    leave_end = st.date_input("End date", value=pkt_now().date())
                    leave_reason = st.text_input("Reason")
                submit_leave = st.form_submit_button("Submit request", type="primary")
            if submit_leave:
                if leave_end < leave_start:
                    st.error("End date cannot be earlier than start date.")
                elif not leave_reason.strip():
                    st.error("Please enter a reason for the leave request.")
                else:
                    days = (leave_end - leave_start).days + 1
                    execute("INSERT INTO leave_requests(employee_id,leave_type,start_date,end_date,days,reason,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (int(selected_emp), leave_type, leave_start.isoformat(), leave_end.isoformat(), days, leave_reason.strip(), "Pending", pkt_now().isoformat(timespec="seconds")))
                    log_action("Submitted leave request", f"{emp_labels[int(selected_emp)]} | {leave_type} | {leave_start} to {leave_end}")
                    st.success("Leave request submitted.")
                    st.rerun()

    pending_leave = query_df("SELECT l.id, e.employee_code, e.full_name, l.leave_type, l.start_date, l.end_date, l.days, l.reason FROM leave_requests l JOIN employees e ON e.id=l.employee_id WHERE l.status='Pending' ORDER BY l.id")
    st.subheader("Pending approvals")
    if pending_leave.empty:
        st.info("There are no pending leave requests.")
    else:
        for req in pending_leave.itertuples(index=False):
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 2, 2])
                c1.markdown(f"**{req.employee_code} · {req.full_name}** · {req.leave_type}")
                c1.caption(f"{req.start_date} → {req.end_date} · {req.days:g} day(s)")
                c2.write(req.reason)
                with c3:
                    b1, b2 = st.columns(2)
                    if b1.button("Approve", key=f"approve_leave_{req.id}", type="primary", use_container_width=True):
                        execute("UPDATE leave_requests SET status='Approved' WHERE id=?", (int(req.id),))
                        log_action("Approved leave request", f"Request #{req.id} — {req.full_name}")
                        st.rerun()
                    if b2.button("Reject", key=f"reject_leave_{req.id}", use_container_width=True):
                        execute("UPDATE leave_requests SET status='Rejected' WHERE id=?", (int(req.id),))
                        log_action("Rejected leave request", f"Request #{req.id} — {req.full_name}")
                        st.rerun()

    st.subheader("All leave requests")
    leave_data = query_df(
        """SELECT l.id AS Request_ID, e.employee_code AS Employee_ID, e.full_name AS Employee,
        l.leave_type AS Leave_Type, l.start_date AS Start_Date, l.end_date AS End_Date,
        l.days AS Days, l.reason AS Reason, l.status AS Status
        FROM leave_requests l JOIN employees e ON e.id=l.employee_id ORDER BY l.id DESC"""
    )
    show_table(leave_data, "No leave requests yet.")
    download_csv(leave_data, "hcm_leave_requests.csv", "Export leave requests to CSV")


# ================================= PAYROLL ===========================
elif page == "Payroll":
    st.title("Payroll & Salary Processing")
    st.caption("Create monthly payroll records, calculate net salary, track payment status, and export payroll reports.")
    st.info("Calculation used: Net salary = Basic salary + Allowances + Bonus − Deductions. Configure legal/statutory deductions with a qualified payroll professional before production use.")

    active_employees = query_df("SELECT id, employee_code, full_name, department, basic_salary FROM employees WHERE status!='Inactive' ORDER BY full_name")
    if active_employees.empty:
        st.warning("Add active employees and their basic salaries first in Employees.")
    else:
        today = pkt_now().date()
        p1, p2 = st.columns(2)
        with p1:
            pay_year = st.number_input("Payroll year", min_value=2000, max_value=2100, value=today.year, step=1)
        with p2:
            pay_month = st.selectbox("Payroll month", list(range(1, 13)), index=today.month-1, format_func=lambda m: date(2000, m, 1).strftime("%B"))

        st.subheader("Create or update an employee payroll record")
        employee_options = {f"{r.employee_code} — {r.full_name} ({float(r.basic_salary):,.2f})": int(r.id) for r in active_employees.itertuples()}
        with st.form("payroll_record_form"):
            selected_label = st.selectbox("Employee", list(employee_options.keys()))
            selected_id = employee_options[selected_label]
            selected_emp = active_employees.loc[active_employees["id"] == selected_id].iloc[0]
            existing = query_df("SELECT * FROM payroll_records WHERE employee_id=? AND pay_year=? AND pay_month=?", (selected_id, int(pay_year), int(pay_month)))
            default_allowances = float(existing.iloc[0]["allowances"]) if not existing.empty else 0.0
            default_bonus = float(existing.iloc[0]["bonus"]) if not existing.empty else 0.0
            default_deductions = float(existing.iloc[0]["deductions"]) if not existing.empty else 0.0
            basic = st.number_input("Basic salary", min_value=0.0, value=float(existing.iloc[0]["basic_salary"]) if not existing.empty else float(selected_emp["basic_salary"]), step=1000.0)
            allowances = st.number_input("Allowances", min_value=0.0, value=default_allowances, step=500.0)
            bonus = st.number_input("Bonus / incentive", min_value=0.0, value=default_bonus, step=500.0)
            deductions = st.number_input("Deductions", min_value=0.0, value=default_deductions, step=500.0)
            submitted = st.form_submit_button("Save payroll record", type="primary", use_container_width=True)
        if submitted:
            net = float(basic + allowances + bonus - deductions)
            if net < 0:
                st.error("Deductions cannot result in a negative net salary.")
            else:
                execute("""INSERT INTO payroll_records(employee_id,pay_year,pay_month,basic_salary,allowances,bonus,deductions,net_salary,payment_status,created_at,paid_at)
                    VALUES(?,?,?,?,?,?,?,?, 'Draft', ?, NULL)
                    ON CONFLICT(employee_id,pay_year,pay_month) DO UPDATE SET
                    basic_salary=excluded.basic_salary, allowances=excluded.allowances, bonus=excluded.bonus,
                    deductions=excluded.deductions, net_salary=excluded.net_salary,
                    payment_status=CASE WHEN payroll_records.payment_status='Paid' THEN 'Draft' ELSE payroll_records.payment_status END,
                    created_at=excluded.created_at, paid_at=NULL""",
                    (selected_id, int(pay_year), int(pay_month), float(basic), float(allowances), float(bonus), float(deductions), net, pkt_now().isoformat(timespec="seconds")))
                log_action("Saved payroll record", f"{selected_emp['employee_code']} {int(pay_year)}-{int(pay_month):02d} net={net:.2f}")
                st.success(f"Payroll saved. Net salary: {net:,.2f}")
                st.rerun()

        payroll = query_df("""SELECT p.id AS Record_No, e.employee_code AS Employee_Code, e.full_name AS Employee,
            e.department AS Department, p.pay_year AS Year, p.pay_month AS Month, p.basic_salary AS Basic_Salary,
            p.allowances AS Allowances, p.bonus AS Bonus, p.deductions AS Deductions, p.net_salary AS Net_Salary,
            p.payment_status AS Payment_Status, p.paid_at AS Paid_At
            FROM payroll_records p JOIN employees e ON e.id=p.employee_id
            WHERE p.pay_year=? AND p.pay_month=? ORDER BY e.employee_code""", (int(pay_year), int(pay_month)))
        st.subheader(f"Payroll register — {date(int(pay_year), int(pay_month), 1).strftime('%B %Y')}")
        if not payroll.empty:
            c1, c2, c3 = st.columns(3)
            c1.metric("Employees in payroll", len(payroll))
            c2.metric("Total gross", f"{(payroll['Basic_Salary'] + payroll['Allowances'] + payroll['Bonus']).sum():,.2f}")
            c3.metric("Total net payroll", f"{payroll['Net_Salary'].sum():,.2f}")
            show_table(payroll, "No payroll records for this period.")
            download_csv(payroll, f"payroll_{int(pay_year)}_{int(pay_month):02d}.csv", "Export monthly payroll CSV")

            # Individual printable payslip (HTML; can be printed/saved as PDF from browser)
            st.subheader("Generate individual payslip")
            slip_labels = {
                f"{r.Employee_Code} — {r.Employee} — {float(r.Net_Salary):,.2f}": int(r.Record_No)
                for r in payroll.itertuples()
            }
            slip_label = st.selectbox("Choose employee payroll record", list(slip_labels.keys()), key="payslip_record_select")
            slip_row = payroll.loc[payroll["Record_No"] == slip_labels[slip_label]].iloc[0]
            company_name_df = query_df("SELECT value FROM settings WHERE key='company_name'")
            company_name = str(company_name_df.iloc[0]["value"]) if not company_name_df.empty else "HCM Lite"
            period_label = date(int(pay_year), int(pay_month), 1).strftime("%B %Y")
            safe_company = html.escape(company_name)
            safe_employee = html.escape(str(slip_row["Employee"]))
            safe_code = html.escape(str(slip_row["Employee_Code"]))
            safe_department = html.escape(str(slip_row["Department"] or "—"))
            safe_status = html.escape(str(slip_row["Payment_Status"]))
            slip_html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Payslip - {safe_code} - {period_label}</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;margin:0;padding:28px;color:#202124}}
.sheet{{max-width:760px;margin:auto;background:white;padding:34px;border:1px solid #ddd}}
h1{{margin:0;color:#252525}}.muted{{color:#666}}.top{{display:flex;justify-content:space-between;gap:20px;border-bottom:3px solid #b7e34a;padding-bottom:18px}}
table{{width:100%;border-collapse:collapse;margin-top:24px}}th,td{{padding:12px;border-bottom:1px solid #ddd;text-align:left}}td.amount{{text-align:right;font-variant-numeric:tabular-nums}}th{{background:#f4f6ef}}
.total{{font-size:20px;font-weight:bold;background:#f4f8e8}}.foot{{margin-top:32px;font-size:12px;color:#666}}
@media print{{body{{background:white;padding:0}}.sheet{{border:0;margin:0;max-width:none}}}}
</style></head><body><main class="sheet">
<div class="top"><div><h1>{safe_company}</h1><p class="muted">Employee salary payslip</p></div><div><strong>Pay period</strong><br>{html.escape(period_label)}<br><span class="muted">Status: {safe_status}</span></div></div>
<p><strong>Employee:</strong> {safe_employee}<br><strong>Employee code:</strong> {safe_code}<br><strong>Department:</strong> {safe_department}</p>
<table><thead><tr><th>Earnings / Deductions</th><th style="text-align:right">Amount</th></tr></thead><tbody>
<tr><td>Basic salary</td><td class="amount">{float(slip_row['Basic_Salary']):,.2f}</td></tr>
<tr><td>Allowances</td><td class="amount">{float(slip_row['Allowances']):,.2f}</td></tr>
<tr><td>Bonus / incentive</td><td class="amount">{float(slip_row['Bonus']):,.2f}</td></tr>
<tr><td>Deductions</td><td class="amount">− {float(slip_row['Deductions']):,.2f}</td></tr>
<tr class="total"><td>Net salary</td><td class="amount">{float(slip_row['Net_Salary']):,.2f}</td></tr>
</tbody></table><p class="foot">This payslip was generated by the HCM Lite application. Verify the amounts and applicable statutory deductions before payment. This document is not proof of bank transfer.</p>
</main></body></html>"""
            st.download_button(
                "Download printable payslip (HTML)",
                data=slip_html,
                file_name=f"payslip_{safe_code}_{int(pay_year)}_{int(pay_month):02d}.html",
                mime="text/html",
                use_container_width=True,
            )
            st.caption("Open the downloaded HTML file in a browser and use Print → Save as PDF if you need a PDF copy.")

            st.subheader("Mark a payroll record as paid")
            draft_records = payroll[payroll["Payment_Status"] != "Paid"]
            if not draft_records.empty:
                labels = {f"{r.Employee_Code} — {r.Employee} — {float(r.Net_Salary):,.2f}": int(r.Record_No) for r in draft_records.itertuples()}
                with st.form("mark_payroll_paid_form"):
                    record_label = st.selectbox("Select record", list(labels.keys()))
                    confirm_paid = st.checkbox("I confirm this salary has actually been paid")
                    mark_paid = st.form_submit_button("Mark as Paid")
                if mark_paid:
                    if not confirm_paid:
                        st.error("Confirm payment before changing the status.")
                    else:
                        execute("UPDATE payroll_records SET payment_status='Paid', paid_at=? WHERE id=?", (pkt_now().isoformat(timespec="seconds"), labels[record_label]))
                        log_action("Marked payroll paid", record_label)
                        st.success("Payroll record marked as paid.")
                        st.rerun()
            else:
                st.success("All records in this payroll period are marked Paid.")
        else:
            st.info("No payroll records exist for this month yet. Select an employee above to create the first record.")


# ============================== ANALYTICS ============================
elif page == "Analytics":
    st.title("📊 People Analytics Studio")
    st.caption("Interactive-looking, colour-coded charts generated from your real HCM database records.")
    st.markdown("""<div style="display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 20px">
    <div style="flex:1;min-width:135px;padding:14px;border-radius:13px;background:#22212c;border:1px solid #48405e"><b style="color:#c4b5fd">01 · PEOPLE</b><div style="font-size:17px;font-weight:750;margin-top:5px">Employee setup</div><small>Register and activate staff</small></div>
    <div style="align-self:center;color:#a78bfa;font-size:22px">➜</div>
    <div style="flex:1;min-width:135px;padding:14px;border-radius:13px;background:#202b2a;border:1px solid #31534a"><b style="color:#6ee7b7">02 · TIME</b><div style="font-size:17px;font-weight:750;margin-top:5px">Attendance</div><small>Track check-in and check-out</small></div>
    <div style="align-self:center;color:#34d399;font-size:22px">➜</div>
    <div style="flex:1;min-width:135px;padding:14px;border-radius:13px;background:#28232a;border:1px solid #5b3b54"><b style="color:#f9a8d4">03 · APPROVAL</b><div style="font-size:17px;font-weight:750;margin-top:5px">Leave & payroll</div><small>Review requests and pay</small></div>
    <div style="align-self:center;color:#f9a8d4;font-size:22px">➜</div>
    <div style="flex:1;min-width:135px;padding:14px;border-radius:13px;background:#202632;border:1px solid #394c68"><b style="color:#93c5fd">04 · INSIGHTS</b><div style="font-size:17px;font-weight:750;margin-top:5px">Analytics</div><small>Use evidence to decide</small></div></div>""", unsafe_allow_html=True)

    employee_data = query_df("SELECT department, status, basic_salary FROM employees")
    attendance_data = query_df("SELECT work_date, status FROM attendance")
    leave_data = query_df("SELECT leave_type, status, days FROM leave_requests")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Employee records", f"{len(employee_data):,}")
    m2.metric("Attendance records", f"{len(attendance_data):,}")
    m3.metric("Leave requests", f"{len(leave_data):,}")
    pending_count = int((leave_data["status"].astype(str).str.casefold() == "pending").sum()) if not leave_data.empty else 0
    m4.metric("Pending leave", f"{pending_count:,}")

    st.markdown("### 🟣 Distribution — full-circle charts")
    d1, d2 = st.columns(2)
    with d1:
        if employee_data.empty:
            st.info("Register employees to view department distribution.")
        else:
            dept_counts = employee_data["department"].fillna("Unassigned").replace("", "Unassigned").value_counts()
            render_donut_chart("Employees by department", dept_counts, ["#8B5CF6","#A3E635","#38BDF8","#FB7185","#F59E0B","#14B8A6"])
    with d2:
        if leave_data.empty:
            st.info("Leave analytics will appear after requests are recorded.")
        else:
            leave_counts = leave_data["status"].fillna("Unknown").value_counts()
            render_donut_chart("Leave requests by status", leave_counts, ["#A3E635","#F59E0B","#38BDF8","#FB7185","#8B5CF6"])
    d3, d4 = st.columns(2)
    with d3:
        if not attendance_data.empty:
            render_donut_chart("Attendance status distribution", attendance_data["status"].fillna("Unknown").value_counts(), ["#A3E635","#F59E0B","#FB7185","#8B5CF6","#38BDF8"])
        else:
            st.info("Attendance distribution appears after check-in records exist.")
    with d4:
        if not leave_data.empty:
            render_donut_chart("Leave type distribution", leave_data["leave_type"].fillna("Unspecified").value_counts(), ["#38BDF8","#8B5CF6","#F59E0B","#14B8A6","#FB7185"])
        else:
            st.info("Leave type chart appears after leave requests exist.")

    st.markdown("### 📈 Trends — month by month")
    if attendance_data.empty:
        st.info("No attendance history available yet. Record real attendance to build the trend chart.")
    else:
        trend = attendance_data.copy()
        trend["work_date"] = pd.to_datetime(trend["work_date"], errors="coerce")
        trend = trend.dropna(subset=["work_date"])
        if not trend.empty:
            trend["Month"] = trend["work_date"].dt.strftime("%Y-%m")
            monthly = pd.crosstab(trend["Month"], trend["status"])
            st.line_chart(monthly, height=360)
            st.caption("Each coloured line represents an attendance status. Hover over points to inspect values.")
    if not employee_data.empty:
        salaries = pd.to_numeric(employee_data["basic_salary"], errors="coerce").fillna(0).to_numpy(dtype=float)
        x, y = st.columns(2)
        x.metric("Total basic salary", f"{float(np.sum(salaries)):,.2f}")
        y.metric("Average basic salary", f"{float(np.mean(salaries)) if salaries.size else 0.0:,.2f}")
# ============================= AI ASSISTANT ==========================
elif page == "AI Assistant":
    st.title("AI Assistant")
    st.caption("Basic database Q&A for now. Hosted LLM integration will come later.")
    question = st.text_input("Ask an HR data question", placeholder="How many employees are registered?")
    if st.button("Check database", type="primary"):
        q = question.casefold()
        if any(word in q for word in ["employee", "staff", "headcount", "people"]):
            count = int(query_df("SELECT COUNT(*) AS n FROM employees WHERE status='Active'").iloc[0]["n"])
            st.success(f"There are currently {count} active employee record(s).")
        elif any(word in q for word in ["attendance", "present", "check-in", "check in"]):
            count = int(query_df("SELECT COUNT(*) AS n FROM attendance WHERE work_date=?", (TODAY,)).iloc[0]["n"])
            st.success(f"There are {count} attendance record(s) for today.")
        elif any(word in q for word in ["leave", "pending", "approval"]):
            count = int(query_df("SELECT COUNT(*) AS n FROM leave_requests WHERE status='Pending'").iloc[0]["n"])
            st.success(f"There are {count} pending leave request(s).")
        else:
            st.info("Ask about employees, attendance or pending leave requests.")
    st.warning("This is a rule-based helper, not an LLM. Role-based permissions and AI integration are still required.")


# ========================== COMMUNICATIONS & AUDIT ===================
elif page == "Communications & Audit":
    st.title("Communications & Audit")

    st.subheader("Post an announcement")
    with st.form("announcement_form", clear_on_submit=True):
        announcement_title = st.text_input("Title")
        announcement_message = st.text_area("Message")
        publish = st.form_submit_button("Publish announcement")
    if publish:
        if not announcement_title.strip() or not announcement_message.strip():
            st.error("Enter both title and message.")
        else:
            execute("INSERT INTO announcements(title,message,created_at) VALUES(?,?,?)", (announcement_title.strip(), announcement_message.strip(), pkt_now().isoformat(timespec="seconds")))
            log_action("Published announcement", announcement_title.strip())
            st.success("Announcement published.")
            st.rerun()

    st.subheader("Published announcements · open to read")
    admin_news = query_df("SELECT title, message, created_at FROM announcements ORDER BY id DESC")
    if admin_news.empty:
        st.info("No announcements have been published yet.")
    else:
        for idx, item in enumerate(admin_news.itertuples(index=False)):
            with st.container(border=True):
                st.caption("📣 OFFICIAL COMPANY NOTICE")
                with st.expander(f"{str(item.title)}  ·  Click to open", expanded=False):
                    st.markdown(f"### {html.escape(str(item.title))}")
                    st.write(str(item.message))
                    st.caption(f"Published: {str(item.created_at)} · Pakistan Standard Time")

    st.subheader("Recent audit history")
    show_table(query_df("SELECT created_at AS Time, action AS Action, details AS Details FROM audit_logs ORDER BY id DESC LIMIT 100"))


# ================================ FOOTER ============================
st.markdown(
    '<div class="footer-line"><b>HCM LITE</b>'
    '<span>Streamlit + Pandas + NumPy + SQLite · No Plotly</span></div>',
    unsafe_allow_html=True,
)

st.caption("HCM Live Database Mode · No synthetic business records are generated automatically. Before public production deployment, add HTTPS, session protections, backups, and a formal password-reset policy.")
