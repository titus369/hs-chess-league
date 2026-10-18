from flask import Blueprint, flash, redirect, render_template, request, url_for

from . import db
from .models import School, Team, Tournament
from .standings import board_standings, team_standings
bp = Blueprint("main", __name__)

def get_or_create_school(name):
    name = " ".join((name or "").split())
    if not name:
        return None
    school = School.query.filter(db.func.lower(School.name) == name.lower()).first()
    if not school:
        school = School(name=name)
        db.session.add(school)
        db.session.flush()
    return school

@bp.get("/")
def index():
    tournaments = Tournament.query.order_by(Tournament.created_at.desc()).all()
    return render_template("index.html", tournaments=tournaments)

@bp.route("/tournaments/new", methods=["GET", "POST"])
def tournament_new():
    if request.method == "POST":
        name = request.form.get("name", ""). strip()
        try:
            num_rounds = int(request.form.get("num_rounds", 7))
        except ValueError:
            num_rounds = 0
        if not name:
            flash("Give the tournament a name.")
        elif num_rounds < 1:
            flash("Rounds must be at least 1.")
        else:
            t = Tournament(name=name, num_rounds = num_rounds)
            db.session.add(t)
            db.session.commit()
            return redirect(url_for("main.tournament", tournament_id = t.id))
    return render_template("tournament_new.html")

@bp.get("/tournaments/<int:tournament_id>")
def tournament(tournament_id):
    t = db.get_or_404(Tournament, tournament_id)
    return render_template(
        "tournament.html",
        t = t,
        standings = team_standings(t), 
        schools = School.query.order_by(School.name).all(), )

def to_int_or_none(value):
    value = (value or "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise ValueError(value)
    return int(value)

@bp.post("/tournaments/<int:tournament_id>/teams")
def add_team(tournament_id):
    t = db.get_or_404(Tournament, tournament_id)
    back = redirect(url_for("main.tournament", tournament_id=t.id))

    school_name = request.form.get("school","").strip()
    names = [value.strip() for value in request.form.getlist("name")]
    lichess_names = [value.strip() for value in request.form.getlist("lichess_username")]
    uscf_ids = [value.strip() for value in request.form.getlist("uscf_id")]
    ratings = [value.strip() for value in request.form.getlist("rating")]

    if not school:
        flash("Enter a school name.")
        return back

    if (len(names) != BOARDS or len(lichess_names) != BOARDS or not all(names) or not all(lichess_names)):
        flash("Enter a name and Lichess username for all 4 core players.")
        return back

    try:
        uscf_ids = [to_int_or_none(v) for v in request.form.getlist("uscf_id")]
        ratings = [to_int_or_none(v) for v in request.form.getlist("rating")]
    except ValueError:
        flash("USCF ID and rating must be numbers, or left blank.")
        return back
    if (len(uscf_ids) != BOARDS or len(ratings) != BOARDS):
        flash("Error with uscf id's and ratings")
        return back

    school = get_or_create_school(school_name)
    existing = [x.team_number for x in t.teams if x.school_id == school.id]
    team = Team(tournament = t, school = school, team_number=max(existing, default=0) + 1)
    db.session.add(team)

    for board in range(1, BOARDS + 1):
        i = board - 1
        db.session.add(Player(name=names[i], lichess_username=lichess_names[i], uscf_id=uscf_ids[i], rating=ratings[i], tournament=t, school=school, is_core=True, core_team=team, board=board))
    db.session.commit()
    flash(f" Added {team.name} with {BOARDS} core players.")
    return back

@bp.get("/tournaments/<int:tournament_id>/boards")
def boards(tournament_id):
    t = db.get_or_404(Tournament, tournament_id)
    return render_template("boards.html", t=t, by_board=board_standings(t))
        