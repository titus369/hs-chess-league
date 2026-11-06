from flask import Blueprint, flash, redirect, render_template, request, url_for, abort
from sqlalchemy import and_, or_

from . import db
from .models import BOARDS, MAX_HALF_BYES, Bye, Game, Match, Player, RosterEntry, Round, School, Team, Tournament
from .rosters import roster_report, seeded_teams
from .standings import board_standings, team_standings
from .pairings import PairingError, apply_generated_pairings, clear_round, create_round, get_round, resize_round, round_byes, round_pairings, set_round_pairings
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
    return render_template("team_roster.html", t=t, roster=roster)

@bp.get("/tournaments/<int:tournament_id>/teams/<int:team_id>/history")
def team_history(tournament_id, team_id):
    t = db.get_or_404(Tournament, tournament_id)
    team = Team.query.filter_by(id=team_id, tournament_id=t.id).first_or_404()
    rounds_by_number = {rd.number: rd for rd in t.rounds}
    history = []

    for number in range(1, t.num_rounds + 1):
        rd = rounds_by_number.get(number)
        match = next(
            (
                match for match in rd.matches
                if team.id in (match.home_team_id, match.away_team_id)
            ),
            None,
        ) if rd else None
        bye = next(
            (bye for bye in rd.byes if bye.team_id == team.id),
            None,
        ) if rd else None

        if match:
            is_home = match.home_team_id == team.id
            opponent = match.away_team if is_home else match.home_team
            team_points = match.home_game_points if is_home else match.away_game_points
            opponent_points = match.away_game_points if is_home else match.home_game_points
            history.append({
                "number": number,
                "match": match,
                "opponent": opponent,
                "is_home": is_home,
                "team_points": team_points,
                "opponent_points": opponent_points,
                "complete": match.is_complete,
                "bye": None,
            })
        else:
            history.append({
                "number": number,
                "match": None,
                "opponent": None,
                "is_home": None,
                "team_points": None,
                "opponent_points": None,
                "complete": False,
                "bye": bye,
            })

    return render_template("team_history.html", t=t, team=team, history=history)

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
    ratings = [value.strip() for value in request.form.getlist("rating")]

    if not school:
        flash("Enter a school name.")
        return back

    bye_requests = []
    half_bye_count = 0
    for round_number in range(1, t.num_rounds + 1):
        bye_type = request.form.get(f"bye_round_{round_number}", "")
        if bye_type not in ("", "half", "zero"):
            flash(f"Invalid bye request for round {round_number}.")
            return back
        if bye_type:
            bye_requests.append((round_number, bye_type))
        if bye_type == "half":
            half_bye_count += 1
    if half_bye_count > MAX_HALF_BYES:
        flash(f"A team may request at most {MAX_HALF_BYES} half-point byes.")
        return back

    if not (len(names) == BOARDS
        and len(lichess_names) == BOARDS
        and len(ratings) == BOARDS
        and all(names)
        and all(lichess_names)):
        flash("Enter a name and Lichess username for all 4 core players.")
        return back

    try:
        ratings = [to_int_or_none(v) for v in request.form.getlist("rating")]
    except ValueError:
        flash("Ratings must be numbers, or left blank.")
        return back
    if len(ratings) != BOARDS:
        flash("Enter a rating for each core player, or leave it blank.")
        return back

    try:
        school = get_or_create_school(school)
        existing = [x.team_number for x in t.teams if x.school_id == school.id]
        team = Team(tournament = t, school = school, team_number=max(existing, default=0) + 1)
        db.session.add(team)
        db.session.flush()

        for index in range(BOARDS):
            player = Player(
                tournament=t,
                school=school,
                name=names[index],
                lichess_username=lichess_names[index],
                rating=ratings[index]
            )
            entry = RosterEntry(
                team=team,
                player=player,
                position=float(index + 1),
                is_core=True
            )
            db.session.add_all([player, entry])

        for round_number, bye_type in bye_requests:
            rd = Round.query.filter_by(
                tournament_id=t.id,
                number=round_number,
            ).first()
            if rd is None:
                rd = Round(tournament_id=t.id, number=round_number)
                db.session.add(rd)
                db.session.flush()
            db.session.add(Bye(
                round=rd,
                team=team,
                bye_type=bye_type,
                source="requested",
            ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    flash(f" Added {team.name} with {BOARDS} core players.")
    return back

@bp.route(
    "/tournaments/<int:tournament_id>/teams/<int:team_id>/edit",
    methods=["GET", "POST"],
)
def edit_team(tournament_id, team_id):
    tournament = db.get_or_404(Tournament, tournament_id)
    team = Team.query.filter_by(
        id=team_id,
        tournament_id=tournament.id,
    ).first_or_404()

    existing_entries = {
        f"e_{entry.id}": entry
        for entry in team.roster
    }
    new_row_keys = {f"n_{number}" for number in range(1, 4)}
    current_player_ids = {entry.player_id for entry in team.roster}
    school_players = (
        Player.query.filter_by(tournament_id=tournament.id, school_id=team.school_id)
        .order_by(Player.name, Player.id)
        .all()
    )

    if request.method == "POST":
        def return_to_edit():
            return redirect(url_for(
                "main.edit_team",
                tournament_id=tournament.id,
                team_id=team.id,
            ))

        school_name = request.form.get("school", "").strip()
        if not school_name:
            flash("Enter a school name.")
            return return_to_edit()
        target_school = School.query.filter(
            db.func.lower(School.name) == school_name.lower()
        ).first()

        row_keys = request.form.getlist("row")
        allowed_keys = set(existing_entries) | new_row_keys
        if len(row_keys) != len(set(row_keys)) or not set(row_keys) <= allowed_keys:
            flash("Invalid roster form submission.")
            return return_to_edit()

        planned_rows = []

        for key in row_keys:
            is_new = key in new_row_keys
            is_core = request.form.get(f"{key}_core") == "1"
            remove = request.form.get(f"{key}_remove") == "1"

            if remove:
                continue

            name = request.form.get(f"{key}_name", "").strip()
            username = request.form.get(f"{key}_username", "").strip()
            rating_value = request.form.get(f"{key}_rating", "").strip()
            raw_player_id = request.form.get(f"{key}_player_id", "").strip() if is_new else ""

            # An untouched blank row is ignored; partially filled rows are invalid.
            if is_new and not any((name, username, rating_value, is_core, raw_player_id)):
                continue

            if not name or not username:
                if not raw_player_id:
                    flash("Every new player needs a name and Lichess username, or select an existing player.")
                    return return_to_edit()

            try:
                rating = to_int_or_none(rating_value)
            except ValueError:
                flash("Ratings must be numbers, or left blank.")
                return return_to_edit()

            entry = existing_entries.get(key)
            player_id = entry.player_id if entry else None
            selected_player_id = None
            selected_player = None
            if is_new:
                if raw_player_id:
                    if not raw_player_id.isdigit():
                        flash("Choose a valid existing player.")
                        return return_to_edit()
                    selected_player_id = int(raw_player_id)
                    selected_player = next(
                        (player for player in school_players if player.id == selected_player_id),
                        None,
                    )
                    if (
                        selected_player is None
                        or target_school is None
                        or selected_player.school_id != target_school.id
                        or selected_player_id in current_player_ids
                    ):
                        flash("Choose an existing player from this school's roster who is not already on this team.")
                        return return_to_edit()
                    player_id = selected_player.id

            if player_id is not None:
                player = entry.player if entry else selected_player
                current_core_team = player.core_team
                if current_core_team and current_core_team.id != team.id:
                    if (
                        target_school
                        and current_core_team.school_id == target_school.id
                        and current_core_team.team_number < team.team_number
                    ):
                        flash(f"{current_core_team.name}'s core player cannot join the lower team {team.name}.")
                        return return_to_edit()
                    if is_core:
                        flash("A player already core on another team can only be added as a substitute.")
                        return return_to_edit()

            planned_rows.append({
                "key": key,
                "entry": entry,
                "is_new": is_new,
                "selected_player_id": selected_player_id,
                "selected_player": selected_player,
                "player_id": player_id,
                "is_core": is_core,
                "name": name,
                "username": username,
                "rating": rating,
            })

        core_count = sum(row["is_core"] for row in planned_rows)
        if core_count != BOARDS:
            flash(f"A team must have exactly {BOARDS} core players.")
            return return_to_edit()
        roster_player_ids = [
            row["player_id"] for row in planned_rows
            if row["player_id"] is not None
        ]
        if len(roster_player_ids) != len(set(roster_player_ids)):
            flash("A player can only appear once on this team roster.")
            return return_to_edit()

        retained_keys = {row["key"] for row in planned_rows}
        for key, entry in existing_entries.items():
            if key in retained_keys:
                continue
            assigned_game = (
                Game.query.join(Match, Game.match_id == Match.id)
                .filter(or_(
                    and_(
                        Match.home_team_id == team.id,
                        Game.home_player_id == entry.player_id,
                    ),
                    and_(
                        Match.away_team_id == team.id,
                        Game.away_player_id == entry.player_id,
                    ),
                ))
                .first()
            )
            if assigned_game:
                flash(f"{entry.player.name} is already assigned in a {team.name} lineup and cannot be removed.")
                return return_to_edit()

        school = get_or_create_school(school_name)
        team.school = school

        # Demote first to release the one-core-team-per-player database constraint.
        for entry in existing_entries.values():
            entry.is_core = False
        db.session.flush()

        for key, entry in existing_entries.items():
            if key not in retained_keys:
                db.session.delete(entry)
        db.session.flush()

        entries_to_update = []
        for position, row in enumerate(planned_rows, start=1):
            entry = row["entry"]

            if row["is_new"]:
                if row["selected_player_id"]:
                    player = row["selected_player"]
                else:
                    player = Player(
                        tournament=tournament,
                        school=school,
                        name=row["name"],
                        lichess_username=row["username"],
                        rating=row["rating"],
                    )
                entry = RosterEntry(
                    team=team,
                    player=player,
                    position=float(position),
                    is_core=False,
                )
                db.session.add(entry)
            else:
                entry.player.name = row["name"]
                entry.player.lichess_username = row["username"]
                entry.player.rating = row["rating"]
                entry.position = float(position)

            entries_to_update.append((entry, row["is_core"]))

        db.session.flush()

        for entry, is_core in entries_to_update:
            entry.is_core = is_core

        db.session.commit()
        flash("Team and roster updated.")
        return redirect(url_for(
            "main.team_roster",
            tournament_id=tournament.id,
            team_id=team.id,
        ))

    return render_template(
        "edit_team.html",
        team=team,
        entries=team.roster,
        new_rows=range(1, 4),
        school_players=[
            player for player in school_players
            if player.id not in current_player_ids
            and not (
                player.core_team
                and player.core_team.school_id == team.school_id
                and player.core_team.team_number < team.team_number
            )
        ],
    )

@bp.get("/tournaments/<int:tournament_id>/boards")
def boards(tournament_id):
    t = db.get_or_404(Tournament, tournament_id)
    return render_template("boards.html", t=t, by_board=board_standings(t))
    
#Round Routes
def to_team_id(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def to_table_count(value):
    value = (value or "").strip()
    if not value.isdigit() or int(value) < 1:
        raise PairingError("Enter a table count of at least 1.")
    return int(value)


def save_match_lineup(match):
    if match.home_team is None or match.away_team is None:
        raise PairingError("Assign both teams before entering player lineups.")
    if len(match.games) != BOARDS:
        raise PairingError(f"A match lineup must have exactly {BOARDS} boards.")

    selected_players = []
    assignments = []
    scores = []
    for side, team in (("home", match.home_team), ("away", match.away_team)):
        roster_player_ids = {entry.player_id for entry in team.roster}
        for game in match.games:
            raw_player_id = request.form.get(
                f"{side}_player_{game.id}", ""
            ).strip()
            if raw_player_id and not raw_player_id.isdigit():
                raise PairingError("Choose a valid roster player for each board.")

            player_id = int(raw_player_id) if raw_player_id else None
            if player_id is None:
                raise PairingError(
                    f"{side.title()} board {game.board}: select a player from {team.name}'s roster."
                )
            if player_id not in roster_player_ids:
                raise PairingError(
                    f"{side.title()} board {game.board}: choose a player from {team.name}'s roster."
                )

            old_player_id = getattr(game, f"{side}_player_id")
            if (
                player_id != old_player_id
                and (game.home_score is not None or game.away_score is not None)
            ):
                raise PairingError(
                    f"Board {game.board} already has a result, so its lineup can't be changed."
                )
            assignments.append((game, side, player_id))
            if player_id is not None:
                selected_players.append(player_id)

    for game in match.games:
        result = request.form.get(f"result_{game.id}", "")
        if result not in ("", "home", "draw", "away"):
            raise PairingError(f"Board {game.board}: choose a valid result.")
        if result == "home":
            scores.append((game, 1.0, 0.0))
        elif result == "draw":
            scores.append((game, 0.5, 0.5))
        elif result == "away":
            scores.append((game, 0.0, 1.0))
        else:
            scores.append((game, None, None))

    if len(selected_players) != len(set(selected_players)):
        raise PairingError("A player can only be assigned to one board in this match.")

    players_in_other_matches = {
        player_id
        for other_match in match.round.matches
        if other_match.id != match.id
        for game in other_match.games
        for player_id in (game.home_player_id, game.away_player_id)
        if player_id is not None
    }
    if players_in_other_matches.intersection(selected_players):
        raise PairingError("A player can only be assigned to one match in a round.")

    roster_players = {
        side: {entry.player_id: entry.player for entry in team.roster}
        for side, team in (("home", match.home_team), ("away", match.away_team))
    }
    for _, side, player_id in assignments:
        if player_id is None:
            continue
        player = roster_players[side][player_id]
        core_team = player.core_team
        team = match.home_team if side == "home" else match.away_team
        if (
            core_team
            and core_team.id != team.id
            and core_team.school_id == team.school_id
            and core_team.team_number < team.team_number
        ):
            raise PairingError(
                f"{core_team.name}'s core player cannot play for the lower team {team.name}."
            )

    for game, side, player_id in assignments:
        setattr(game, f"{side}_player_id", player_id)
    for game, home_score, away_score in scores:
        game.home_score = home_score
        game.away_score = away_score


@bp.route("/tournaments/<int:tournament_id>/rounds/<int:number>", methods=["GET", "POST"])
def round_page(tournament_id, number):
    t = db.get_or_404(Tournament, tournament_id)
    if not 1 <= number <= t.num_rounds:
        abort(404)
 
    if request.method == "POST":
        try:
            action = request.form.get("action")
            if action == "create":
                create_round(t, number, to_table_count(request.form.get("num_tables")))
                warnings = []
                db.session.commit()
                flash(f"Round {number} created.")
            elif action == "resize":
                resize_round(t, number, to_table_count(request.form.get("num_tables")))
                db.session.commit()
                warnings = []
                flash(f"Round {number} table count updated.")
            elif action == "lineup":
                rd = get_round(t, number)
                if rd is None:
                    raise PairingError(f"Round {number} hasn't been created yet.")
                match_id = to_team_id(request.form.get("match_id"))
                match = next(
                    (match for match in rd.matches if match.id == match_id),
                    None,
                )
                if match is None:
                    raise PairingError("That match does not exist in this round.")
                save_match_lineup(match)
                db.session.commit()
                warnings = []
                flash(f"Table {match.table_number} lineup saved.")
            elif action == "clear":
                rd = get_round(t, number)
                if rd is None:
                    raise PairingError(f"Round {number} hasn't been created yet.")
                clear_round(rd)
                db.session.commit()
                warnings = []
                flash(f"Round {number} pairings, lineups, and results cleared. Requested byes were kept.")
            elif action == "generate":
                notes, warnings = apply_generated_pairings(t, number)
                db.session.commit()
                flash("Suggested pairings generated. Review and edit them below, then save.")
                for n in notes:
                    flash("Note: " + n)
            elif action == "save":
                existing_bye_sources = {
                    bye.team_id: bye.source
                    for bye in round_byes(t, number)
                }
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
                    team_id = to_team_id(request.form.get(f"bye_team_{i}"))
                    bye_type = request.form.get(f"bye_type_{i}", "")
                    source = existing_bye_sources.get(team_id)
                    byes.append((team_id, bye_type, source) if source else (team_id, bye_type))
                    i += 1
                warnings = set_round_pairings(t, number, slots, byes)
                db.session.commit()
                flash("Pairings saved.")
            else:
                raise PairingError("Unknown round action.")
        except PairingError as err:
            db.session.rollback()
            flash(str(err))
        else:
            for w in warnings:
                flash("Warning: " + w)
        return redirect(url_for("main.round_page", tournament_id=t.id, number=number))
 
    matches = round_pairings(t, number)
    seeds = seeded_teams(t.id)
    team_match_points = {
        row["team"].id: row["mp"]
        for row in team_standings(t, before_round=number)
    }
    requested_byes_by_team = {
        bye.team_id: bye.bye_type
        for bye in round_byes(t, number)
        if bye.source == "requested"
    }
    bye_rows = [
        {
            "team_id": b.team_id,
            "bye_type": b.bye_type,
            "source": b.source,
            "team": b.team,
        }
        for b in round_byes(t, number)
    ]
    bye_rows += [
        {"team_id": None, "bye_type": "half", "source": None, "team": None}
    ] * max(1, 3 - len(bye_rows))
    return render_template(
        "round.html",
        t=t,
        number=number,
        seeds=seeds,
        team_match_points=team_match_points,
        requested_byes_by_team=requested_byes_by_team,
        roster_players={
            seed.team.id: seed.team.roster
            for seed in seeds
        },
        matches=matches,
        bye_rows=bye_rows,
    )