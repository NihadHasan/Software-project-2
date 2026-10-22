"""
Attendance Management System
-----------------------------
A Flask + SQLite web app to manage student records (manual entry or Excel
bulk upload), take daily attendance, and generate attendance reports
(on-screen + Excel export).

Students are grouped by Department + Session (instead of a single "class"
field) — attendance and reports are taken per Dept/Session group.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000 in your browser.
"""

import os
import io
import time
import secrets
import sqlite3
from datetime import date, timedelta
from functools import wraps

import pandas as pd
from flask import (
    Flask, render_template, request, redirect, url_for, flash, send_file,
    session, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "attendance.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
ALLOWED_EXTENSIONS = {"xlsx", "xls"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)



def load_secret_key():
    """Use SECRET_KEY env var, otherwise generate one once and keep it in secret.key."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    key_path = os.path.join(BASE_DIR, "secret.key")
    if os.path.exists(key_path):
        with open(key_path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(key_path, "w") as f:
        f.write(key)
    return key


app = Flask(__name__)
app.secret_key = load_secret_key()
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

# ---- Login details: change these (or set ADMIN_USERNAME / ADMIN_PASSWORD env vars) ----
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
MIN_PASSWORD_LEN = 6


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            roll TEXT NOT NULL UNIQUE,
            dept TEXT NOT NULL,
            session TEXT NOT NULL,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            status TEXT NOT NULL,
            dept TEXT NOT NULL,
            session TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            UNIQUE(student_id, date)
        );

        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Migration: older databases used a "batch" column -> rename it to "session"
    for table in ("students", "attendance"):
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if "batch" in cols and "session" not in cols:
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN batch TO session")
    conn.commit()
    conn.close()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# --------------------------------------------------------------------------
# Login: one admin (set in this file) + teachers added by the admin
# --------------------------------------------------------------------------
PUBLIC_ENDPOINTS = {"login", "static"}
# Pages a logged-in teacher may use (everything else is admin-only).
TEACHER_ENDPOINTS = {"attendance_page", "save_attendance", "reports", "export_report", "logout"}

# Brute-force protection: ip -> (fail_count, locked_until)
MAX_FAILS = 5
LOCK_SECONDS = 300
_login_fails = {}


def norm_name(value):
    """Teacher login key: trimmed, single-spaced, case-insensitive."""
    return " ".join(value.split()).lower()


def is_admin():
    return session.get("role") == "admin"


def home_url():
    return url_for("dashboard") if is_admin() else url_for("attendance_page")


def safe_next(target):
    """Only allow redirects to local paths (prevents open-redirect)."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return home_url()


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


@app.context_processor
def inject_helpers():
    from markupsafe import Markup
    return {
        "csrf_input": lambda: Markup(
            f'<input type="hidden" name="_csrf" value="{csrf_token()}">'
        ),
        "logged_in": bool(session.get("role")),
        "is_admin": is_admin(),
        "user_name": session.get("name", ""),
    }


@app.before_request
def enforce_login():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Invalid or missing CSRF token. Please reload the page and try again.")

    endpoint = request.endpoint
    if endpoint in PUBLIC_ENDPOINTS or endpoint is None:
        return None

    role = session.get("role")
    if not role:
        return redirect(url_for("login", next=request.full_path.rstrip("?")))

    if role == "teacher":
        # make sure the teacher account still exists (admin may have deleted it)
        conn = get_db()
        exists = conn.execute(
            "SELECT 1 FROM teachers WHERE id=?", (session.get("teacher_id"),)
        ).fetchone()
        conn.close()
        if not exists:
            session.clear()
            flash("Your account is no longer active.", "danger")
            return redirect(url_for("login"))
        if endpoint not in TEACHER_ENDPOINTS:
            flash("Teacher accounts can only take attendance and view/download reports.", "danger")
            return redirect(url_for("attendance_page"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("role"):
        return redirect(home_url())

    next_url = request.values.get("next", "")
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        fails, locked_until = _login_fails.get(ip, (0, 0))
        if locked_until > time.time():
            wait = int(locked_until - time.time()) // 60 + 1
            flash(f"Too many failed attempts. Try again in about {wait} minute(s).", "danger")
            return render_template("login.html", next=next_url, username=""), 429

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        who = None  # (role, name, teacher_id)
        ok_user = secrets.compare_digest(username.encode(), ADMIN_USERNAME.encode())
        ok_pass = secrets.compare_digest(password.encode(), ADMIN_PASSWORD.encode())
        if ok_user and ok_pass:
            who = ("admin", "Admin", None)
        else:
            conn = get_db()
            t = conn.execute(
                "SELECT * FROM teachers WHERE username=?", (norm_name(username),)
            ).fetchone()
            conn.close()
            if t and check_password_hash(t["password_hash"], password):
                who = ("teacher", t["name"], t["id"])

        if who:
            _login_fails.pop(ip, None)
            csrf = session.get("_csrf")
            session.clear()
            session["_csrf"] = csrf or secrets.token_hex(16)
            session.permanent = True
            session["role"], session["name"], session["teacher_id"] = who
            return redirect(safe_next(next_url))

        fails += 1
        _login_fails[ip] = (fails, time.time() + LOCK_SECONDS if fails >= MAX_FAILS else 0)
        flash("Incorrect username or password.", "danger")
        return render_template("login.html", next=next_url, username=username), 401

    return render_template("login.html", next=next_url, username="")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Teachers (admin only): add / reset password / delete
# --------------------------------------------------------------------------
@app.route("/teachers", methods=["GET", "POST"])
def teachers():
    conn = get_db()
    if request.method == "POST":
        name = " ".join(request.form.get("name", "").split())
        password = request.form.get("password", "")
        key = norm_name(name)
        error = None
        if not name or not password:
            error = "Teacher name and password are required."
        elif key == norm_name(ADMIN_USERNAME):
            error = "That name is reserved. Choose a different name."
        elif len(password) < MIN_PASSWORD_LEN:
            error = f"Password must be at least {MIN_PASSWORD_LEN} characters."
        if error:
            flash(error, "danger")
        else:
            try:
                # the (normalized) name doubles as the login name
                conn.execute(
                    "INSERT INTO teachers (name, username, password_hash) VALUES (?, ?, ?)",
                    (name, key, generate_password_hash(password)),
                )
                conn.commit()
                flash(f'Teacher "{name}" added. They log in with this name and the password.', "success")
            except sqlite3.IntegrityError:
                flash(f'A teacher named "{name}" already exists.', "danger")
        conn.close()
        return redirect(url_for("teachers"))

    rows = conn.execute("SELECT * FROM teachers ORDER BY name").fetchall()
    conn.close()
    return render_template("teachers.html", teachers=rows)


@app.route("/teachers/<int:teacher_id>/reset-password", methods=["POST"])
def reset_teacher_password(teacher_id):
    password = request.form.get("password", "")
    if len(password) < MIN_PASSWORD_LEN:
        flash(f"Password must be at least {MIN_PASSWORD_LEN} characters.", "danger")
        return redirect(url_for("teachers"))
    conn = get_db()
    conn.execute(
        "UPDATE teachers SET password_hash=? WHERE id=?",
        (generate_password_hash(password), teacher_id),
    )
    conn.commit()
    conn.close()
    flash("Password updated.", "success")
    return redirect(url_for("teachers"))


@app.route("/teachers/<int:teacher_id>/delete", methods=["POST"])
def delete_teacher(teacher_id):
    conn = get_db()
    conn.execute("DELETE FROM teachers WHERE id=?", (teacher_id,))
    conn.commit()
    conn.close()
    flash("Teacher removed.", "info")
    return redirect(url_for("teachers"))


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@app.route("/")
def dashboard():
    conn = get_db()
    total_students = conn.execute("SELECT COUNT(*) c FROM students").fetchone()["c"]
    groups = conn.execute("SELECT DISTINCT dept, session FROM students").fetchall()
    today = date.today().isoformat()

    today_present = conn.execute(
        "SELECT COUNT(*) c FROM attendance WHERE date=? AND status='Present'", (today,)
    ).fetchone()["c"]
    today_absent = conn.execute(
        "SELECT COUNT(*) c FROM attendance WHERE date=? AND status='Absent'", (today,)
    ).fetchone()["c"]

    recent = conn.execute(
        """
        SELECT a.date, a.status, a.dept, a.session, s.name, s.roll
        FROM attendance a JOIN students s ON a.student_id = s.id
        ORDER BY a.created_at DESC LIMIT 8
        """
    ).fetchall()
    conn.close()
    return render_template(
        "index.html",
        total_students=total_students,
        total_groups=len(groups),
        today=today,
        today_present=today_present,
        today_absent=today_absent,
        recent=recent,
    )


# --------------------------------------------------------------------------
# Students - list / add / edit / delete
# --------------------------------------------------------------------------
@app.route("/students")
def students():
    conn = get_db()
    dept_filter = request.args.get("dept", "")
    session_filter = request.args.get("session", "")
    search = request.args.get("search", "")

    query = "SELECT * FROM students WHERE 1=1"
    params = []
    if dept_filter:
        query += " AND dept=?"
        params.append(dept_filter)
    if session_filter:
        query += " AND session=?"
        params.append(session_filter)
    if search:
        query += " AND (name LIKE ? OR roll LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    query += " ORDER BY dept, session, roll"

    rows = conn.execute(query, params).fetchall()
    depts = conn.execute("SELECT DISTINCT dept FROM students ORDER BY dept").fetchall()
    session_list = conn.execute("SELECT DISTINCT session FROM students ORDER BY session").fetchall()
    conn.close()
    return render_template(
        "students.html", students=rows, depts=depts, session_list=session_list,
        dept_filter=dept_filter, session_filter=session_filter, search=search
    )


@app.route("/students/add", methods=["GET", "POST"])
def add_student():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        roll = request.form.get("roll", "").strip()
        dept = request.form.get("dept", "").strip()
        sess = request.form.get("session", "").strip()
        phone = request.form.get("phone", "").strip()

        if not name or not roll or not dept or not sess:
            flash("Name, Roll, Dept and Session are required.", "danger")
            return redirect(url_for("add_student"))

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO students (name, roll, dept, session, phone) VALUES (?, ?, ?, ?, ?)",
                (name, roll, dept, sess, phone),
            )
            conn.commit()
            flash(f"{name} was added successfully.", "success")
        except sqlite3.IntegrityError:
            flash(f'Roll "{roll}" already exists.', "danger")
        finally:
            conn.close()
        return redirect(url_for("students"))

    return render_template("add_student.html")


@app.route("/students/edit/<int:student_id>", methods=["GET", "POST"])
def edit_student(student_id):
    conn = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        roll = request.form.get("roll", "").strip()
        dept = request.form.get("dept", "").strip()
        sess = request.form.get("session", "").strip()
        phone = request.form.get("phone", "").strip()
        try:
            conn.execute(
                "UPDATE students SET name=?, roll=?, dept=?, session=?, phone=? WHERE id=?",
                (name, roll, dept, sess, phone, student_id),
            )
            conn.commit()
            flash("Student details updated.", "success")
        except sqlite3.IntegrityError:
            flash(f'Roll "{roll}" already exists.', "danger")
        conn.close()
        return redirect(url_for("students"))

    student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    conn.close()
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("students"))
    return render_template("edit_student.html", student=student)


@app.route("/students/delete/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    conn = get_db()
    conn.execute("DELETE FROM students WHERE id=?", (student_id,))
    conn.commit()
    conn.close()
    flash("Student deleted.", "info")
    return redirect(url_for("students"))


# --------------------------------------------------------------------------
# Excel bulk upload
# --------------------------------------------------------------------------
@app.route("/students/upload", methods=["GET", "POST"])
def upload_excel():
    if request.method == "POST":
        if "file" not in request.files or request.files["file"].filename == "":
            flash("No file selected.", "danger")
            return redirect(url_for("upload_excel"))

        file = request.files["file"]
        if not allowed_file(file.filename):
            flash("Only .xlsx or .xls files can be uploaded.", "danger")
            return redirect(url_for("upload_excel"))

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        try:
            df = pd.read_excel(filepath, dtype=str)
        except Exception as e:
            flash(f"There was a problem reading the file: {e}", "danger")
            return redirect(url_for("upload_excel"))

        df.columns = [str(c).strip().lower() for c in df.columns]
        col_map = {}
        for c in df.columns:
            if c in ("name", "student name", "student_name"):
                col_map[c] = "name"
            elif c in ("roll", "roll no", "roll_no", "rollno", "id"):
                col_map[c] = "roll"
            elif c in ("dept", "department"):
                col_map[c] = "dept"
            elif c in ("session", "batch", "year"):
                col_map[c] = "session"
            elif c in ("phone", "mobile", "contact"):
                col_map[c] = "phone"
        df = df.rename(columns=col_map)

        if not {"name", "roll", "dept", "session"}.issubset(df.columns):
            flash("The Excel file must contain Name, Roll, Dept and Session columns.", "danger")
            return redirect(url_for("upload_excel"))

        conn = get_db()
        added, skipped = 0, 0
        for _, row in df.iterrows():
            name = str(row.get("name", "")).strip()
            roll = str(row.get("roll", "")).strip()
            dept = str(row.get("dept", "")).strip()
            sess = str(row.get("session", "")).strip()
            phone = (
                str(row.get("phone", "")).strip()
                if "phone" in df.columns and pd.notna(row.get("phone")) else ""
            )

            if not name or not roll or not dept or not sess or name == "nan" or roll == "nan":
                skipped += 1
                continue
            try:
                conn.execute(
                    "INSERT INTO students (name, roll, dept, session, phone) VALUES (?, ?, ?, ?, ?)",
                    (name, roll, dept, sess, phone),
                )
                added += 1
            except sqlite3.IntegrityError:
                skipped += 1
        conn.commit()
        conn.close()
        flash(f"{added} student(s) added. {skipped} row(s) skipped (duplicate/incomplete).", "success")
        return redirect(url_for("students"))

    return render_template("upload.html")


@app.route("/students/sample-excel")
def sample_excel():
    df = pd.DataFrame(
        {
            "Name": ["Rahim Uddin", "Karim Hossain", "Fatema Begum"],
            "Roll": ["230139", "230140", "230141"],
            "Dept": ["CSE", "CSE", "EEE"],
            "Session": ["2022-23", "2022-23", "2023-24"],
            "Phone": ["01700000001", "01700000002", "01700000003"],
        }
    )
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Students")
    output.seek(0)
    return send_file(
        output, as_attachment=True, download_name="sample_students.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# --------------------------------------------------------------------------
# Attendance taking
# --------------------------------------------------------------------------
@app.route("/attendance")
def attendance_page():
    conn = get_db()
    depts = conn.execute("SELECT DISTINCT dept FROM students ORDER BY dept").fetchall()
    session_list = conn.execute("SELECT DISTINCT session FROM students ORDER BY session").fetchall()
    selected_dept = request.args.get("dept", "")
    selected_session = request.args.get("session", "")
    selected_date = request.args.get("date", date.today().isoformat())

    students_list = []
    existing = {}
    if selected_dept and selected_session:
        students_list = conn.execute(
            "SELECT * FROM students WHERE dept=? AND session=? ORDER BY roll",
            (selected_dept, selected_session),
        ).fetchall()
        rows = conn.execute(
            "SELECT student_id, status FROM attendance WHERE dept=? AND session=? AND date=?",
            (selected_dept, selected_session, selected_date),
        ).fetchall()
        existing = {r["student_id"]: r["status"] for r in rows}
    conn.close()
    return render_template(
        "attendance.html", depts=depts, session_list=session_list, students=students_list,
        selected_dept=selected_dept, selected_session=selected_session,
        selected_date=selected_date, existing=existing,
    )


@app.route("/attendance/save", methods=["POST"])
def save_attendance():
    selected_dept = request.form.get("dept")
    selected_session = request.form.get("session")
    selected_date = request.form.get("date")
    conn = get_db()
    student_ids = conn.execute(
        "SELECT id FROM students WHERE dept=? AND session=?", (selected_dept, selected_session)
    ).fetchall()
    count = 0
    for s in student_ids:
        sid = s["id"]
        status = request.form.get(f"status_{sid}", "Present")
        conn.execute(
            """
            INSERT INTO attendance (student_id, date, status, dept, session)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, date) DO UPDATE SET status=excluded.status
            """,
            (sid, selected_date, status, selected_dept, selected_session),
        )
        count += 1
    conn.commit()
    conn.close()
    flash(f"Attendance for {selected_date} has been saved ({count} student(s)).", "success")
    return redirect(url_for(
        "attendance_page", **{"dept": selected_dept, "session": selected_session, "date": selected_date}
    ))


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------
# Attendance % -> marks. Each row is (minimum percentage, marks).
MARKS_SCALE = [(90, 10), (80, 9), (75, 8), (70, 7), (60, 6)]


def attendance_marks(pct):
    """90-100 -> 10, 80-89 -> 9, 75-79 -> 8, 70-74 -> 7, 60-69 -> 6, below 60 -> 0."""
    for minimum, marks in MARKS_SCALE:
        if pct >= minimum:
            return marks
    return 0


@app.route("/reports")
def reports():
    conn = get_db()
    depts = conn.execute("SELECT DISTINCT dept FROM students ORDER BY dept").fetchall()
    session_list = conn.execute("SELECT DISTINCT session FROM students ORDER BY session").fetchall()
    dept_filter = request.args.get("dept", "")
    session_filter = request.args.get("session", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    summary = []
    if dept_filter and session_filter and from_date and to_date:
        summary_rows = conn.execute(
            """
            SELECT s.id, s.roll, s.name,
                SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) as present,
                SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) as absent,
                COUNT(a.id) as total
            FROM students s
            LEFT JOIN attendance a ON a.student_id = s.id AND a.date BETWEEN ? AND ?
            WHERE s.dept=? AND s.session=?
            GROUP BY s.id ORDER BY s.roll
            """,
            (from_date, to_date, dept_filter, session_filter),
        ).fetchall()
        for r in summary_rows:
            pct = round((r["present"] / r["total"]) * 100, 1) if r["total"] else 0.0
            summary.append({**dict(r), "percentage": pct, "marks": attendance_marks(pct)})
    conn.close()
    return render_template(
        "reports.html", depts=depts, session_list=session_list, summary=summary,
        dept_filter=dept_filter, session_filter=session_filter,
        from_date=from_date, to_date=to_date,
    )


@app.route("/reports/export")
def export_report():
    dept_filter = request.args.get("dept", "")
    session_filter = request.args.get("session", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    if not (dept_filter and session_filter and from_date and to_date):
        flash("Please select a department, session and date range.", "danger")
        return redirect(url_for("reports"))

    conn = get_db()
    summary_rows = conn.execute(
        """
        SELECT s.roll, s.name,
            SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) as present,
            SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) as absent,
            COUNT(a.id) as total_marked
        FROM students s
        LEFT JOIN attendance a ON a.student_id = s.id AND a.date BETWEEN ? AND ?
        WHERE s.dept=? AND s.session=?
        GROUP BY s.id ORDER BY s.roll
        """,
        (from_date, to_date, dept_filter, session_filter),
    ).fetchall()
    conn.close()

    data = []
    for r in summary_rows:
        total = r["total_marked"]
        pct = round((r["present"] / total) * 100, 1) if total else 0.0
        data.append(
            {
                "Roll": r["roll"], "Name": r["name"],
                "Present": r["present"], "Absent": r["absent"],
                "Total Marked Days": total, "Attendance %": pct,
                "Marks": attendance_marks(pct),
            }
        )
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Report")
    output.seek(0)
    fname = f"attendance_report_{dept_filter}_{session_filter}_{from_date}_to_{to_date}.xlsx"
    return send_file(
        output, as_attachment=True, download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


init_db()

def get_lan_ip():
    """Best-effort guess of this computer's address on the local network."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))  # no data is sent; just picks the active interface
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    # The app is reachable from other devices on the same network (HOST=0.0.0.0).
    # Debug mode stays OFF by default (turn on only on your own PC: FLASK_DEBUG=1).
    # To limit access to this computer only:  HOST=127.0.0.1 python app.py
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    if host == "0.0.0.0":
        print("\n  Open on this computer : http://127.0.0.1:%d" % port)
        print("  Open from other devices (same Wi-Fi/network): http://%s:%d\n" % (get_lan_ip(), port))
    app.run(
        debug=os.environ.get("FLASK_DEBUG") == "1",
        host=host,
        port=port,
        threaded=True,
    )
