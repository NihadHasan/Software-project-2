"""
Attendance Management System
-----------------------------
A Flask + SQLite web app to manage student records (manual entry or Excel
bulk upload), take daily attendance, and generate attendance reports
(on-screen + Excel export).

Students are grouped by Department + Batch (instead of a single "class"
field) — attendance and reports are taken per Dept/Batch group.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000 in your browser.
"""

import os
import io
import sqlite3
from datetime import date

import pandas as pd
from flask import (
    Flask, render_template, request, redirect, url_for, flash, send_file
)
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "attendance.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
ALLOWED_EXTENSIONS = {"xlsx", "xls"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = "attendance-management-secret-key-2026"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


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
            batch TEXT NOT NULL,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            status TEXT NOT NULL,
            dept TEXT NOT NULL,
            batch TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            UNIQUE(student_id, date)
        );
        """
    )
    conn.commit()
    conn.close()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@app.route("/")
def dashboard():
    conn = get_db()
    total_students = conn.execute("SELECT COUNT(*) c FROM students").fetchone()["c"]
    groups = conn.execute("SELECT DISTINCT dept, batch FROM students").fetchall()
    today = date.today().isoformat()

    today_present = conn.execute(
        "SELECT COUNT(*) c FROM attendance WHERE date=? AND status='Present'", (today,)
    ).fetchone()["c"]
    today_absent = conn.execute(
        "SELECT COUNT(*) c FROM attendance WHERE date=? AND status='Absent'", (today,)
    ).fetchone()["c"]

    recent = conn.execute(
        """
        SELECT a.date, a.status, a.dept, a.batch, s.name, s.roll
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
    batch_filter = request.args.get("batch", "")
    search = request.args.get("search", "")

    query = "SELECT * FROM students WHERE 1=1"
    params = []
    if dept_filter:
        query += " AND dept=?"
        params.append(dept_filter)
    if batch_filter:
        query += " AND batch=?"
        params.append(batch_filter)
    if search:
        query += " AND (name LIKE ? OR roll LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    query += " ORDER BY dept, batch, roll"

    rows = conn.execute(query, params).fetchall()
    depts = conn.execute("SELECT DISTINCT dept FROM students ORDER BY dept").fetchall()
    batches = conn.execute("SELECT DISTINCT batch FROM students ORDER BY batch").fetchall()
    conn.close()
    return render_template(
        "students.html", students=rows, depts=depts, batches=batches,
        dept_filter=dept_filter, batch_filter=batch_filter, search=search
    )


