"""HTTP routes."""

from __future__ import annotations

import re
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup, escape

from . import auth, exams, flashcards
from .db import get_db
from .selection import focus_topics
from .stats import answered_count, domain_stats, topic_stats

bp = Blueprint("main", __name__)

CODE_SPAN = re.compile(r"`([^`\n]+)`")


# --- template helpers ---------------------------------------------------------------------------


@bp.app_context_processor
def inject_globals() -> dict:
    conn = get_db()
    return {
        "current_user": g.get("user"),
        "bank_counts": {
            "questions": conn.execute("SELECT COUNT(*) FROM questions WHERE active = 1").fetchone()[0],
            "flashcards": conn.execute("SELECT COUNT(*) FROM flashcards WHERE active = 1").fetchone()[0],
        },
    }


@bp.app_template_filter("when")
def format_when(value: str | None) -> str:
    if not value:
        return ""
    dt = datetime.fromisoformat(value).astimezone()
    return dt.strftime("%b %d, %Y · %H:%M")


@bp.app_template_filter("duration")
def format_duration(exam) -> str:
    if not exam["completed_at"]:
        return ""
    seconds = int(
        (datetime.fromisoformat(exam["completed_at"]) - datetime.fromisoformat(exam["started_at"])).total_seconds()
    )
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {secs:02d}s"


@bp.app_template_filter("rich")
def format_rich(text: str | None) -> Markup:
    """Escape text, then turn `backtick spans` into <code> (mirrors TestPrep.rich in app.js)."""
    return Markup(CODE_SPAN.sub(r"<code>\1</code>", str(escape(text or ""))))


@bp.app_template_filter("question_rich")
def format_question(text: str | None) -> Markup:
    """Server-side twin of TestPrep.questionText: lead line, code snippet, optional trailing question."""
    lines = (text or "").split("\n")
    if len(lines) < 2:
        return format_rich(text)
    has_tail = len(lines) >= 3 and lines[-1].strip().endswith("?")
    code = "\n".join(lines[1:-1] if has_tail else lines[1:])
    html = f"<span>{format_rich(lines[0])}</span><pre class=\"snippet\">{escape(code)}</pre>"
    if has_tail:
        html += f"<span>{format_rich(lines[-1])}</span>"
    return Markup(html)


@bp.app_template_filter("pct")
def format_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


# --- profile ------------------------------------------------------------------------------------


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        name = (request.form.get("name") or "").strip()[:40]
        if action == "rename" and name:
            if conn.execute("SELECT 1 FROM users WHERE name = ? AND id != ?", (name, g.user["id"])).fetchone():
                flash(f"A profile called “{name}” already exists.", "error")
            else:
                conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, g.user["id"]))
                conn.commit()
                flash("Profile renamed.", "success")
        elif action == "reset":
            conn.execute("DELETE FROM exams WHERE user_id = ?", (g.user["id"],))
            conn.execute("DELETE FROM flashcard_sessions WHERE user_id = ?", (g.user["id"],))
            conn.execute("DELETE FROM flashcard_reviews WHERE user_id = ?", (g.user["id"],))
            conn.commit()
            flash("All progress for this profile was cleared.", "success")
        elif action == "delete":
            conn.execute("DELETE FROM users WHERE id = ?", (g.user["id"],))
            conn.commit()
            session.clear()
            flash("Your profile and all of its history were deleted.", "success")
            return redirect(url_for("auth.login"))
        return redirect(url_for("main.profile"))
    identities = conn.execute(
        "SELECT provider, email, last_login_at FROM identities WHERE user_id = ? ORDER BY last_login_at DESC",
        (g.user["id"],),
    ).fetchall()
    return render_template("profile.html", identities=identities, provider_labels=auth.PROVIDERS)


# --- dashboard & progress -----------------------------------------------------------------------


def _summary(conn, user_id: int) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS completed, AVG(score) AS avg_score, MAX(score) AS best, "
        "(SELECT score FROM exams WHERE user_id = ? AND completed_at IS NOT NULL "
        " ORDER BY completed_at DESC LIMIT 1) AS last_score "
        "FROM exams WHERE user_id = ? AND completed_at IS NOT NULL",
        (user_id, user_id),
    ).fetchone()
    reviews = conn.execute("SELECT COUNT(*) FROM flashcard_reviews WHERE user_id = ?", (user_id,)).fetchone()[0]
    return {
        "completed": row["completed"],
        "avg_score": row["avg_score"],
        "best": row["best"],
        "last_score": row["last_score"],
        "answered": answered_count(conn, user_id),
        "reviews": reviews,
    }


