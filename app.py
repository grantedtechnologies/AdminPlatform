from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from hmac import compare_digest
import os

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask.json.provider import DefaultJSONProvider
import pymysql

import db

load_dotenv()


class AdminJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, date):
            return o.isoformat()
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
app.json_provider_class = AdminJSONProvider
app.json = AdminJSONProvider(app)

DUPLICATE_CODE = 1062


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
        return view(*args, **kwargs)
    return wrapped


def _safe_next(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value.rstrip("?") or url_for("home")
    return url_for("home")


def _parse_pilotid(required=False):
    raw = request.args.get("pilotid")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        if required:
            return None
        return None


def _dt_local(value):
    if not value:
        return ""
    return value.strftime("%Y-%m-%dT%H:%M")


def _features_payload(pilotid=None):
    return {
        "usage_daily": db.feature_usage_daily(pilotid),
        "dok": db.dok_distribution(pilotid),
        "vocab_daily": db.vocab_lists_daily(pilotid),
        "adoption": db.feature_adoption(pilotid) or {},
    }


def _usage_payload(pilotid=None, include_types=False):
    payload = {
        "usage_daily": db.feature_usage_daily(pilotid),
        "signups": db.signups_daily(pilotid),
        "active_teachers": db.active_teachers_daily(pilotid),
        "active_hours": db.active_hours_daily(pilotid),
        "edit_rate": db.edit_rate(pilotid) or {},
        "funnel": db.funnel(pilotid),
        "return_days": db.return_days(pilotid),
    }
    if include_types:
        payload["teacher_types"] = db.pilot_teacher_types(pilotid)
    return payload


def _pilot_form_values(form=None, pilot=None):
    if form is not None:
        return {
            "name": form.get("name", "").strip(),
            "school": form.get("school", "").strip(),
            "district": form.get("district", "").strip(),
            "code": form.get("code", "").strip(),
            "start": form.get("start", "").strip(),
            "end": form.get("end", "").strip(),
        }
    if pilot:
        return {
            "name": pilot["name"],
            "school": pilot["school"],
            "district": pilot.get("district") or "",
            "code": pilot["code"],
            "start": _dt_local(pilot["startTime"]),
            "end": _dt_local(pilot["endTime"]),
        }
    return {"name": "", "school": "", "district": "", "code": "", "start": "", "end": ""}


def _validate_pilot(values):
    errors = []
    if not values["name"]:
        errors.append("Name is required.")
    if not values["school"]:
        errors.append("School is required.")
    if not values["district"]:
        errors.append("District is required.")
    if not values["code"]:
        errors.append("Code is required.")
    if not values["start"] or not values["end"]:
        errors.append("Start and end dates are required.")
        return errors
    try:
        start = datetime.fromisoformat(values["start"])
        end = datetime.fromisoformat(values["end"])
    except ValueError:
        errors.append("Start and end must be valid dates.")
        return errors
    if end <= start:
        errors.append("End date must be after the start date.")
    return errors


def _round_label(baseline):
    if baseline == 1:
        return "Baseline"
    if baseline == 0:
        return "Follow-up"
    return "Standalone"


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin"):
        return redirect(_safe_next(request.args.get("next")))
    error = None
    if request.method == "POST":
        expected = os.getenv("ADMIN_PASSWORD") or ""
        submitted = request.form.get("password") or ""
        if expected and compare_digest(submitted, expected):
            session["admin"] = True
            return redirect(_safe_next(request.args.get("next")))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/logout")
@admin_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@admin_required
def home():
    return render_template("home.html", section="home")


@app.route("/analytics/features")
@admin_required
def analytics_features():
    return render_template("analytics_features.html", section="analytics")


@app.route("/api/analytics/features")
@admin_required
def api_analytics_features():
    return _features_payload()


@app.route("/analytics/usage")
@admin_required
def analytics_usage():
    return render_template("analytics_usage.html", section="analytics")


@app.route("/api/analytics/usage")
@admin_required
def api_analytics_usage():
    return _usage_payload()


@app.route("/pilot/calendar")
@admin_required
def pilot_calendar():
    return render_template(
        "pilot_calendar.html",
        section="pilot",
        pilots=db.all_pilots(),
    )


@app.route("/api/pilot/calendar")
@admin_required
def api_pilot_calendar():
    return {"pilots": db.all_pilots()}


@app.route("/pilot/maker", methods=["GET", "POST"])
@admin_required
def pilot_maker():
    raw_id = request.args.get("pilotid")
    pilotid = None
    if raw_id not in (None, ""):
        try:
            pilotid = int(raw_id)
        except (TypeError, ValueError):
            return render_template("pilot_maker.html", section="pilot", pilot=None, values=_pilot_form_values(), errors=["That pilot was not found."]), 404
        if not db.get_pilot(pilotid):
            return render_template("pilot_maker.html", section="pilot", pilot=None, values=_pilot_form_values(), errors=["That pilot was not found."]), 404

    errors = []
    values = _pilot_form_values(pilot=db.get_pilot(pilotid) if pilotid else None)

    if request.method == "POST":
        values = _pilot_form_values(form=request.form)
        errors = _validate_pilot(values)
        if not errors:
            district = db.find_district_by_name(values["district"])
            if not district:
                errors.append("No district with that exact name was found.")
        if not errors:
            try:
                if pilotid:
                    db.update_pilot(
                        pilotid,
                        values["name"],
                        values["school"],
                        values["code"],
                        values["start"],
                        values["end"],
                        district["districtid"],
                    )
                    flash("Pilot updated.", "success")
                else:
                    db.create_pilot(
                        values["name"],
                        values["school"],
                        values["code"],
                        values["start"],
                        values["end"],
                        district["districtid"],
                    )
                    flash("Pilot created.", "success")
                return redirect(url_for("pilot_calendar"))
            except pymysql.err.IntegrityError as exc:
                if exc.args and exc.args[0] == DUPLICATE_CODE:
                    errors.append("That pilot code is already in use.")
                else:
                    errors.append("Could not save this pilot.")
            except pymysql.err.OperationalError:
                errors.append("Could not save this pilot.")

    return render_template(
        "pilot_maker.html",
        section="pilot",
        pilotid=pilotid,
        values=values,
        errors=errors,
    )


@app.route("/pilot/features")
@admin_required
def pilot_features():
    return render_template(
        "pilot_features.html",
        section="pilot",
        pilots=db.all_pilots(),
        selected=_parse_pilotid(),
    )


@app.route("/api/pilot/features")
@admin_required
def api_pilot_features():
    pilotid = _parse_pilotid()
    if pilotid is None:
        return {"error": "Select a pilot."}, 400
    return _features_payload(pilotid)


@app.route("/pilot/usage")
@admin_required
def pilot_usage():
    return render_template(
        "pilot_usage.html",
        section="pilot",
        pilots=db.all_pilots(),
        selected=_parse_pilotid(),
    )


@app.route("/api/pilot/usage")
@admin_required
def api_pilot_usage():
    pilotid = _parse_pilotid()
    if pilotid is None:
        return {"error": "Select a pilot."}, 400
    return _usage_payload(pilotid, include_types=True)


@app.route("/survey/maker", methods=["GET", "POST"])
@admin_required
def survey_maker():
    values = {
        "title": "",
        "description": "",
        "baseline": "",
        "pilotid": "",
        "questions": [{"content": "", "questionType": "rating"}],
    }
    errors = []
    if request.method == "POST":
        values["title"] = (request.form.get("title") or "").strip()
        values["description"] = (request.form.get("description") or "").strip()
        values["baseline"] = request.form.get("baseline", "")
        values["pilotid"] = request.form.get("pilotid", "")
        contents = request.form.getlist("question_text")
        types = request.form.getlist("question_type")
        questions = []
        for content, question_type in zip(contents, types):
            questions.append({
                "content": (content or "").strip(),
                "questionType": question_type,
            })
        if not questions:
            questions = [{"content": "", "questionType": "rating"}]
        values["questions"] = questions

        if not values["title"]:
            errors.append("Title is required.")
        if not values["description"]:
            errors.append("Description is required.")
        if values["baseline"] not in ("1", "0", ""):
            errors.append("Choose a survey round.")
        filled = [q for q in questions if q["content"]]
        if not filled:
            errors.append("Add at least one question.")
        allowed = {"rating", "number", "text"}
        for question in filled:
            if question["questionType"] not in allowed:
                errors.append("Each question needs a type of rating, number, or text.")
                break
        pilotid = None
        if values["pilotid"]:
            try:
                pilotid = int(values["pilotid"])
            except (TypeError, ValueError):
                errors.append("Choose a valid pilot.")
                pilotid = None
            else:
                if not db.get_pilot(pilotid):
                    errors.append("Choose a valid pilot.")

        if not errors:
            baseline = None if values["baseline"] == "" else int(values["baseline"])
            surveyid = db.create_survey_with_questions(
                values["title"],
                values["description"],
                baseline,
                pilotid,
                filled,
            )
            flash("Survey created.", "success")
            return redirect(url_for("survey_result", surveyid=surveyid))

    return render_template(
        "survey_maker.html",
        section="survey",
        pilots=db.all_pilots(),
        values=values,
        errors=errors,
    )


@app.route("/survey/feedback")
@admin_required
def survey_feedback():
    return render_template(
        "survey_feedback.html",
        section="survey",
        pilots=db.all_pilots(),
        selected=_parse_pilotid(),
    )


@app.route("/api/survey/feedback")
@admin_required
def api_survey_feedback():
    pilotid = _parse_pilotid()
    return {
        "rating_by_source": db.rating_by_source(pilotid),
        "rating_distribution": db.rating_distribution(pilotid),
        "recent_feedback": db.recent_feedback(pilotid, 50),
    }


@app.route("/survey/results")
@admin_required
def survey_results():
    surveys = db.all_surveys()
    pilots = {p["pilotid"]: p["name"] for p in db.all_pilots()}
    rows = []
    for survey in surveys:
        rows.append({
            **survey,
            "round": _round_label(survey["baseline"]),
            "pilot_name": pilots.get(survey["pilotid"]) if survey["pilotid"] else "—",
        })
    return render_template("survey_results.html", section="survey", surveys=rows)


@app.route("/survey/results/<int:surveyid>")
@admin_required
def survey_result(surveyid):
    surveys = db.all_surveys()
    survey = next((s for s in surveys if s["surveyid"] == surveyid), None)
    if survey is None:
        return render_template("survey_result.html", section="survey", survey=None), 404
    pilots = {p["pilotid"]: p["name"] for p in db.all_pilots()}
    survey = {
        **survey,
        "round": _round_label(survey["baseline"]),
        "pilot_name": pilots.get(survey["pilotid"]) if survey["pilotid"] else "—",
    }
    return render_template("survey_result.html", section="survey", survey=survey)


@app.route("/api/survey/results/<int:surveyid>")
@admin_required
def api_survey_result(surveyid):
    return {
        "results": db.survey_results(surveyid),
        "text_answers": db.text_answers(surveyid),
    }


@app.route("/monitoring/audit")
@admin_required
def monitoring_audit():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    per_page = 50
    rows = db.audit_log(per_page, (page - 1) * per_page)
    return render_template(
        "monitoring_audit.html",
        section="monitoring",
        rows=rows,
        page=page,
        has_next=len(rows) == per_page,
    )


@app.route("/api/monitoring/audit")
@admin_required
def api_monitoring_audit():
    return {"failures": db.failure_summary()}


@app.route("/testing")
@admin_required
def testing():
    return render_template("testing.html", section="testing")


if __name__ == "__main__":
    app.run(debug=True)
