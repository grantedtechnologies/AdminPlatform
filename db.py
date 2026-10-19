import os

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor

load_dotenv()


def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST") or os.getenv("LOCAL_DATABASE_HOST") or "localhost",
        user=os.getenv("DB_USER") or os.getenv("MYSQL_USER"),
        password=os.getenv("DB_PASSWORD") or os.getenv("LOCAL_DATABASE_PASSWORD") or "",
        database=os.getenv("DB_NAME") or os.getenv("LOCAL_DATABASE_NAME"),
        port=int(os.getenv("DB_PORT") or os.getenv("MYSQL_PORT") or "3306"),
        cursorclass=DictCursor,
        charset="utf8mb4",
        autocommit=False,
    )


def teacher_filter(col="teacherid", pilotid=None):
    if pilotid is None:
        return (f"{col} NOT IN (SELECT teacherid FROM subscriptions WHERE pilotid IS NOT NULL)", ())
    return (f"{col} IN (SELECT teacherid FROM subscriptions WHERE pilotid = %s)", (pilotid,))


def event_filter(col="pilotid", pilotid=None):
    if pilotid is None:
        return (f"{col} IS NULL", ())
    return (f"{col} = %s", (pilotid,))


def _query(sql, params=(), one=False):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if one:
                return cur.fetchone()
            return list(cur.fetchall())
    finally:
        conn.close()


