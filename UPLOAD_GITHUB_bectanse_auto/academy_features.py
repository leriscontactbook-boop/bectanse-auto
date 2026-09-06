import json
import math
import re
from datetime import datetime

from flask import jsonify, redirect, render_template, request, session, url_for


ACADEMY_CURRICULUM = [
    {
        "key": "fondations", "number": "01", "title": "Fondations du trader",
        "subtitle": "Comprendre le marché avant de chercher un signal.",
        "lessons": [
            {"key": "bases-trading", "title": "Comprendre le trading", "type": "video", "duration": "3 min", "url": "https://www.youtube.com/watch?v=H2_4GyTGqYQ"},
            {"key": "introduction-trading", "title": "Introduction au trading", "type": "pdf", "duration": "15 min", "url": "https://drive.google.com/file/d/1EHWq2LlH-1ltvLJ-86cXQqB5I_3K43j6/view"},
            {"key": "role-broker", "title": "Comprendre le rôle du broker", "type": "video", "duration": "2 min", "url": "https://www.youtube.com/watch?v=PBKDkBsuXm0"},
        ],
        "quiz": {"question": "Quelle est la première mission d’un plan de trading ?", "answers": ["Prédire chaque mouvement", "Définir les conditions et le risque", "Multiplier les positions"], "correct": 1},
    },
    {
        "key": "lecture", "number": "02", "title": "Lecture du marché",
        "subtitle": "Structure, contexte et niveaux avant toute décision.",
        "lessons": [
            {"key": "analyse-technique", "title": "Analyse technique", "type": "pdf", "duration": "20 min", "url": "https://drive.google.com/file/d/1H2XiDg_G8D7e1vSFwgqH6jstJn5pxr-G/view"},
            {"key": "analyse-fondamentale", "title": "Analyse fondamentale", "type": "pdf", "duration": "18 min", "url": "https://drive.google.com/file/d/1I1UYl2-OO6las6u2gN2FzceHkwybK_iH/view"},
            {"key": "facteurs-or", "title": "Les facteurs qui font bouger l’or", "type": "pdf", "duration": "22 min", "url": "https://drive.google.com/file/d/1xExENFWAYRpFr-wwlY5q61UYkglYAmdq/view"},
        ],
        "quiz": {"question": "Une zone devient exploitable quand…", "answers": ["elle est confirmée par le contexte et un déclencheur", "elle est colorée sur le graphique", "le prix s’en approche"], "correct": 0},
    },
    {
        "key": "execution", "number": "03", "title": "Exécution maîtrisée",
        "subtitle": "Entrer, protéger et sortir sans improvisation.",
        "lessons": [
            {"key": "lancer-trade", "title": "Comment lancer un trade", "type": "video", "duration": "6 min", "url": "https://www.youtube.com/watch?v=xTDauJuGM-0"},
            {"key": "securiser-trade", "title": "Comment sécuriser ses trades", "type": "video", "duration": "2 min", "url": "https://www.youtube.com/watch?v=Cc7pRmfmP84"},
            {"key": "prendre-profits", "title": "Comment prendre ses profits", "type": "video", "duration": "4 min", "url": "https://www.youtube.com/watch?v=P6Ikb41wwdc"},
        ],
        "quiz": {"question": "Quand le stop doit-il être défini ?", "answers": ["Après l’entrée", "Avant l’entrée", "Seulement si le marché baisse"], "correct": 1},
    },
    {
        "key": "risque", "number": "04", "title": "Gestion du risque",
        "subtitle": "Protéger le capital et travailler avec un R/R cohérent.",
        "lessons": [
            {"key": "gestion-risque", "title": "Gestion du risque", "type": "pdf", "duration": "20 min", "url": "https://drive.google.com/file/d/1Low5Nj-km7gQwS3b_eOpj64FZs1TrC9P/view"},
            {"key": "money-management-or", "title": "Money management spécifique à l’or", "type": "pdf", "duration": "25 min", "url": "https://drive.google.com/file/d/1efP-whhnYfQ09XRka-ETKLhEIUUtzfmz/view"},
            {"key": "erreurs-debutant", "title": "Les erreurs du débutant", "type": "video", "duration": "6 min", "url": "https://www.youtube.com/watch?v=kzeV1U3wbbU"},
        ],
        "quiz": {"question": "Un R/R de 1:2 signifie…", "answers": ["risquer 2 pour viser 1", "risquer 1 pour viser 2", "gagner deux trades sur trois"], "correct": 1},
    },
    {
        "key": "psychologie", "number": "05", "title": "Psychologie & discipline",
        "subtitle": "Construire un processus répétable même sous pression.",
        "lessons": [
            {"key": "psychologie-trader", "title": "Psychologie du trader", "type": "pdf", "duration": "20 min", "url": "https://drive.google.com/file/d/1ThwdFM4CJH7Ko52W1XLIWAf7dtlC2CyI/view"},
            {"key": "preparation-pratique", "title": "Préparation pratique", "type": "pdf", "duration": "18 min", "url": "https://drive.google.com/file/d/1lFlAlqvoKWq1iIKZpNlWeyQ7XLFn_OYM/view"},
            {"key": "etudes-cas-or", "title": "Études de cas historiques sur l’or", "type": "pdf", "duration": "30 min", "url": "https://drive.google.com/file/d/18hbWOlZLpiIheOsNmXcsONAeYXUlqXN1/view"},
        ],
        "quiz": {"question": "Après une perte, la meilleure décision est…", "answers": ["augmenter le risque", "revenir au plan et documenter", "reprendre immédiatement"], "correct": 1},
    },
    {
        "key": "plan-xau", "number": "06", "title": "Plan XAU/USD",
        "subtitle": "Assembler analyse, exécution, risque et revue.",
        "lessons": [
            {"key": "lire-xau", "title": "Lire XAU/USD comme un professionnel", "type": "pdf", "duration": "25 min", "url": "https://drive.google.com/file/d/1xJPJUdNOkAs_hTPt_01jiTENxMXdWdg0/view"},
            {"key": "strategies-or", "title": "Stratégies de trading sur l’or", "type": "pdf", "duration": "30 min", "url": "https://drive.google.com/file/d/1t6NzPSTpNDVm91GpFMClnE7oPTATLTrG/view"},
            {"key": "plan-complet-xau", "title": "Plan de trading complet XAU/USD", "type": "pdf", "duration": "35 min", "url": "https://drive.google.com/file/d/1mKO81-1wQQWOpBuO1rniQWnkY26i_Bj0/view"},
        ],
        "quiz": {"question": "Un plan complet doit contenir au minimum…", "answers": ["une direction", "entrée, invalidation, objectifs et risque", "un indicateur"], "correct": 1},
    },
]

