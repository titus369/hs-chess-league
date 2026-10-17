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

@bp.post("/tournaments/<int:tournament_id>/teams")
def add_team(tournament_id):
    t = db.get_or_404(Tournament, tournament_id)

    player_names = [value.strip() for value in request.form.getlist("name")]
    lichess_names = [value.strip() for value in request.form.getlist("lichess_username")]
    uscf_ids = [value.strip() for value in request.form.getlist("uscf_id")]
    ratings = [value.strip() for value in request.form.getlist("rating")]

    school = get_or_create_school(request.form.get("school"))
    if not school:
        flash("Enter a school name.")
        return redirect(url_for("main.tournament", tournament_id = t.id))

    existing = [x.team_number for x in t.teams if x.school_id == school.id]
    team = Team(tournament = t, school = school, team_number=max(existing, default = 0) + 1)
    db.session.add(team)
    db.session.commit()
    flash(f" Added {team.name}.")
    return redirect(url_for("main.tournament", tournament_id=t.id))

@bp.get("/tournaments/<int:tournament_id>/boards")
def boards(tournament_id):
    t = db.get_or_404(Tournament, tournament_id)
    return render_template("boards.html", t=t, by_board=board_standings(t))
        