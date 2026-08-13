from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
from datetime import date
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'student_mgmt_secret_key_2024'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg'}

os.makedirs('static/uploads', exist_ok=True)

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'student_db'
}

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# ===================== LOGIN =====================
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
        user = cur.fetchone()
        db.close()
        if user:
            session['user'] = username
            session['role'] = user['role']
            session['user_id'] = user['id']
            session['full_name'] = user['full_name']
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password!', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ===================== DASHBOARD =====================
@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS total FROM students")
    total = cur.fetchone()['total']
    cur.execute("""SELECT COUNT(*) AS present FROM attendance
                   WHERE status='Present' AND date=%s AND teacher_id=%s""",
                (date.today(), session['user_id']))
    present = cur.fetchone()['present']
    if session['role'] == 'admin':
        cur.execute("SELECT * FROM students ORDER BY id ASC LIMIT 5")
    else:
        cur.execute("""SELECT s.* FROM students s
                      INNER JOIN teacher_students ts ON s.id=ts.student_id
                      WHERE ts.teacher_id=%s ORDER BY s.id ASC LIMIT 5""",
                    (session['user_id'],))
    recent = cur.fetchall()
    db.close()
    return render_template('dashboard.html', total=total, present=present, recent=recent)

# ===================== MANAGE TEACHERS (Admin only) =====================
@app.route('/teachers')
def teachers():
    if 'user' not in session or session['role'] != 'admin':
        flash('Access denied!', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE role='teacher' ORDER BY id ASC")
    teachers = cur.fetchall()
    db.close()
    return render_template('teachers.html', teachers=teachers)

@app.route('/add_teacher', methods=['GET', 'POST'])
def add_teacher():
    if 'user' not in session or session['role'] != 'admin':
        flash('Access denied!', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        full_name = request.form['full_name']
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        cur = db.cursor()
        try:
            cur.execute("INSERT INTO users (full_name, username, password, role) VALUES (%s,%s,%s,'teacher')",
                        (full_name, username, password))
            db.commit()
            db.close()
            flash('Teacher added successfully!', 'success')
            return redirect(url_for('teachers'))
        except:
            db.close()
            flash(' username!', 'danger')
    return render_template('add_teacher.html')

@app.route('/delete_teacher/<int:id>')
def delete_teacher(id):
    if 'user' not in session or session['role'] != 'admin':
        flash('Access denied!', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM attendance WHERE teacher_id=%s", (id,))
    cur.execute("DELETE FROM teacher_students WHERE teacher_id=%s", (id,))
    cur.execute("DELETE FROM users WHERE id=%s AND role='teacher'", (id,))
    db.commit()
    db.close()
    flash('Teacher deleted!', 'danger')
    return redirect(url_for('teachers'))

@app.route('/assign_students/<int:teacher_id>', methods=['GET', 'POST'])
def assign_students(teacher_id):
    if 'user' not in session or session['role'] != 'admin':
        flash('Access denied!', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor(dictionary=True)
    if request.method == 'POST':
        cur.execute("DELETE FROM teacher_students WHERE teacher_id=%s", (teacher_id,))
        selected = request.form.getlist('students')
        for sid in selected:
            cur.execute("INSERT INTO teacher_students (teacher_id, student_id) VALUES (%s,%s)",
                        (teacher_id, sid))
        db.commit()
        flash('Students assigned!', 'success')
        return redirect(url_for('teachers'))
    cur.execute("SELECT * FROM users WHERE id=%s", (teacher_id,))
    teacher = cur.fetchone()
    cur.execute("SELECT * FROM students ORDER BY id ASC")
    all_students = cur.fetchall()
    cur.execute("SELECT student_id FROM teacher_students WHERE teacher_id=%s", (teacher_id,))
    assigned = [r['student_id'] for r in cur.fetchall()]
    db.close()
    return render_template('assign_students.html', teacher=teacher,
                           all_students=all_students, assigned=assigned)

# ===================== ADD STUDENT =====================
@app.route('/add_student', methods=['GET', 'POST'])
def add_student():
    if 'user' not in session or session['role'] != 'admin':
        flash('Only admin can add students!', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        student_id = request.form['student_id']
        name = request.form['name']
        department = request.form['department']
        session_year = request.form['session']
        email = request.form['email']
        phone = request.form['phone']
        photo = None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{student_id}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                photo = filename
        db = get_db()
        cur = db.cursor()
        try:
            cur.execute("INSERT INTO students (id, name, department, session, email, phone, photo) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (student_id, name, department, session_year, email, phone, photo))
            db.commit()
            db.close()
            flash('Student added successfully!', 'success')
            return redirect(url_for('student_list'))
        except:
            db.close()
            flash(' ID !  ID ', 'danger')
    return render_template('add_student.html')

# ===================== STUDENT LIST =====================
@app.route('/students')
def student_list():
    if 'user' not in session:
        return redirect(url_for('login'))
    search = request.args.get('search', '')
    db = get_db()
    cur = db.cursor(dictionary=True)
    if session['role'] == 'admin':
        if search:
            cur.execute("SELECT * FROM students WHERE CAST(id AS CHAR) LIKE %s OR name LIKE %s OR department LIKE %s OR email LIKE %s ORDER BY id ASC",
                        (f'%{search}%', f'%{search}%', f'%{search}%', f'%{search}%'))
        else:
            cur.execute("SELECT * FROM students ORDER BY id ASC")
    else:
        if search:
            cur.execute("""SELECT s.* FROM students s
                          INNER JOIN teacher_students ts ON s.id=ts.student_id
                          WHERE ts.teacher_id=%s AND (CAST(s.id AS CHAR) LIKE %s OR s.name LIKE %s OR s.department LIKE %s)
                          ORDER BY s.id ASC""",
                        (session['user_id'], f'%{search}%', f'%{search}%', f'%{search}%'))
        else:
            cur.execute("""SELECT s.* FROM students s
                          INNER JOIN teacher_students ts ON s.id=ts.student_id
                          WHERE ts.teacher_id=%s ORDER BY s.id ASC""",
                        (session['user_id'],))
    students = cur.fetchall()
    db.close()
    return render_template('student_list.html', students=students, search=search)

# ===================== EDIT STUDENT =====================
@app.route('/edit_student/<int:id>', methods=['GET', 'POST'])
def edit_student(id):
    if 'user' not in session or session['role'] != 'admin':
        flash('Only admin can edit students!', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor(dictionary=True)
    if request.method == 'POST':
        name = request.form['name']
        department = request.form['department']
        session_year = request.form['session']
        email = request.form['email']
        phone = request.form['phone']
        cur.execute("SELECT photo FROM students WHERE id=%s", (id,))
        old = cur.fetchone()
        photo = old['photo']
        if 'photo' in request.files:
            file = request.files['photo']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{id}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                photo = filename
        cur.execute("UPDATE students SET name=%s, department=%s, session=%s, email=%s, phone=%s, photo=%s WHERE id=%s",
                    (name, department, session_year, email, phone, photo, id))
        db.commit()
        db.close()
        flash('Student updated successfully!', 'success')
        return redirect(url_for('student_list'))
    cur.execute("SELECT * FROM students WHERE id=%s", (id,))
    student = cur.fetchone()
    db.close()
    return render_template('edit_student.html', student=student)

# ===================== DELETE STUDENT =====================
@app.route('/delete_student/<int:id>')
def delete_student(id):
    if 'user' not in session or session['role'] != 'admin':
        flash('Only admin can delete students!', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM attendance WHERE student_id=%s", (id,))
    cur.execute("DELETE FROM teacher_students WHERE student_id=%s", (id,))
    cur.execute("DELETE FROM students WHERE id=%s", (id,))
    db.commit()
    db.close()
    flash('Student deleted!', 'danger')
    return redirect(url_for('student_list'))

# ===================== ID CARD =====================
@app.route('/id_card/<int:id>')
def id_card(id):
    if 'user' not in session:
        return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT * FROM students WHERE id=%s", (id,))
    student = cur.fetchone()
    db.close()
    return render_template('id_card.html', student=student)

# ===================== ATTENDANCE =====================
@app.route('/attendance', methods=['GET', 'POST'])
def attendance():
    if 'user' not in session:
        return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor(dictionary=True)

    selected_date = request.args.get('att_date', str(date.today()))
    selected_dept = request.args.get('department', '')
    selected_session = request.args.get('session', '')

    cur.execute("SELECT DISTINCT department FROM students ORDER BY department")
    departments = cur.fetchall()
    cur.execute("SELECT DISTINCT session FROM students WHERE session IS NOT NULL ORDER BY session DESC")
    sessions = cur.fetchall()

    if request.method == 'POST':
        att_date = request.form['att_date']
        dept = request.form.get('department', '')
        sess = request.form.get('session', '')

        if session['role'] == 'admin':
            query = "SELECT id FROM students WHERE 1=1"
            params = []
            if dept:
                query += " AND department=%s"
                params.append(dept)
            if sess:
                query += " AND session=%s"
                params.append(sess)
        else:
            query = """SELECT s.id FROM students s
                      INNER JOIN teacher_students ts ON s.id=ts.student_id
                      WHERE ts.teacher_id=%s"""
            params = [session['user_id']]
            if dept:
                query += " AND s.department=%s"
                params.append(dept)
            if sess:
                query += " AND s.session=%s"
                params.append(sess)

        cur.execute(query, params)
        filtered_students = cur.fetchall()

        for s in filtered_students:
            sid = s['id']
            status = request.form.get(f'status_{sid}', 'Absent')
            cur.execute("""SELECT id FROM attendance
                          WHERE student_id=%s AND date=%s AND teacher_id=%s""",
                        (sid, att_date, session['user_id']))
            existing = cur.fetchone()
            if existing:
                cur.execute("""UPDATE attendance SET status=%s
                              WHERE student_id=%s AND date=%s AND teacher_id=%s""",
                            (status, sid, att_date, session['user_id']))
            else:
                cur.execute("""INSERT INTO attendance (student_id, date, status, teacher_id)
                              VALUES (%s,%s,%s,%s)""",
                            (sid, att_date, status, session['user_id']))
        db.commit()
        flash('Attendance saved!', 'success')
        selected_date = att_date
        selected_dept = dept
        selected_session = sess

    if session['role'] == 'admin':
        query = """SELECT s.id, s.name, s.department, s.session, a.status
                   FROM students s
                   LEFT JOIN attendance a ON s.id=a.student_id
                   AND a.date=%s AND a.teacher_id=%s
                   WHERE 1=1"""
        params = [selected_date, session['user_id']]
    else:
        query = """SELECT s.id, s.name, s.department, s.session, a.status
                   FROM students s
                   INNER JOIN teacher_students ts ON s.id=ts.student_id
                   LEFT JOIN attendance a ON s.id=a.student_id
                   AND a.date=%s AND a.teacher_id=%s
                   WHERE ts.teacher_id=%s"""
        params = [selected_date, session['user_id'], session['user_id']]

    if selected_dept:
        query += " AND s.department=%s"
        params.append(selected_dept)
    if selected_session:
        query += " AND s.session=%s"
        params.append(selected_session)
    query += " ORDER BY s.id ASC"
    cur.execute(query, params)
    students = cur.fetchall()
    db.close()

    return render_template('attendance.html',
                           students=students,
                           selected_date=selected_date,
                           selected_dept=selected_dept,
                           selected_session=selected_session,
                           departments=departments,
                           sessions=sessions)

# ===================== ATTENDANCE REPORT =====================
@app.route('/attendance_report')
def attendance_report():
    if 'user' not in session:
        return redirect(url_for('login'))

    selected_date = request.args.get('report_date', str(date.today()))
    selected_dept = request.args.get('department', '')
    selected_session = request.args.get('session', '')

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("SELECT DISTINCT department FROM students ORDER BY department")
    departments = cur.fetchall()
    cur.execute("SELECT DISTINCT session FROM students WHERE session IS NOT NULL ORDER BY session DESC")
    sessions = cur.fetchall()

    if session['role'] == 'admin':
        query1 = """SELECT s.id, s.name, s.department, s.session,
                   COALESCE(a.status, 'Absent') AS status
                   FROM students s
                   LEFT JOIN attendance a ON s.id=a.student_id
                   AND a.date=%s AND a.teacher_id=%s
                   WHERE 1=1"""
        params1 = [selected_date, session['user_id']]
        query2 = """SELECT s.id, s.name, s.department, s.session,
                   SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) AS present_days,
                   COUNT(a.id) AS total_days
                   FROM students s
                   LEFT JOIN attendance a ON s.id=a.student_id AND a.teacher_id=%s
                   WHERE 1=1"""
        params2 = [session['user_id']]
    else:
        query1 = """SELECT s.id, s.name, s.department, s.session,
                   COALESCE(a.status, 'Absent') AS status
                   FROM students s
                   INNER JOIN teacher_students ts ON s.id=ts.student_id
                   LEFT JOIN attendance a ON s.id=a.student_id
                   AND a.date=%s AND a.teacher_id=%s
                   WHERE ts.teacher_id=%s"""
        params1 = [selected_date, session['user_id'], session['user_id']]
        query2 = """SELECT s.id, s.name, s.department, s.session,
                   SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) AS present_days,
                   COUNT(a.id) AS total_days
                   FROM students s
                   INNER JOIN teacher_students ts ON s.id=ts.student_id
                   LEFT JOIN attendance a ON s.id=a.student_id AND a.teacher_id=%s
                   WHERE ts.teacher_id=%s"""
        params2 = [session['user_id'], session['user_id']]

    if selected_dept:
        query1 += " AND s.department=%s"
        params1.append(selected_dept)
        query2 += " AND s.department=%s"
        params2.append(selected_dept)
    if selected_session:
        query1 += " AND s.session=%s"
        params1.append(selected_session)
        query2 += " AND s.session=%s"
        params2.append(selected_session)

    query1 += " ORDER BY s.id ASC"
    query2 += " GROUP BY s.id ORDER BY s.id ASC"

    cur.execute(query1, params1)
    report = cur.fetchall()
    cur.execute(query2, params2)
    summary = cur.fetchall()

    cur.execute("""SELECT DISTINCT date FROM attendance
                   WHERE teacher_id=%s ORDER BY date DESC""",
                (session['user_id'],))
    dates = cur.fetchall()
    db.close()

    return render_template('attendance_report.html',
                           report=report, summary=summary,
                           selected_date=selected_date, dates=dates,
                           departments=departments, sessions=sessions,
                           selected_dept=selected_dept, selected_session=selected_session)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)