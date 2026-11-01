from sqlalchemy.orm import selectinload
from . import db
from .models import BOARDS, MAX_HALF_BYES, Bye, Game, Match, Round

BYE_TYPES = ("full", "half","zero")
DEFAULT_SOURCE = {"full": "odd", "half": "requested", "zero": "default"}

class PairingError(ValueError):
    """Raised when a round can't be created or its pairings can't be saved."""

def get_round(tournament, number):
    return Round.query.filter_by(tournament_id=tournament.id, number=number).first()

def round_pairings(tournament, number):
    rd = get_round(tournament, number)
    return sorted(rd.matches, key=lambda m: m.table_number) if rd else []

def round_byes(tournament, number):
    rd = get_round(tournament, number)
    return list(rd.byes) if rd else []

def all_pairings(tournament):
    rounds = (
        Round.query.filter_by(tournament_id=tournament.id)
        .order_by(Round.number)
        .options(selectinload(Round.matches))
        .all()
    )

    return {
        rd.number: sorted(rd.matches, key=lambda m: m.table_number)
        for rd in rounds if rd.matches
    }

def create_round(tournament, number, num_tables = None):
    if not 1 <= number <= tournament.num_rounds:
        raise PairingError(f"Round {number} is outside this tournament's range of rounds: 1 to {tournament.num_rounds}.")

    if num_tables is None:
        num_tables = len(tournament.teams) // 2
    
    if num_tables < 1:
        raise PairingError("Add at least 2 teams before creating a round.")

    rd = get_round(tournament, number)
    if rd is None:
        rd = Round(tournament_id=tournament.id, number=number)
        db.session.add(rd)
        db.session.flush()

    have = {m.table_number for m in rd.matches}
    for table in range(1, num_tables + 1):
        if table not in have:
            db.session.add(Match(round=rd, table_number=table, games=[Game(board=b) for b in range(1, BOARDS + 1)]))
    
    db.session.flush()
    db.session.expire(rd, ["matches"])
    return rd

def set_round_pairings(tournament, number, slots, byes=()):
    rd = get_round(tournament, number)
    if rd is None:
        raise PairingError(f"Round {number} hasn't been created yet.")
    teams = {t.id: t for t in tournament.teams}
    matches = {m.table_number: m for m in rd.matches}
    for table in slots:
        if table not in matches:
            raise PairingError(f"Table {table} doesn't exist in round {number}.")

    used = {}
    def claim(team_id, where):
        if team_id not in teams:
            raise PairingError(f"{where}: that team isn't in this tournament.")
        if team_id in used:
            raise PairingError(f"{teams[team_id].name} has already been used ({used[team_id]} and {where}).")
        used[team_id] = where
    
    plan = {}
    for table in sorted(matches):
        home_id, away_id = slots.get(table, (matches[table].home_team_id, matches[table].away_team_id))
        if home_id is not None and home_id == away_id:
            raise PairingError(f"Table {table}: a team can't play itself.")
        for team_id in (home_id, away_id):
            if team_id is not None:
                claim(team_id, f"table {table}")
        plan[table] = (home_id, away_id)

    warnings = []
    bye_plan = []
    for entry in byes:
        team_id, bye_type = entry[0], entry[1]
        if team_id is None:
            continue

        if bye_type not in BYE_TYPES:
            raise PairingError(f"Unknown bye type ' {bye_type}'.")

        source = entry[2] if len(entry) > 2 else DEFAULT_SOURCE[bye_type]
        claim(team_id, f"a {bye_type} bye")
        if (bye_type == "half" and source in ("requested", "late_join") and _half_byes_used(tournament, number, team_id) >= MAX_HALF_BYES):
            bye_type, source = "zero", "default"
            warnings.append(f"{teams[team_id].name} already has {MAX_HALF_BYES} half-point byes, so this is defaulted as a zero-point bye.")
        bye_plan.append((team_id, bye_type, source))

    for table, (home_id, away_id) in plan.items():
        m = matches[table]
        scored = any(g.home_score is not None for g in m.games)
        for side, new_id, old_id in (("home", home_id, m.home_team_id), ("away", away_id, m.away_team_id)):
            if new_id == old_id:
                continue
            has_players = any(getattr(g, f"{side}_player_id") is not None for g in m.games)
            if scored or has_players:
                raise PairingError(f"Table{table}: the {side} team already has players or results entered, so it can't be changed.")

    for table, (home_id, away_id) in plan.items():
        m = matches[table]
        m.home_team = teams.get(home_id)
        m.away_team = teams.get(away_id)

    for b in list(rd. byes):
        db.session.delete(b)
    db.session.flush()
    for team_id, bye_type, source in bye_plan:
        db.session.add(Bye(round=rd, team_id=team_id, bye_type=bye_type, source=source))
    db.session.flush()
    db.session.expire(rd, ["matches", "byes"])

    warnings += pairing_warnings(rd)
    unplaced = [t.name for t in tournament.teams if t.id not in used]
    if unplaced:
        warnings.append(f"Not placed in round {number}: {','.join(unplaced)}.")
    return warnings

def pairing_warnings(rd):
    met = {}
    for m in _other_matches(rd):
        if m.home_team_id is not None and m.away_team_id is not None:
            met.setdefault(frozenset((m.home_team_id, m.away_team_id)), m.round.number)

    warnings = []
    for m in sorted(rd.matches, key=lambda m: m.table_number):
        home, away = m.home_team, m.away_team
        if home is None or away is None:
            continue
        if home.school_id == away.school_id:
            warnings.append(f"{home.name} vs {away.name}: same school.")
        earlier = met.get(frozenset((home.id, away.id)))
        if earlier is not None:
            warnings.append(f"{home.name} and {away.name} also play in round{earlier}.")
    return warnings

def _other_matches(rd):
    return(
        Match.query.join(Round, Match.round_id == Round.id)
        .filter(Round.tournament_id == rd.tournament_id, Round.number != rd.number)
        .all()
    )

def _half_byes_used(tournament, number, team_id):
    return (
        Bye.query.join(Round, Bye.round_id == Round.id)
        .filter(
            Round.tournament_id == tournament.id,
            Round.number != number,
            Bye.team_id == team_id,
            Bye.bye_type == "half",
            Bye.source.in_(("requested", "late_join")),
        )
        .count()
    )