@bp.route("/")
def dashboard():
    conn = get_db()
    user_id = g.user["id"]
    topics = topic_stats(conn, user_id)
    in_progress = conn.execute(
        "SELECT e.*, (SELECT COUNT(*) FROM exam_questions eq WHERE eq.exam_id = e.id "
        "AND eq.selected_choice_id IS NOT NULL) AS answered "
        "FROM exams e WHERE e.user_id = ? AND e.completed_at IS NULL ORDER BY e.started_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return render_template(
        "dashboard.html",
        summary=_summary(conn, user_id),
        domains=domain_stats(topics),
        focus=focus_topics(topics, limit=4),
        recent=exams.history(conn, user_id)[:5],
        in_progress=in_progress,
    )


@bp.route("/progress")
def progress():
    conn = get_db()
    user_id = g.user["id"]
    topics = topic_stats(conn, user_id)
    completed = conn.execute(
        "SELECT id, mode, score, correct, total, completed_at FROM exams "
        "WHERE user_id = ? AND completed_at IS NOT NULL ORDER BY completed_at",
        (user_id,),
    ).fetchall()
    card_ratings = {
        row["topic_id"]: {"known": row["known"], "learning": row["learning"]}
        for row in conn.execute(
            "SELECT f.topic_id, SUM(latest.rating = 'known') AS known, SUM(latest.rating = 'learning') AS learning "
            "FROM (SELECT flashcard_id, rating, MAX(reviewed_at) FROM flashcard_reviews WHERE user_id = ? "
            "      GROUP BY flashcard_id) latest "
            "JOIN flashcards f ON f.id = latest.flashcard_id GROUP BY f.topic_id",
            (user_id,),
        )
    }
    card_totals = {
        row["topic_id"]: row["n"]
        for row in conn.execute("SELECT topic_id, COUNT(*) AS n FROM flashcards WHERE active = 1 GROUP BY topic_id")
    }
    question_totals = {
        row["topic_id"]: row["n"]
        for row in conn.execute("SELECT topic_id, COUNT(*) AS n FROM questions WHERE active = 1 GROUP BY topic_id")
    }
    return render_template(
        "progress.html",
        summary=_summary(conn, user_id),
        domains=domain_stats(topics),
        trend=[
            {"id": e["id"], "score": e["score"], "mode": e["mode"], "date": format_when(e["completed_at"]),
             "correct": e["correct"], "total": e["total"]}
            for e in completed
        ],
        card_ratings=card_ratings,
        card_totals=card_totals,
        question_totals=question_totals,
        focus=focus_topics(topics, limit=6),
    )


# --- exams --------------------------------------------------------------------------------------


@bp.route("/exams")
def exam_hub():
    conn = get_db()
    user_id = g.user["id"]
    topics = topic_stats(conn, user_id)
    return render_template(
        "exams.html",
        history=exams.history(conn, user_id),
        focus=focus_topics(topics, limit=5),
        answered=answered_count(conn, user_id),
        exam_size=exams.EXAM_SIZE,
    )


@bp.post("/exams")
def start_exam():
    mode = request.form.get("mode", "random")
    try:
        exam_id = exams.create_exam(get_db(), g.user["id"], mode)
    except exams.ExamError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.exam_hub"))
    return redirect(url_for("main.take_exam", exam_id=exam_id))


def _exam_or_404(exam_id: int):
    try:
        return exams.get_exam(get_db(), exam_id, g.user["id"])
    except exams.ExamError:
        abort(404)


@bp.route("/exams/<int:exam_id>")
def take_exam(exam_id: int):
    exam = _exam_or_404(exam_id)
    if exam["completed_at"]:
        return redirect(url_for("main.exam_results", exam_id=exam_id))
    items = exams.exam_items(get_db(), exam_id, include_answers=False)
    return render_template("exam_take.html", exam=exam, items=items)