LESSON_KEYS = {lesson["key"] for phase in ACADEMY_CURRICULUM for lesson in phase["lessons"]}
PHASES_BY_KEY = {phase["key"]: phase for phase in ACADEMY_CURRICULUM}


def ensure_growth_schema(conn):
    """Schéma additive-only : aucun changement sur le copy trading ou les offres."""
    conn.run("""CREATE TABLE IF NOT EXISTS member_learning_profiles (
        member_code TEXT PRIMARY KEY,
        experience TEXT NOT NULL DEFAULT '',
        objective TEXT NOT NULL DEFAULT '',
        weekly_time TEXT NOT NULL DEFAULT '',
        trading_style TEXT NOT NULL DEFAULT '',
        confidence INTEGER NOT NULL DEFAULT 0,
        path_code TEXT NOT NULL DEFAULT 'fondations',
        daily_goal TEXT NOT NULL DEFAULT '',
        onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    conn.run("""CREATE TABLE IF NOT EXISTS academy_progress (
        member_code TEXT NOT NULL,
        lesson_key TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'started',
        progress_percent INTEGER NOT NULL DEFAULT 0,
        started_at TIMESTAMP NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMP,
        last_viewed_at TIMESTAMP NOT NULL DEFAULT NOW(),
        PRIMARY KEY (member_code, lesson_key)
    )""")
    conn.run("""CREATE INDEX IF NOT EXISTS academy_progress_member_idx
        ON academy_progress (member_code, last_viewed_at DESC)""")
    conn.run("""CREATE TABLE IF NOT EXISTS academy_quiz_attempts (
        id BIGSERIAL PRIMARY KEY,
        member_code TEXT NOT NULL,
        phase_key TEXT NOT NULL,
        score INTEGER NOT NULL,
        passed BOOLEAN NOT NULL DEFAULT FALSE,
        answers_json TEXT NOT NULL DEFAULT '{}',
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    conn.run("""CREATE INDEX IF NOT EXISTS academy_quiz_member_idx
        ON academy_quiz_attempts (member_code, created_at DESC)""")
    conn.run("""CREATE TABLE IF NOT EXISTS simulator_sessions (
        id BIGSERIAL PRIMARY KEY,
        member_code TEXT NOT NULL,
        market TEXT NOT NULL,
        timeframe TEXT NOT NULL DEFAULT 'M15',
        direction TEXT NOT NULL,
        account_size NUMERIC NOT NULL,
        risk_pct NUMERIC NOT NULL,
        entry_price NUMERIC NOT NULL,
        stop_loss NUMERIC NOT NULL,
        tp1 NUMERIC,
        tp2 NUMERIC,
        tp3 NUMERIC,
        risk_amount NUMERIC NOT NULL,
        theoretical_units NUMERIC NOT NULL,
        rr1 NUMERIC,
        rr2 NUMERIC,
        rr3 NUMERIC,
        outcome TEXT NOT NULL DEFAULT 'pending',
        outcome_r NUMERIC,
        notes TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        closed_at TIMESTAMP
    )""")
    conn.run("""CREATE INDEX IF NOT EXISTS simulator_member_idx
        ON simulator_sessions (member_code, created_at DESC)""")
    conn.run("""CREATE TABLE IF NOT EXISTS trading_journal_entries (
        id BIGSERIAL PRIMARY KEY,
        member_code TEXT NOT NULL,
        analysis_job_id TEXT,
        simulator_session_id BIGINT,
        market TEXT NOT NULL,
        timeframe TEXT NOT NULL DEFAULT 'M15',
        direction TEXT NOT NULL,
        setup TEXT NOT NULL DEFAULT '',
        context TEXT NOT NULL DEFAULT '',
        entry_price NUMERIC,
        stop_loss NUMERIC,
        tp1 NUMERIC,
        tp2 NUMERIC,
        tp3 NUMERIC,
        exit_price NUMERIC,
        risk_pct NUMERIC NOT NULL DEFAULT 0,
        result_r NUMERIC,
        emotion_before TEXT NOT NULL DEFAULT '',
        emotion_after TEXT NOT NULL DEFAULT '',
        plan_followed BOOLEAN NOT NULL DEFAULT FALSE,
        mistake TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        plan_snapshot TEXT NOT NULL DEFAULT '{}',
        intelligent_review TEXT NOT NULL DEFAULT '',
        trade_date TIMESTAMP NOT NULL DEFAULT NOW(),
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    conn.run("""CREATE UNIQUE INDEX IF NOT EXISTS journal_analysis_unique_idx
        ON trading_journal_entries (member_code, analysis_job_id) WHERE analysis_job_id IS NOT NULL""")
    conn.run("""CREATE INDEX IF NOT EXISTS journal_member_idx
        ON trading_journal_entries (member_code, trade_date DESC)""")
    conn.run("""CREATE TABLE IF NOT EXISTS trade_score_snapshots (
        id BIGSERIAL PRIMARY KEY,
        member_code TEXT NOT NULL,
        total_score INTEGER NOT NULL,
        discipline_score INTEGER NOT NULL,
        risk_score INTEGER NOT NULL,
        learning_score INTEGER NOT NULL,
        preparation_score INTEGER NOT NULL,
        consistency_score INTEGER NOT NULL,
        rank_label TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    conn.run("""CREATE INDEX IF NOT EXISTS trade_score_member_idx
        ON trade_score_snapshots (member_code, created_at DESC)""")
    conn.run("""CREATE TABLE IF NOT EXISTS analysis_feedback (
        job_id TEXT PRIMARY KEY,
        member_code TEXT NOT NULL,
        usefulness INTEGER NOT NULL DEFAULT 0,
        clarity INTEGER NOT NULL DEFAULT 0,
        outcome_status TEXT NOT NULL DEFAULT 'pending',
        outcome_r NUMERIC,
        notes TEXT NOT NULL DEFAULT '',
        saved_to_journal BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")


def _safe_float(value, minimum=None, maximum=None, required=False):
    if value in (None, ""):
        if required:
            raise ValueError("Valeur obligatoire")
        return None
    number = float(str(value).replace(" ", "").replace(",", "."))
    if not math.isfinite(number):
        raise ValueError("Valeur invalide")
    if minimum is not None and number < minimum:
        raise ValueError("Valeur trop basse")
    if maximum is not None and number > maximum:
        raise ValueError("Valeur trop élevée")
    return number


def _num_from_text(value):
    text = str(value or "").replace("\u202f", " ")
    matches = re.findall(r"-?\d[\d\s]*(?:[.,]\d+)?", text)
    if not matches:
        return None
    try:
        return float(matches[0].replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _score_member(conn, code, persist=False):
    total_lessons = max(1, len(LESSON_KEYS))
    completed = int(conn.run("""SELECT COUNT(*) FROM academy_progress
        WHERE member_code=:code AND status='completed'""", code=code)[0][0])
    journal = conn.run("""SELECT risk_pct, plan_followed, context, analysis_job_id, trade_date
        FROM trading_journal_entries WHERE member_code=:code ORDER BY trade_date DESC LIMIT 50""", code=code)
    simulations = conn.run("""SELECT risk_pct, created_at FROM simulator_sessions
        WHERE member_code=:code ORDER BY created_at DESC LIMIT 50""", code=code)

    if journal:
        planned = sum(1 for row in journal if row[1]) / len(journal)
        discipline = round(30 * planned)
        good_risk = sum(1 for row in journal if 0 < float(row[0] or 0) <= 1.0) / len(journal)
        preparation_ratio = sum(1 for row in journal if str(row[2] or "").strip() and row[3]) / len(journal)
    else:
        discipline = 0
        good_risk = 0
        preparation_ratio = 0
    if simulations:
        sim_good = sum(1 for row in simulations if 0 < float(row[0] or 0) <= 1.0) / len(simulations)
        good_risk = (good_risk + sim_good) / (2 if journal else 1)
    risk = round(25 * good_risk)
    learning = round(20 * min(1, completed / total_lessons))
    preparation = round(15 * preparation_ratio)

    dates = set()
    for row in journal:
        if row[4]: dates.add(row[4].date().isoformat())
    for row in simulations:
        if row[1]: dates.add(row[1].date().isoformat())
    progress_dates = conn.run("""SELECT DISTINCT DATE(last_viewed_at) FROM academy_progress
        WHERE member_code=:code AND last_viewed_at > NOW() - INTERVAL '14 days'""", code=code)
    dates.update(row[0].isoformat() for row in progress_dates if row[0])
    consistency = round(10 * min(1, len(dates) / 6))
    total = max(0, min(100, discipline + risk + learning + preparation + consistency))
    rank = ("ÉLITE" if total >= 85 else "AVANCÉ" if total >= 70 else
            "DISCIPLINÉ" if total >= 50 else "CONSTRUCTEUR" if total >= 30 else "DÉPART")
    result = {
        "total": total, "rank": rank, "discipline": discipline, "risk": risk,
        "learning": learning, "preparation": preparation, "consistency": consistency,
        "completed_lessons": completed, "total_lessons": total_lessons,
        "journal_count": len(journal), "simulation_count": len(simulations),
        "active_days": len(dates),
    }
    if persist:
        conn.run("""INSERT INTO trade_score_snapshots
            (member_code,total_score,discipline_score,risk_score,learning_score,
             preparation_score,consistency_score,rank_label,details_json)
            VALUES (:code,:total,:discipline,:risk,:learning,:preparation,:consistency,:rank,:details)""",
            code=code, total=total, discipline=discipline, risk=risk, learning=learning,
            preparation=preparation, consistency=consistency, rank=rank,
            details=json.dumps(result, ensure_ascii=False))
    return result


def _journal_review(data):
    strengths, priorities = [], []
    risk = float(data.get("risk_pct") or 0)
    result_r = data.get("result_r")
    if data.get("plan_followed"):
        strengths.append("Le plan annoncé a été respecté.")
    else:
        priorities.append("Écrire les conditions d’entrée puis ne rien modifier pendant l’exécution.")
    if 0 < risk <= 1:
        strengths.append("Le risque reste dans une zone disciplinée (≤ 1 %).")
    elif risk > 1:
        priorities.append("Réduire le risque par position à 1 % maximum pendant la phase de progression.")
    if str(data.get("emotion_before") or "").lower() in {"stress", "fomo", "colère", "revanche"}:
        priorities.append("Mettre en pause l’exécution lorsque l’émotion dominante n’est pas neutre.")
    if result_r is not None and float(result_r) < 0 and data.get("plan_followed"):
        strengths.append("Une perte conforme au plan reste une bonne exécution.")
    if not data.get("context"):
        priorities.append("Documenter le contexte et le niveau d’invalidation avant l’entrée.")
    if not strengths:
        strengths.append("Le trade a été documenté : c’est la base d’une amélioration mesurable.")
    if not priorities:
        priorities.append("Continuer le même processus sur une série d’au moins 10 décisions.")
    return "POINT FORT — " + " ".join(strengths[:3]) + " PROCHAINE ACTION — " + " ".join(priorities[:2])


def _member_context(get_member, current_demo_mode):
    code = session.get("member_code", "")
    member = get_member(code)
    return code, member, current_demo_mode(code, member)


def register_growth_features(app, get_conn, get_member, login_required,
                             academy_access_required, current_demo_mode,
                             admin_required):
    @app.route("/academie")
    @login_required
    def academie():
        code, member, demo_mode = _member_context(get_member, current_demo_mode)
        conn = get_conn()
        try:
            ensure_growth_schema(conn)
            profile_rows = conn.run("SELECT * FROM member_learning_profiles WHERE member_code=:code", code=code)
            profile = None
            if profile_rows:
                cols = [column["name"] for column in conn.columns]
                profile = dict(zip(cols, profile_rows[0]))
            progress_rows = conn.run("""SELECT lesson_key,status,progress_percent
                FROM academy_progress WHERE member_code=:code""", code=code)
            progress = {row[0]: {"status": row[1], "percent": int(row[2] or 0)} for row in progress_rows}
            passed_rows = conn.run("""SELECT phase_key,MAX(score) FROM academy_quiz_attempts
                WHERE member_code=:code AND passed=TRUE GROUP BY phase_key""", code=code)
            passed = {row[0]: int(row[1]) for row in passed_rows}
            completed = sum(1 for item in progress.values() if item["status"] == "completed")
            pct = round(completed / max(1, len(LESSON_KEYS)) * 100)
            next_lesson = next((lesson for phase in ACADEMY_CURRICULUM for lesson in phase["lessons"]
                                if progress.get(lesson["key"], {}).get("status") != "completed"), None)
            score = _score_member(conn, code)
        finally:
            conn.close()
        return render_template("academie.html", member=member, demo_mode=demo_mode,
                               profile=profile, curriculum=ACADEMY_CURRICULUM,
                               progress=progress, passed=passed, academy_percent=pct,
                               completed_lessons=completed, total_lessons=len(LESSON_KEYS),
                               next_lesson=next_lesson, trade_score=score)

    @app.route("/api/academy/diagnostic", methods=["POST"])
    @login_required
    @academy_access_required
    def academy_diagnostic():
        data = request.get_json(silent=True) or {}
        experience = str(data.get("experience", ""))[:40]
        objective = str(data.get("objective", ""))[:80]
        weekly_time = str(data.get("weekly_time", ""))[:40]
        style = str(data.get("trading_style", ""))[:40]
        confidence = int(data.get("confidence", 0) or 0)
        allowed_experience = {"debutant", "intermediaire", "avance"}
        if experience not in allowed_experience or not objective or not weekly_time or not style or confidence not in range(1, 6):
            return jsonify({"ok": False, "error": "Complète toutes les réponses du diagnostic."}), 400
        path = "fondations" if experience == "debutant" or confidence <= 2 else ("risque" if objective == "discipline" else "lecture")
        daily_goal = "15 minutes de formation + 1 décision simulée" if weekly_time in {"1-2h", "3-4h"} else "1 leçon + 1 décision simulée + journal"
        conn = get_conn()
        try:
            conn.run("""INSERT INTO member_learning_profiles
                (member_code,experience,objective,weekly_time,trading_style,confidence,path_code,daily_goal,onboarding_completed)
                VALUES (:code,:experience,:objective,:weekly,:style,:confidence,:path,:goal,TRUE)
                ON CONFLICT (member_code) DO UPDATE SET experience=:experience,objective=:objective,
                weekly_time=:weekly,trading_style=:style,confidence=:confidence,path_code=:path,
                daily_goal=:goal,onboarding_completed=TRUE,updated_at=NOW()""",
                code=session["member_code"], experience=experience, objective=objective,
                weekly=weekly_time, style=style, confidence=confidence, path=path, goal=daily_goal)
            return jsonify({"ok": True, "path": path, "daily_goal": daily_goal})
        finally:
            conn.close()

    @app.route("/api/academy/lesson", methods=["POST"])
    @login_required
    @academy_access_required
    def academy_lesson_progress():
        data = request.get_json(silent=True) or {}
        key = str(data.get("lesson_key", ""))
        status = str(data.get("status", "started"))
        if key not in LESSON_KEYS or status not in {"started", "completed"}:
            return jsonify({"ok": False, "error": "Leçon invalide."}), 400
        percent = 100 if status == "completed" else 10
        conn = get_conn()
        try:
            conn.run("""INSERT INTO academy_progress
                (member_code,lesson_key,status,progress_percent,started_at,completed_at,last_viewed_at)
                VALUES (:code,:key,:status,:percent,NOW(),CASE WHEN :status='completed' THEN NOW() ELSE NULL END,NOW())
                ON CONFLICT (member_code,lesson_key) DO UPDATE SET status=:status,
                progress_percent=GREATEST(academy_progress.progress_percent,:percent),
                completed_at=CASE WHEN :status='completed' THEN COALESCE(academy_progress.completed_at,NOW()) ELSE academy_progress.completed_at END,
                last_viewed_at=NOW()""", code=session["member_code"], key=key, status=status, percent=percent)
            completed = int(conn.run("""SELECT COUNT(*) FROM academy_progress
                WHERE member_code=:code AND status='completed'""", code=session["member_code"])[0][0])
            return jsonify({"ok": True, "completed": completed, "percent": round(completed / len(LESSON_KEYS) * 100)})
        finally:
            conn.close()

    @app.route("/api/academy/quiz", methods=["POST"])
    @login_required
    @academy_access_required
    def academy_quiz():
        data = request.get_json(silent=True) or {}
        phase_key = str(data.get("phase_key", ""))
        try:
            answer = int(data.get("answer"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Réponse invalide."}), 400
        phase = PHASES_BY_KEY.get(phase_key)
        if not phase or answer not in range(len(phase["quiz"]["answers"])):
            return jsonify({"ok": False, "error": "Quiz invalide."}), 400
        passed = answer == phase["quiz"]["correct"]
        score = 100 if passed else 0
        conn = get_conn()
        try:
            conn.run("""INSERT INTO academy_quiz_attempts
                (member_code,phase_key,score,passed,answers_json)
                VALUES (:code,:phase,:score,:passed,:answers)""", code=session["member_code"],
                phase=phase_key, score=score, passed=passed, answers=json.dumps({"answer": answer}))
        finally:
            conn.close()
        return jsonify({"ok": True, "passed": passed, "score": score,
                        "message": "Phase validée." if passed else "Revois la phase puis réessaie."})

    @app.route("/trader-lab")
    @login_required
    def trader_lab():
        code, member, demo_mode = _member_context(get_member, current_demo_mode)
        conn = get_conn()
        try:
            ensure_growth_schema(conn)
            score = _score_member(conn, code)
            journal_count = int(conn.run("SELECT COUNT(*) FROM trading_journal_entries WHERE member_code=:code", code=code)[0][0])
            simulator_count = int(conn.run("SELECT COUNT(*) FROM simulator_sessions WHERE member_code=:code", code=code)[0][0])
            analysis_count = int(conn.run("SELECT COUNT(*) FROM analysis_jobs WHERE member_code=:code AND status='completed'", code=code)[0][0])
        finally:
            conn.close()
        return render_template("trader_lab.html", member=member, demo_mode=demo_mode,
                               trade_score=score, journal_count=journal_count,
                               simulator_count=simulator_count, analysis_count=analysis_count)

    @app.route("/simulateur")
    @login_required
    def simulateur():
        code, member, demo_mode = _member_context(get_member, current_demo_mode)
        sessions = []
        if not demo_mode:
            conn = get_conn()
            try:
                rows = conn.run("""SELECT id,market,timeframe,direction,account_size,risk_pct,entry_price,stop_loss,
                    tp1,tp2,tp3,risk_amount,theoretical_units,rr1,rr2,rr3,outcome,outcome_r,created_at
                    FROM simulator_sessions WHERE member_code=:code ORDER BY created_at DESC LIMIT 12""", code=code)
                keys = ["id","market","timeframe","direction","account_size","risk_pct","entry_price","stop_loss",
                        "tp1","tp2","tp3","risk_amount","theoretical_units","rr1","rr2","rr3","outcome","outcome_r","created_at"]
                sessions = [dict(zip(keys, row)) for row in rows]
            finally:
                conn.close()
        return render_template("simulateur.html", member=member, demo_mode=demo_mode, sessions=sessions)

    @app.route("/api/simulateur", methods=["POST"])
    @login_required
    @academy_access_required
    def simulator_create():
        data = request.get_json(silent=True) or {}
        market = str(data.get("market", "XAU/USD")).upper()[:20]
        timeframe = str(data.get("timeframe", "M15"))[:8]
        direction = str(data.get("direction", "long")).lower()
        if direction not in {"long", "short"} or not re.fullmatch(r"[A-Z0-9][A-Z0-9/_.-]{1,19}", market):
            return jsonify({"ok": False, "error": "Configuration invalide."}), 400
        try:
            account = _safe_float(data.get("account_size"), 10, 100000000, True)
            risk_pct = _safe_float(data.get("risk_pct"), 0.1, 5, True)
            entry = _safe_float(data.get("entry_price"), 0.000001, None, True)
            stop = _safe_float(data.get("stop_loss"), 0.000001, None, True)
            targets = [_safe_float(data.get(key), 0.000001) for key in ("tp1", "tp2", "tp3")]
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Vérifie les montants et les niveaux de prix."}), 400
        distance = (entry - stop) if direction == "long" else (stop - entry)
        if distance <= 0:
            return jsonify({"ok": False, "error": "Le stop doit être sous l’entrée en LONG et au-dessus en SHORT."}), 400
        risk_amount = account * risk_pct / 100
        units = risk_amount / distance
        rrs = []
        for target in targets:
            if target is None:
                rrs.append(None)
                continue
            reward = (target - entry) if direction == "long" else (entry - target)
            rrs.append(round(reward / distance, 2) if reward > 0 else None)
        conn = get_conn()
        try:
            inserted = conn.run("""INSERT INTO simulator_sessions
                (member_code,market,timeframe,direction,account_size,risk_pct,entry_price,stop_loss,
                 tp1,tp2,tp3,risk_amount,theoretical_units,rr1,rr2,rr3,notes)
                VALUES (:code,:market,:timeframe,:direction,:account,:risk_pct,:entry,:stop,
                 :tp1,:tp2,:tp3,:risk_amount,:units,:rr1,:rr2,:rr3,:notes) RETURNING id""",
                code=session["member_code"], market=market, timeframe=timeframe, direction=direction,
                account=account, risk_pct=risk_pct, entry=entry, stop=stop, tp1=targets[0], tp2=targets[1],
                tp3=targets[2], risk_amount=risk_amount, units=units, rr1=rrs[0], rr2=rrs[1], rr3=rrs[2],
                notes=str(data.get("notes", ""))[:1500])
            session_id = int(inserted[0][0])
            return jsonify({"ok": True, "id": session_id, "risk_amount": round(risk_amount, 2),
                            "theoretical_units": round(units, 4), "rr1": rrs[0], "rr2": rrs[1], "rr3": rrs[2]})
        finally:
            conn.close()

    @app.route("/api/simulateur/<int:sim_id>/outcome", methods=["POST"])
    @login_required
    @academy_access_required
    def simulator_outcome(sim_id):
        data = request.get_json(silent=True) or {}
        outcome = str(data.get("outcome", "pending"))
        if outcome not in {"pending", "stop", "breakeven", "tp1", "tp2", "tp3"}:
            return jsonify({"ok": False, "error": "Résultat invalide."}), 400
        conn = get_conn()
        try:
            rows = conn.run("""SELECT rr1,rr2,rr3 FROM simulator_sessions
                WHERE id=:id AND member_code=:code""", id=sim_id, code=session["member_code"])
            if not rows:
                return jsonify({"ok": False, "error": "Simulation introuvable."}), 404
            mapping = {"stop": -1, "breakeven": 0, "tp1": rows[0][0], "tp2": rows[0][1], "tp3": rows[0][2], "pending": None}
            outcome_r = mapping[outcome]
            conn.run("""UPDATE simulator_sessions SET outcome=:outcome,outcome_r=:outcome_r,
                closed_at=CASE WHEN :outcome='pending' THEN NULL ELSE NOW() END
                WHERE id=:id AND member_code=:code""", outcome=outcome, outcome_r=outcome_r,
                id=sim_id, code=session["member_code"])
            return jsonify({"ok": True, "outcome_r": float(outcome_r) if outcome_r is not None else None})
        finally:
            conn.close()

    @app.route("/journal/manual")
    @login_required
    def journal():
        code, member, demo_mode = _member_context(get_member, current_demo_mode)
        entries = []
        stats = {"count": 0, "average_r": 0, "discipline": 0, "plan_rate": 0}
        if not demo_mode:
            conn = get_conn()
            try:
                rows = conn.run("""SELECT id,market,timeframe,direction,setup,risk_pct,result_r,
                    emotion_before,emotion_after,plan_followed,mistake,notes,intelligent_review,trade_date
                    FROM trading_journal_entries WHERE member_code=:code ORDER BY trade_date DESC LIMIT 40""", code=code)
                keys = ["id","market","timeframe","direction","setup","risk_pct","result_r","emotion_before",
                        "emotion_after","plan_followed","mistake","notes","intelligent_review","trade_date"]
                entries = [dict(zip(keys, row)) for row in rows]
                results = [float(item["result_r"]) for item in entries if item["result_r"] is not None]
                stats = {"count": len(entries), "average_r": round(sum(results) / len(results), 2) if results else 0,
                         "discipline": sum(1 for item in entries if item["plan_followed"] and 0 < float(item["risk_pct"] or 0) <= 1),
                         "plan_rate": round(100 * sum(1 for item in entries if item["plan_followed"]) / len(entries)) if entries else 0}
            finally:
                conn.close()
        return render_template("journal.html", member=member, demo_mode=demo_mode, entries=entries, stats=stats)

    @app.route("/api/journal", methods=["POST"])
    @login_required
    @academy_access_required
    def journal_create():
        data = request.get_json(silent=True) or {}
        market = str(data.get("market", "XAU/USD")).upper()[:20]
        direction = str(data.get("direction", "observation")).lower()
        if direction not in {"long", "short", "observation"} or not re.fullmatch(r"[A-Z0-9][A-Z0-9/_.-]{1,19}", market):
            return jsonify({"ok": False, "error": "Marché ou direction invalide."}), 400
        try:
            numeric = {key: _safe_float(data.get(key), 0 if key == "risk_pct" else None, 5 if key == "risk_pct" else None)
                       for key in ("entry_price", "stop_loss", "tp1", "tp2", "tp3", "exit_price", "risk_pct", "result_r")}
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Un niveau de prix est invalide."}), 400
        clean = {
            "risk_pct": numeric["risk_pct"] or 0, "result_r": numeric["result_r"],
            "plan_followed": bool(data.get("plan_followed")), "context": str(data.get("context", ""))[:2500],
            "emotion_before": str(data.get("emotion_before", ""))[:40],
        }
        review = _journal_review(clean)
        conn = get_conn()
        try:
            inserted = conn.run("""INSERT INTO trading_journal_entries
                (member_code,market,timeframe,direction,setup,context,entry_price,stop_loss,tp1,tp2,tp3,
                 exit_price,risk_pct,result_r,emotion_before,emotion_after,plan_followed,mistake,notes,intelligent_review)
                VALUES (:code,:market,:timeframe,:direction,:setup,:context,:entry,:stop,:tp1,:tp2,:tp3,
                 :exit,:risk_pct,:result_r,:emotion_before,:emotion_after,:plan_followed,:mistake,:notes,:review)
                RETURNING id""", code=session["member_code"], market=market,
                timeframe=str(data.get("timeframe", "M15"))[:8], direction=direction,
                setup=str(data.get("setup", ""))[:120], context=clean["context"], entry=numeric["entry_price"],
                stop=numeric["stop_loss"], tp1=numeric["tp1"], tp2=numeric["tp2"], tp3=numeric["tp3"],
                exit=numeric["exit_price"], risk_pct=clean["risk_pct"], result_r=clean["result_r"],
                emotion_before=clean["emotion_before"], emotion_after=str(data.get("emotion_after", ""))[:40],
                plan_followed=clean["plan_followed"], mistake=str(data.get("mistake", ""))[:600],
                notes=str(data.get("notes", ""))[:2500], review=review)
            score = _score_member(conn, session["member_code"], persist=True)
            return jsonify({"ok": True, "id": int(inserted[0][0]), "review": review, "trade_score": score})
        finally:
            conn.close()

    @app.route("/api/journal/<int:entry_id>", methods=["DELETE"])
    @login_required
    @academy_access_required
    def journal_delete(entry_id):
        conn = get_conn()
        try:
            deleted = conn.run("""DELETE FROM trading_journal_entries
                WHERE id=:id AND member_code=:code RETURNING id""", id=entry_id, code=session["member_code"])
            return jsonify({"ok": bool(deleted)})
        finally:
            conn.close()

    @app.route("/trade-score")
    @login_required
    def trade_score():
        code, member, demo_mode = _member_context(get_member, current_demo_mode)
        conn = get_conn()
        try:
            score = _score_member(conn, code, persist=not demo_mode)
            history_rows = conn.run("""SELECT total_score,rank_label,created_at FROM trade_score_snapshots
                WHERE member_code=:code ORDER BY created_at DESC LIMIT 12""", code=code) if not demo_mode else []
            history = [{"score": int(row[0]), "rank": row[1], "date": row[2]} for row in history_rows]
        finally:
            conn.close()
        return render_template("trade_score.html", member=member, demo_mode=demo_mode,
                               score=score, history=history)

    @app.route("/centre-confiance")
    @login_required
    def trust_center():
        code, member, demo_mode = _member_context(get_member, current_demo_mode)
        conn = get_conn()
        try:
            ensure_growth_schema(conn)
            metrics = {
                "analyses": int(conn.run("SELECT COUNT(*) FROM analysis_jobs WHERE status='completed'")[0][0]),
                "failures": int(conn.run("SELECT COUNT(*) FROM analysis_jobs WHERE status='failed'")[0][0]),
                "feedbacks": int(conn.run("SELECT COUNT(*) FROM analysis_feedback")[0][0]),
                "saved": int(conn.run("SELECT COUNT(*) FROM analysis_feedback WHERE saved_to_journal=TRUE")[0][0]),
            }
            averages = conn.run("SELECT AVG(usefulness),AVG(clarity) FROM analysis_feedback WHERE usefulness>0 OR clarity>0")[0]
            metrics["usefulness"] = round(float(averages[0] or 0), 1)
            metrics["clarity"] = round(float(averages[1] or 0), 1)
            total = metrics["analyses"] + metrics["failures"]
            metrics["completion_rate"] = round(100 * metrics["analyses"] / total, 1) if total else 0
        finally:
            conn.close()
        return render_template("centre_confiance.html", member=member, demo_mode=demo_mode, metrics=metrics)

    @app.route("/api/analyse-ia/feedback", methods=["POST"])
    @login_required
    def analysis_feedback():
        data = request.get_json(silent=True) or {}
        job_id = str(data.get("job_id", ""))
        outcome = str(data.get("outcome_status", "pending"))
        if outcome not in {"pending", "no_trade", "invalidated", "stop", "tp1", "tp2", "tp3"}:
            return jsonify({"ok": False, "error": "Résultat invalide."}), 400
        try:
            usefulness = int(data.get("usefulness", 0) or 0)
            clarity = int(data.get("clarity", 0) or 0)
            outcome_r = _safe_float(data.get("outcome_r"), -20, 50)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Évaluation invalide."}), 400
        if usefulness not in range(0, 6) or clarity not in range(0, 6):
            return jsonify({"ok": False, "error": "La note doit être comprise entre 1 et 5."}), 400
        conn = get_conn()
        try:
            owned = conn.run("SELECT 1 FROM analysis_jobs WHERE id=:job AND member_code=:code", job=job_id, code=session["member_code"])
            if not owned:
                return jsonify({"ok": False, "error": "Analyse introuvable."}), 404
            conn.run("""INSERT INTO analysis_feedback
                (job_id,member_code,usefulness,clarity,outcome_status,outcome_r,notes)
                VALUES (:job,:code,:usefulness,:clarity,:outcome,:outcome_r,:notes)
                ON CONFLICT (job_id) DO UPDATE SET usefulness=:usefulness,clarity=:clarity,
                outcome_status=:outcome,outcome_r=:outcome_r,notes=:notes,updated_at=NOW()""",
                job=job_id, code=session["member_code"], usefulness=usefulness, clarity=clarity,
                outcome=outcome, outcome_r=outcome_r, notes=str(data.get("notes", ""))[:1000])
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/analyse-ia/to-journal", methods=["POST"])
    @login_required
    @academy_access_required
    def analysis_to_journal():
        data = request.get_json(silent=True) or {}
        job_id = str(data.get("job_id", ""))
        conn = get_conn()
        try:
            rows = conn.run("""SELECT market,timeframe,result_json FROM analysis_jobs
                WHERE id=:job AND member_code=:code AND status='completed'""", job=job_id, code=session["member_code"])
            if not rows:
                return jsonify({"ok": False, "error": "Analyse introuvable."}), 404
            market, timeframe, raw = rows[0]
            result = json.loads(raw or "{}")
            plan = next((item for item in result.get("plans", []) if item.get("statut") == "VALIDE"),
                        (result.get("plans") or [{}])[0])
            direction_text = str(plan.get("direction", "")).lower()
            direction = "long" if any(word in direction_text for word in ("achat", "long", "hauss")) else ("short" if any(word in direction_text for word in ("vente", "short", "baiss")) else "observation")
            review_data = {"risk_pct": 0, "result_r": None, "plan_followed": False,
                           "context": result.get("resume", ""), "emotion_before": ""}
            review = _journal_review(review_data)
            inserted = conn.run("""INSERT INTO trading_journal_entries
                (member_code,analysis_job_id,market,timeframe,direction,setup,context,
                 entry_price,stop_loss,tp1,tp2,tp3,plan_snapshot,intelligent_review)
                VALUES (:code,:job,:market,:timeframe,:direction,:setup,:context,
                 :entry,:stop,:tp1,:tp2,:tp3,:snapshot,:review)
                ON CONFLICT DO NOTHING RETURNING id""",
                code=session["member_code"], job=job_id, market=market, timeframe=timeframe,
                direction=direction, setup=str(plan.get("declencheur", ""))[:120],
                context=str(result.get("resume", ""))[:2500], entry=_num_from_text(plan.get("entree")),
                stop=_num_from_text(plan.get("stop_loss") or plan.get("invalidation")),
                tp1=_num_from_text(plan.get("objectif_1")), tp2=_num_from_text(plan.get("objectif_2")),
                tp3=_num_from_text(plan.get("objectif_3")), snapshot=json.dumps(plan, ensure_ascii=False), review=review)
            conn.run("""INSERT INTO analysis_feedback (job_id,member_code,saved_to_journal)
                VALUES (:job,:code,TRUE) ON CONFLICT (job_id) DO UPDATE SET saved_to_journal=TRUE,updated_at=NOW()""",
                job=job_id, code=session["member_code"])
            return jsonify({"ok": True, "created": bool(inserted), "journal_url": "/journal"})
        finally:
            conn.close()

    @app.route("/admin/intelligence")
    @admin_required
    def admin_intelligence():
        conn = get_conn()
        try:
            ensure_growth_schema(conn)
            paid_filter = "COALESCE(m.access_level,'member') NOT IN ('explorer','demo')"
            active_paid_filter = f"{paid_filter} AND m.actif=TRUE AND (m.date_fin IS NULL OR m.date_fin>NOW())"
            kpis = {
                "paid": int(conn.run(f"SELECT COUNT(*) FROM members m WHERE {active_paid_filter}")[0][0]),
                "explorers": int(conn.run("SELECT COUNT(*) FROM members WHERE COALESCE(access_level,'member') IN ('explorer','demo') AND code<>'BCT-DEMO2026'")[0][0]),
                "active_7d": int(conn.run(f"SELECT COUNT(*) FROM members m WHERE {paid_filter} AND m.last_login>NOW()-INTERVAL '7 days'")[0][0]),
                "at_risk": int(conn.run(f"SELECT COUNT(*) FROM members m WHERE {paid_filter} AND m.actif=TRUE AND (m.last_login IS NULL OR m.last_login<NOW()-INTERVAL '7 days')")[0][0]),
                "academy_users": int(conn.run("SELECT COUNT(DISTINCT member_code) FROM academy_progress")[0][0]),
                "journal_users": int(conn.run("SELECT COUNT(DISTINCT member_code) FROM trading_journal_entries")[0][0]),
                "simulator_users": int(conn.run("SELECT COUNT(DISTINCT member_code) FROM simulator_sessions")[0][0]),
                "analysis_users": int(conn.run("SELECT COUNT(DISTINCT member_code) FROM analysis_jobs WHERE status='completed' AND member_code LIKE 'BCT-%'")[0][0]),
            }
            push_row = conn.run(f"""SELECT COUNT(ps.id),COUNT(DISTINCT ps.member_code),
                COUNT(ps.id) FILTER (WHERE ps.failure_count>0),MAX(ps.last_delivery_at)
                FROM push_subscriptions ps JOIN members m ON m.code=ps.member_code
                WHERE {active_paid_filter}""")[0]
            last_vip_push = conn.run("SELECT MAX(push_notified_at) FROM canal_messages")[0][0]
            push_health = {
                "devices": int(push_row[0] or 0),
                "members": int(push_row[1] or 0),
                "failing": int(push_row[2] or 0),
                "last_delivery": push_row[3],
                "last_vip_push": last_vip_push,
                "coverage": round(100 * int(push_row[1] or 0) / kpis["paid"]) if kpis["paid"] else 0,
            }
            at_risk = conn.run(f"""SELECT m.code,m.nom,m.email,m.last_login,m.date_fin
                FROM members m WHERE {paid_filter} AND m.actif=TRUE
                AND (m.last_login IS NULL OR m.last_login<NOW()-INTERVAL '7 days')
                ORDER BY m.last_login NULLS FIRST LIMIT 20""")
            expiring = conn.run(f"""SELECT m.code,m.nom,m.email,m.date_fin
                FROM members m WHERE {paid_filter} AND m.actif=TRUE
                AND m.date_fin BETWEEN NOW() AND NOW()+INTERVAL '7 days' ORDER BY m.date_fin LIMIT 20""")
            explorers = conn.run("""SELECT code,nom,email,created_at,last_login FROM members
                WHERE COALESCE(access_level,'member') IN ('explorer','demo') AND code<>'BCT-DEMO2026'
                ORDER BY created_at DESC LIMIT 20""")
            champions = conn.run("""SELECT DISTINCT ON (s.member_code) s.member_code,m.nom,s.total_score,s.rank_label,s.created_at
                FROM trade_score_snapshots s JOIN members m ON m.code=s.member_code
                ORDER BY s.member_code,s.created_at DESC""")
            champions = sorted(champions, key=lambda row: int(row[2]), reverse=True)[:20]
        finally:
            conn.close()
        return render_template("admin_intelligence.html", kpis=kpis, at_risk=at_risk,
                               expiring=expiring, explorers=explorers, champions=champions,
                               push_health=push_health)
