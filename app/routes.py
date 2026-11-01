from flask import Blueprint, flash, redirect, render_template, request, url_for, abort

from . import db
from .models import BOARDS, Player, RosterEntry, School, Team, Tournament
from .rosters import roster_report, seeded_teams
from .standings import board_standings, team_standings
from .pairings import PairingError, create_round, round_byes, round_pairings, set_round_pairings
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

@bp.get("/tournaments/<int:tournament_id>/teams/<int:team_id>/roster")
def team_roster(tournament_id, team_id):
    t = db.get_or_404(Tournament, tournament_id)
    team = Team.query.filter_by(id=team_id, tournament_id=t.id).first_or_404()
    roster = next(
        roster for roster in roster_report(t.id)
        if roster.team_id == team.id
    )
    return render_template("team_roster.html", roster=roster)

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

    school = request.form.get("school","").strip()
    names = [value.strip() for value in request.form.getlist("name")]
    lichess_names = [value.strip() for value in request.form.getlist("lichess_username")]
    uscf_ids = [value.strip() for value in request.form.getlist("uscf_id")]
    ratings = [value.strip() for value in request.form.getlist("rating")]

    if not school:
        flash("Enter a school name.")
        return back

    if not (len(names) == BOARDS
        and len(lichess_names) == BOARDS
        and len(uscf_ids) == BOARDS
        and len(ratings) == BOARDS
        and all(names)
        and all(lichess_names)):
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

    try:
        school = get_or_create_school(school)
        existing = [x.team_number for x in t.teams if x.school_id == school.id]
        team = Team(tournament = t, school = school, team_number=max(existing, default=0) + 1)
        db.session.add(team)

        for index in range(BOARDS):
            player = Player(
                tournament=t,
                school=school,
                name=names[index],
                lichess_username=lichess_names[index],
                uscf_id=uscf_ids[index],
                rating=ratings[index]
            )
            entry = RosterEntry(
                team=team,
                player=player,
                position=float(index + 1),
                is_core=True
            )
            db.session.add_all([player, entry])
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    flash(f" Added {team.name} with {BOARDS} core players.")
    return back

@bp.get("/tournaments/<int:tournament_id>/boards")
def boards(tournament_id):
    t = db.get_or_404(Tournament, tournament_id)
    return render_template("boards.html", t=t, by_board=board_standings(t))
    
#Round Routes
def to_team_id(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() else None
 
 
@bp.route("/tournaments/<int:tournament_id>/rounds/<int:number>", methods=["GET", "POST"])
def round_page(tournament_id, number):
    t = db.get_or_404(Tournament, tournament_id)
    if not 1 <= number <= t.num_rounds:
        abort(404)
 
    if request.method == "POST":
        try:
            if request.form.get("action") == "create":
                create_round(t, number, to_team_id(request.form.get("num_tables")))
                warnings = []
                db.session.commit()
                flash(f"Round {number} created.")
            else:
                slots = {
                    m.table_number: (
                        to_team_id(request.form.get(f"home_{m.table_number}")),
                        to_team_id(request.form.get(f"away_{m.table_number}")),
                    )
                    for m in round_pairings(t, number)
                }
                byes = []
                i = 1
                while f"bye_team_{i}" in request.form:
                    byes.append((to_team_id(request.form.get(f"bye_team_{i}")),
                                 request.form.get(f"bye_type_{i}", "")))
                    i += 1
                warnings = set_round_pairings(t, number, slots, byes)
                db.session.commit()
                flash("Pairings saved.")
        except PairingError as err:
            db.session.rollback()
            flash(str(err))
        else:
            for w in warnings:
                flash("Warning: " + w)
        return redirect(url_for("main.round_page", tournament_id=t.id, number=number))
 
    matches = round_pairings(t, number)
    bye_rows = [{"team_id": b.team_id, "bye_type": b.bye_type} for b in round_byes(t, number)]
    bye_rows += [{"team_id": None, "bye_type": "half"}] * max(1, 3 - len(bye_rows))
    return render_template(
        "round.html",
        t=t,
        number=number,
        seeds=seeded_teams(t.id),
        matches=matches,
        bye_rows=bye_rows,
        default_tables=len(t.teams) // 2,
    )