def _execute(sql, params=()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def feature_usage_daily(pilotid=None):
    fragment, params = teacher_filter("teacherid", pilotid)
    sql = f"""
        SELECT DATE(createdAt) AS day, COUNT(*) AS total,
               SUM(phonics IS NOT NULL) AS phonics,
               SUM(vocabListid IS NOT NULL) AS vocab,
               SUM(DOKLevel <=> 'custom') AS custom_dok
        FROM worksheets WHERE {fragment} GROUP BY day ORDER BY day
    """
    return _query(sql, params)


def dok_distribution(pilotid=None):
    fragment, params = teacher_filter("teacherid", pilotid)
    sql = f"""
        SELECT DOKLevel, COUNT(*) AS worksheets FROM worksheets
        WHERE DOKLevel IS NOT NULL AND {fragment} GROUP BY DOKLevel ORDER BY DOKLevel
    """
    return _query(sql, params)


def vocab_lists_daily(pilotid=None):
    fragment, params = teacher_filter("c.teacherid", pilotid)
    sql = f"""
        SELECT DATE(v.createdAt) AS day, COUNT(*) AS lists_created
        FROM vocabList v
        JOIN Students s ON s.studentid = v.studentid
        JOIN class c ON c.classid = s.classid
        WHERE {fragment} GROUP BY day ORDER BY day
    """
    return _query(sql, params)


def feature_adoption(pilotid=None):
    fragment, params = teacher_filter("teacherid", pilotid)
    sql = f"""
        SELECT COUNT(DISTINCT teacherid) AS total_teachers,
          COUNT(DISTINCT CASE WHEN phonics IS NOT NULL THEN teacherid END) AS used_phonics,
          COUNT(DISTINCT CASE WHEN vocabListid IS NOT NULL THEN teacherid END) AS used_vocab,
          COUNT(DISTINCT CASE WHEN DOKLevel = 'custom' THEN teacherid END) AS used_custom_dok
        FROM worksheets WHERE {fragment}
    """
    return _query(sql, params, one=True)


def signups_daily(pilotid=None):
    fragment, params = teacher_filter("teacherid", pilotid)
    sql = f"""
        SELECT DATE(createdAt) AS day, COUNT(*) AS signups FROM Teachers
        WHERE {fragment} GROUP BY day ORDER BY day
    """
    return _query(sql, params)


def active_teachers_daily(pilotid=None):
    fragment, params = teacher_filter("teacherid", pilotid)
    sql = f"""
        SELECT DATE(startedAt) AS day, COUNT(DISTINCT teacherid) AS teachers FROM sessions
        WHERE {fragment} GROUP BY day ORDER BY day
    """
    return _query(sql, params)


def active_hours_daily(pilotid=None):
    fragment, params = teacher_filter("teacherid", pilotid)
    sql = f"""
        SELECT DATE(startedAt) AS day,
               ROUND(SUM(TIMESTAMPDIFF(MINUTE, startedAt, endedAt)) / 60, 1) AS hours
        FROM sessions WHERE endedAt IS NOT NULL AND {fragment} GROUP BY day ORDER BY day
    """
    return _query(sql, params)


def edit_rate(pilotid=None):
    fragment, params = teacher_filter("w.teacherid", pilotid)
    sql = f"""
        SELECT SUM(was_edited) AS edited, COUNT(*) - SUM(was_edited) AS not_edited
        FROM (SELECT v.sheetid, MAX(v.source = 'edit') AS was_edited
              FROM versions v JOIN worksheets w ON w.sheetid = v.sheetid
              WHERE {fragment} GROUP BY v.sheetid) AS per_sheet
    """
    return _query(sql, params, one=True)


def funnel(pilotid=None):
    fragment, params = event_filter("pilotid", pilotid)
    sql = f"""
        SELECT eventType, COUNT(DISTINCT teacherid) AS teachers FROM Events
        WHERE eventType IN ('sign_up','add_class','add_student','generate','download')
          AND success = 1 AND {fragment}
        GROUP BY eventType
        ORDER BY FIELD(eventType,'sign_up','add_class','add_student','generate','download')
    """
    return _query(sql, params)


def return_days(pilotid=None):
    fragment, params = teacher_filter("teacherid", pilotid)
    sql = f"""
        SELECT teacherid, COUNT(DISTINCT DATE(startedAt)) AS days_active,
               MIN(DATE(startedAt)) AS first_day, MAX(DATE(startedAt)) AS last_day
        FROM sessions WHERE {fragment} GROUP BY teacherid
    """
    return _query(sql, params)


def audit_log(limit, offset):
    sql = """
        SELECT e.eventTime, e.eventType, e.success, e.statusCode, e.errorMessage,
               e.ip, e.entity, e.entityid, e.pilotid, t.fname, t.lname
        FROM Events e LEFT JOIN Teachers t ON t.teacherid = e.teacherid
        ORDER BY e.eventTime DESC LIMIT %s OFFSET %s
    """
    return _query(sql, (limit, offset))


def failure_summary():
    sql = """
        SELECT eventType, COUNT(*) AS failures FROM Events
        WHERE success = 0 GROUP BY eventType ORDER BY failures DESC
    """
    return _query(sql)


def all_pilots():
    sql = """
        SELECT p.pilotid, p.name, p.school, p.code, p.startTime, p.endTime,
          CASE WHEN NOW() < p.startTime THEN 'upcoming'
               WHEN NOW() > p.endTime THEN 'ended' ELSE 'active' END AS status,
          COUNT(s.teacherid) AS teachers
        FROM Pilot p LEFT JOIN subscriptions s ON s.pilotid = p.pilotid
        GROUP BY p.pilotid ORDER BY p.startTime
    """
    return _query(sql)


def find_district_by_name(name):
    sql = "SELECT districtid, name FROM districts WHERE name = %s LIMIT 1"
    return _query(sql, (name,), one=True)


def create_pilot(name, school, code, start, end, districtid):
    sql = """
        INSERT INTO Pilot (districtid, name, school, code, startTime, endTime, createdAt)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
    """
    return _execute(sql, (districtid, name, school, code, start, end))


def get_pilot(pilotid):
    sql = """
        SELECT p.pilotid, p.name, p.school, p.code, p.startTime, p.endTime,
               p.districtid, d.name AS district
        FROM Pilot p
        LEFT JOIN districts d ON d.districtid = p.districtid
        WHERE p.pilotid = %s
    """
    return _query(sql, (pilotid,), one=True)


def update_pilot(pilotid, name, school, code, start, end, districtid):
    sql = """
        UPDATE Pilot SET districtid = %s, name = %s, school = %s, code = %s, startTime = %s, endTime = %s
        WHERE pilotid = %s
    """
    _execute(sql, (districtid, name, school, code, start, end, pilotid))


def pilot_teacher_types(pilotid=None):
    fragment, params = teacher_filter("c.teacherid", pilotid)
    sql = f"""
        SELECT c.gradeLevel, c.subject, COUNT(DISTINCT c.teacherid) AS teachers
        FROM class c WHERE {fragment} GROUP BY c.gradeLevel, c.subject ORDER BY c.gradeLevel
    """
    return _query(sql, params)


def create_survey_with_questions(title, description, baseline, pilotid, questions):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO Survey (surveyDate, title, description, baseline, pilotid)
                VALUES (NOW(), %s, %s, %s, %s)
                """,
                (title, description, baseline, pilotid),
            )
            surveyid = cur.lastrowid
            for question in questions:
                cur.execute(
                    "INSERT INTO Question (surveyid, content, questionType) VALUES (%s, %s, %s)",
                    (surveyid, question["content"], question["questionType"]),
                )
        conn.commit()
        return surveyid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rating_by_source(pilotid=None):
    fragment, params = teacher_filter("w.teacherid", pilotid)
    sql = f"""
        SELECT v.source, ROUND(AVG(v.rating), 2) AS avg_rating, COUNT(*) AS ratings
        FROM versions v JOIN worksheets w ON w.sheetid = v.sheetid
        WHERE v.rating IS NOT NULL AND {fragment} GROUP BY v.source
    """
    return _query(sql, params)


def rating_distribution(pilotid=None):
    fragment, params = teacher_filter("w.teacherid", pilotid)
    sql = f"""
        SELECT v.rating, COUNT(*) AS ratings
        FROM versions v JOIN worksheets w ON w.sheetid = v.sheetid
        WHERE v.rating IS NOT NULL AND {fragment} GROUP BY v.rating ORDER BY v.rating
    """
    return _query(sql, params)


def recent_feedback(pilotid=None, limit=50):
    fragment, params = teacher_filter("w.teacherid", pilotid)
    sql = f"""
        SELECT v.createdAt, v.rating, v.source, v.feedback
        FROM versions v JOIN worksheets w ON w.sheetid = v.sheetid
        WHERE v.feedback IS NOT NULL AND {fragment}
        ORDER BY v.createdAt DESC LIMIT %s
    """
    return _query(sql, params + (limit,))


def all_surveys():
    sql = """
        SELECT s.surveyid, s.title, s.surveyDate, s.baseline, s.pilotid,
               COUNT(DISTINCT a.teacherid) AS respondents
        FROM Survey s
        LEFT JOIN Question q ON q.surveyid = s.surveyid
        LEFT JOIN Answer a ON a.questionid = q.questionid
        GROUP BY s.surveyid ORDER BY s.surveyDate DESC
    """
    return _query(sql)


def survey_results(surveyid):
    sql = """
        SELECT q.questionid, q.content AS question, q.questionType,
               a.content AS answer, COUNT(*) AS responses
        FROM Question q JOIN Answer a ON a.questionid = q.questionid
        WHERE q.surveyid = %s AND q.questionType <> 'text'
        GROUP BY q.questionid, q.content, q.questionType, a.content
        ORDER BY q.questionid, a.content
    """
    return _query(sql, (surveyid,))


def text_answers(surveyid):
    sql = """
        SELECT q.content AS question, a.content AS answer, a.answertime
        FROM Question q JOIN Answer a ON a.questionid = q.questionid
        WHERE q.surveyid = %s AND q.questionType = 'text'
        ORDER BY q.questionid, a.answertime
    """
    return _query(sql, (surveyid,))