@bp.post("/api/exams/<int:exam_id>/answer")
def api_answer(exam_id: int):
    data = request.get_json(silent=True) or {}
    try:
        result = exams.answer(
            get_db(), exam_id, g.user["id"], int(data.get("position", 0)), int(data.get("choice_id", 0))
        )
    except (exams.ExamError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@bp.post("/exams/<int:exam_id>/finish")
def finish_exam(exam_id: int):
    _exam_or_404(exam_id)
    exams.finish(get_db(), exam_id, g.user["id"])
    return redirect(url_for("main.exam_results", exam_id=exam_id))


@bp.route("/exams/<int:exam_id>/results")
def exam_results(exam_id: int):
    exam = _exam_or_404(exam_id)
    if not exam["completed_at"]:
        return redirect(url_for("main.take_exam", exam_id=exam_id))
    items = exams.exam_items(get_db(), exam_id, include_answers=True)
    previous = get_db().execute(
        "SELECT score FROM exams WHERE user_id = ? AND completed_at IS NOT NULL AND completed_at < ? "
        "ORDER BY completed_at DESC LIMIT 1",
        (g.user["id"], exam["completed_at"]),
    ).fetchone()
    return render_template(
        "exam_results.html",
        exam=exam,
        items=items,
        breakdown=exams.breakdown(items),
        unanswered=sum(1 for i in items if not i["answered"]),
        previous_score=previous["score"] if previous else None,
    )


@bp.post("/exams/<int:exam_id>/delete")
def delete_exam(exam_id: int):
    _exam_or_404(exam_id)
    exams.delete(get_db(), exam_id, g.user["id"])
    flash("Exam deleted.", "success")
    return redirect(url_for("main.exam_hub"))


# --- flash cards --------------------------------------------------------------------------------


@bp.route("/flashcards")
def flashcard_hub():
    conn = get_db()
    user_id = g.user["id"]
    topics = topic_stats(conn, user_id)
    domains = conn.execute(
        "SELECT d.id, d.name, COUNT(f.id) AS cards FROM domains d "
        "JOIN topics t ON t.domain_id = d.id LEFT JOIN flashcards f ON f.topic_id = t.id AND f.active = 1 "
        "GROUP BY d.id ORDER BY d.sort_order"
    ).fetchall()
    topic_rows = conn.execute(
        "SELECT t.id, t.name, t.domain_id, COUNT(f.id) AS cards FROM topics t "
        "LEFT JOIN flashcards f ON f.topic_id = t.id AND f.active = 1 "
        "GROUP BY t.id ORDER BY t.domain_id, t.sort_order"
    ).fetchall()
    return render_template(
        "flashcards.html",
        domains=domains,
        topics=topic_rows,
        focus=focus_topics(topics, limit=5),
        answered=answered_count(conn, user_id),
        sessions=flashcards.recent_sessions(conn, user_id),
        deck_sizes=flashcards.DECK_SIZES,
        default_size=flashcards.DEFAULT_DECK_SIZE,
        preselect_topic=request.args.get("topic", type=int),
    )


@bp.post("/flashcards")
def start_flashcards():
    mode = request.form.get("mode", "random")
    size = request.form.get("size", flashcards.DEFAULT_DECK_SIZE, type=int)
    scope = request.form.get("scope", "")
    domain_id = topic_id = None
    if scope.startswith("domain:"):
        domain_id = int(scope.split(":", 1)[1])
    elif scope.startswith("topic:"):
        topic_id = int(scope.split(":", 1)[1])
    try:
        session_id = flashcards.create_session(
            get_db(), g.user["id"], mode, max(1, min(size, 100)), domain_id, topic_id
        )
    except flashcards.FlashcardError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.flashcard_hub"))
    return redirect(url_for("main.study", session_id=session_id))


@bp.route("/flashcards/<int:session_id>")
def study(session_id: int):
    conn = get_db()
    try:
        fc_session = flashcards.get_session(conn, session_id, g.user["id"])
    except flashcards.FlashcardError:
        abort(404)
    return render_template(
        "flashcards_study.html", fc_session=fc_session, cards=flashcards.session_cards(conn, fc_session)
    )


@bp.post("/api/flashcards/<int:session_id>/review")
def api_review(session_id: int):
    data = request.get_json(silent=True) or {}
    try:
        flashcards.review(get_db(), session_id, g.user["id"], int(data.get("card_id", 0)), str(data.get("rating")))
    except (flashcards.FlashcardError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@bp.post("/flashcards/<int:session_id>/retry")
def retry_flashcards(session_id: int):
    try:
        new_id = flashcards.retry_session(get_db(), session_id, g.user["id"])
    except flashcards.FlashcardError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.study", session_id=session_id))
    return redirect(url_for("main.study", session_id=new_id))