@app.route("/students/add", methods=["GET", "POST"])
def add_student():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        roll = request.form.get("roll", "").strip()
        dept = request.form.get("dept", "").strip()
        batch = request.form.get("batch", "").strip()
        phone = request.form.get("phone", "").strip()

        if not name or not roll or not dept or not batch:
            flash("Name, Roll, Dept and Batch are required.", "danger")
            return redirect(url_for("add_student"))

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO students (name, roll, dept, batch, phone) VALUES (?, ?, ?, ?, ?)",
                (name, roll, dept, batch, phone),
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
        batch = request.form.get("batch", "").strip()
        phone = request.form.get("phone", "").strip()
        try:
            conn.execute(
                "UPDATE students SET name=?, roll=?, dept=?, batch=?, phone=? WHERE id=?",
                (name, roll, dept, batch, phone, student_id),
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
            df = pd.read_excel(filepath)
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
            elif c in ("batch", "session", "year"):
                col_map[c] = "batch"
            elif c in ("phone", "mobile", "contact"):
                col_map[c] = "phone"
        df = df.rename(columns=col_map)

        if not {"name", "roll", "dept", "batch"}.issubset(df.columns):
            flash("The Excel file must contain Name, Roll, Dept and Batch columns.", "danger")
            return redirect(url_for("upload_excel"))

        conn = get_db()
        added, skipped = 0, 0
        for _, row in df.iterrows():
            name = str(row.get("name", "")).strip()
            roll = str(row.get("roll", "")).strip()
            dept = str(row.get("dept", "")).strip()
            batch = str(row.get("batch", "")).strip()
            phone = (
                str(row.get("phone", "")).strip()
                if "phone" in df.columns and pd.notna(row.get("phone")) else ""
            )

            if not name or not roll or not dept or not batch or name == "nan" or roll == "nan":
                skipped += 1
                continue
            try:
                conn.execute(
                    "INSERT INTO students (name, roll, dept, batch, phone) VALUES (?, ?, ?, ?, ?)",
                    (name, roll, dept, batch, phone),
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
            "Roll": ["101", "102", "103"],
            "Dept": ["CSE", "CSE", "EEE"],
            "Batch": ["51", "51", "52"],
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
    batches = conn.execute("SELECT DISTINCT batch FROM students ORDER BY batch").fetchall()
    selected_dept = request.args.get("dept", "")
    selected_batch = request.args.get("batch", "")
    selected_date = request.args.get("date", date.today().isoformat())

    students_list = []
    existing = {}
    if selected_dept and selected_batch:
        students_list = conn.execute(
            "SELECT * FROM students WHERE dept=? AND batch=? ORDER BY roll",
            (selected_dept, selected_batch),
        ).fetchall()
        rows = conn.execute(
            "SELECT student_id, status FROM attendance WHERE dept=? AND batch=? AND date=?",
            (selected_dept, selected_batch, selected_date),
        ).fetchall()
        existing = {r["student_id"]: r["status"] for r in rows}
    conn.close()
    return render_template(
        "attendance.html", depts=depts, batches=batches, students=students_list,
        selected_dept=selected_dept, selected_batch=selected_batch,
        selected_date=selected_date, existing=existing,
    )


@app.route("/attendance/save", methods=["POST"])
def save_attendance():
    selected_dept = request.form.get("dept")
    selected_batch = request.form.get("batch")
    selected_date = request.form.get("date")
    conn = get_db()
    student_ids = conn.execute(
        "SELECT id FROM students WHERE dept=? AND batch=?", (selected_dept, selected_batch)
    ).fetchall()
    count = 0
    for s in student_ids:
        sid = s["id"]
        status = request.form.get(f"status_{sid}", "Present")
        conn.execute(
            """
            INSERT INTO attendance (student_id, date, status, dept, batch)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, date) DO UPDATE SET status=excluded.status
            """,
            (sid, selected_date, status, selected_dept, selected_batch),
        )
        count += 1
    conn.commit()
    conn.close()
    flash(f"Attendance for {selected_date} has been saved ({count} student(s)).", "success")
    return redirect(url_for(
        "attendance_page", **{"dept": selected_dept, "batch": selected_batch, "date": selected_date}
    ))


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------
@app.route("/reports")
def reports():
    conn = get_db()
    depts = conn.execute("SELECT DISTINCT dept FROM students ORDER BY dept").fetchall()
    batches = conn.execute("SELECT DISTINCT batch FROM students ORDER BY batch").fetchall()
    dept_filter = request.args.get("dept", "")
    batch_filter = request.args.get("batch", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    summary = []
    if dept_filter and batch_filter and from_date and to_date:
        summary_rows = conn.execute(
            """
            SELECT s.id, s.roll, s.name,
                SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) as present,
                SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) as absent,
                COUNT(a.id) as total
            FROM students s
            LEFT JOIN attendance a ON a.student_id = s.id AND a.date BETWEEN ? AND ?
            WHERE s.dept=? AND s.batch=?
            GROUP BY s.id ORDER BY s.roll
            """,
            (from_date, to_date, dept_filter, batch_filter),
        ).fetchall()
        for r in summary_rows:
            pct = round((r["present"] / r["total"]) * 100, 1) if r["total"] else 0.0
            summary.append({**dict(r), "percentage": pct})
    conn.close()
    return render_template(
        "reports.html", depts=depts, batches=batches, summary=summary,
        dept_filter=dept_filter, batch_filter=batch_filter,
        from_date=from_date, to_date=to_date,
    )


@app.route("/reports/export")
def export_report():
    dept_filter = request.args.get("dept", "")
    batch_filter = request.args.get("batch", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    if not (dept_filter and batch_filter and from_date and to_date):
        flash("Please select a department, batch and date range.", "danger")
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
        WHERE s.dept=? AND s.batch=?
        GROUP BY s.id ORDER BY s.roll
        """,
        (from_date, to_date, dept_filter, batch_filter),
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
            }
        )
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Report")
    output.seek(0)
    fname = f"attendance_report_{dept_filter}_{batch_filter}_{from_date}_to_{to_date}.xlsx"
    return send_file(
        output, as_attachment=True, download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
