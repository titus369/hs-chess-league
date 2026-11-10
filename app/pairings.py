from sqlalchemy.orm import selectinload
from . import db
from .models import BOARDS, MAX_HALF_BYES, Bye, Game, Match, Round
from .rosters import seeded_teams
from .standings import team_standings
from .swiss import Entrant, suggest_pairings
 
BYE_TYPES = ("full", "half","zero")
DEFAULT_SOURCE = {"full": "odd", "half": "requested", "zero": "requested"}
 
class PairingError(ValueError):
    """Raised when a round can't be created or its pairings can't be saved."""
 
def previous_round_problem(tournament, number):
    """Why round `number` can't be paired yet, or None if the previous round is complete."""
    if number <= 1:
        return None
    prev = get_round(tournament, number - 1)
    if prev is None or not prev.paired_matches:
        return f"Round {number - 1} hasn't been paired yet. Pair and finish it before pairing round {number}."
    if prev.incomplete_tables:
        tables = ", ".join(str(n) for n in prev.incomplete_tables)
        return (f"Round {number - 1} isn't complete (table {tables} still needs results). "
                f"Finish it before pairing round {number}.")
    return None

def require_previous_round_complete(tournament, number):
    problem = previous_round_problem(tournament, number)
    if problem:
        raise PairingError(problem)

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
 
def create_round(tournament, number, num_tables):
    if not 1 <= number <= tournament.num_rounds:
        raise PairingError(f"Round {number} is outside this tournament's range of rounds: 1 to {tournament.num_rounds}.")
    require_previous_round_complete(tournament, number)
 
    if num_tables < 1:
        raise PairingError("A round must have at least 1 table.")
 
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
 
def resize_round(tournament, number, num_tables):
    rd = get_round(tournament, number)
    if rd is None:
        return create_round(tournament, number, num_tables)
    if num_tables < 1:
        raise PairingError("A round must have at least 1 table.")
 
    matches = {match.table_number: match for match in rd.matches}
    for table_number in range(num_tables + 1, max(matches, default=0) + 1):
        match = matches.get(table_number)
        if match is None:
            continue
        has_lineups_or_results = any(
            game.home_player_id is not None
            or game.away_player_id is not None
            or game.home_score is not None
            or game.away_score is not None
            for game in match.games
        )
        if match.home_team_id is not None or match.away_team_id is not None or has_lineups_or_results:
            raise PairingError(
                f"Table {table_number} has teams, lineups, or results. Clear or reassign it before reducing the table count."
            )
 
    for table_number, match in matches.items():
        if table_number > num_tables:
            db.session.delete(match)
 
    existing = {table for table in matches if table <= num_tables}
    for table_number in range(1, num_tables + 1):
        if table_number not in existing:
            db.session.add(Match(
                round=rd,
                table_number=table_number,
                games=[Game(board=board) for board in range(1, BOARDS + 1)],
            ))
 
    db.session.flush()
    db.session.expire(rd, ["matches"])
    return rd
 
def clear_round(rd):
    for match in rd.matches:
        match.home_team = None
        match.away_team = None
        for game in match.games:
            game.home_player = None
            game.away_player = None
            game.home_score = None
            game.away_score = None
 
    for bye in list(rd.byes):
        if bye.source != "requested":
            db.session.delete(bye)
 
    db.session.flush()
 
def set_round_pairings(tournament, number, slots, byes=()):
    require_previous_round_complete(tournament, number)
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
 
        source = entry[2] if len(entry) > 2 and entry[2] else DEFAULT_SOURCE[bye_type]
        claim(team_id, f"a {bye_type} bye")
        if (bye_type == "half" and source in ("requested", "late_join") and _half_byes_used(tournament, number, team_id) >= MAX_HALF_BYES):
            bye_type, source = "zero", "requested"
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
 
def add_match(tournament, number, home_id, away_id):
    """Add one more table to a round for two teams that aren't playing in it yet,
    e.g. two teams that had byes. Their byes in this round are removed; existing
    tables, lineups, and results are left alone. Returns pairing warnings."""
    require_previous_round_complete(tournament, number)
    rd = get_round(tournament, number)
    if rd is None:
        raise PairingError(f"Round {number} hasn't been created yet.")
    teams = {t.id: t for t in tournament.teams}
    if home_id not in teams or away_id not in teams:
        raise PairingError("Choose two teams from this tournament.")
    if home_id == away_id:
        raise PairingError("A team can't play itself.")
    for m in rd.matches:
        for team_id in (home_id, away_id):
            if team_id in (m.home_team_id, m.away_team_id):
                raise PairingError(f"{teams[team_id].name} is already playing at table {m.table_number}.")

    for bye in list(rd.byes):
        if bye.team_id in (home_id, away_id):
            db.session.delete(bye)
    table = max((m.table_number for m in rd.matches), default=0) + 1
    db.session.add(Match(
        round=rd,
        table_number=table,
        home_team=teams[home_id],
        away_team=teams[away_id],
        games=[Game(board=b) for b in range(1, BOARDS + 1)],
    ))
    db.session.flush()
    db.session.expire(rd, ["matches", "byes"])
    return table, pairing_warnings(rd)

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
 
 
def suggest_round_pairings(tournament, number):
    """Build a suggested pairing for a round. Returns (Suggestion, fixed_byes, notes).
 
    Teams with a requested/late-join bye this round sit out of the pool.
    An automatic ("odd") bye from an earlier generation is re-decided.
    """
    rd = get_round(tournament, number)
    fixed_byes = [
        (b.team_id, b.bye_type, b.source)
        for b in (rd.byes if rd else [])
        if b.source != "odd"
    ]
    fixed_ids = {b[0] for b in fixed_byes}
 
    seeds = seeded_teams(tournament.id)
    mp = {row["team"].id: row["mp"] for row in team_standings(tournament, before_round=number)}
 
    played, history = set(), []
    full_bye_teams, defeated = set(), set()
    notes = []
    rounds = Round.query.filter_by(tournament_id=tournament.id).order_by(Round.number).all()
    for r in rounds:
        if r.number == number:
            continue
        for m in r.matches:
            if m.home_team_id is None or m.away_team_id is None:
                continue
            played.add(frozenset((m.home_team_id, m.away_team_id)))
            if r.number < number:
                history.append((r.number, m.home_team_id, "H"))
                history.append((r.number, m.away_team_id, "A"))
                if m.is_complete:
                    if m.home_match_points == 0:
                        defeated.add(m.home_team_id)
                    if m.away_match_points == 0:
                        defeated.add(m.away_team_id)
                else:
                    notes.append(f"Round {r.number}, table {m.table_number} has no complete result yet, so match points may be low.")
        for b in r.byes:
            if b.bye_type == "full":
                full_bye_teams.add(b.team_id)
            if r.number < number and b.bye_type == "zero":
                defeated.add(b.team_id)
    history.sort(key=lambda h: h[0])
    sides = {}
    for _, team_id, side in history:
        sides.setdefault(team_id, []).append(side)
 
    entrants = []
    for seed in seeds:
        t = seed.team
        if t.id in fixed_ids:
            continue
        if seed.core_count < 4:
            notes.append(f"{t.name} has only {seed.core_count} core players.")
        entrants.append(Entrant(
            id=t.id, name=t.name, tpn=seed.number, school_id=t.school_id,
            mp=mp.get(t.id, 0.0), sides=sides.get(t.id, []),
            had_full_bye=t.id in full_bye_teams, undefeated=t.id not in defeated,
        ))
    if len(entrants) < 2 and not entrants:
        raise PairingError("There are no teams available to pair.")
    suggestion = suggest_pairings(entrants, played)
    notes = sorted(set(notes)) + suggestion.notes
    return suggestion, fixed_byes, notes
 
 
def apply_generated_pairings(tournament, number):
    """Replace this round's teams with a generated suggestion. Returns (notes, warnings)."""
    require_previous_round_complete(tournament, number)
    rd = get_round(tournament, number)
    if rd is not None:
        for m in rd.matches:
            if any(g.home_player_id is not None or g.away_player_id is not None
                   or g.home_score is not None or g.away_score is not None for g in m.games):
                raise PairingError(
                    f"Table {m.table_number} already has lineups or results. Use Clear this round first, then generate again."
                )
    suggestion, fixed_byes, notes = suggest_round_pairings(tournament, number)
    if not suggestion.pairs:
        raise PairingError("No matches could be generated for this round.")
 
    if rd is not None:
        for m in rd.matches:
            m.home_team = None
            m.away_team = None
        db.session.flush()
    needed = len(suggestion.pairs)
    rd = resize_round(tournament, number, needed) if rd is not None else create_round(tournament, number, needed)
 
    slots = {table: pair for table, pair in enumerate(suggestion.pairs, start=1)}
    byes = list(fixed_byes)
    if suggestion.bye is not None:
        byes.append((suggestion.bye, "full", "odd"))
    warnings = set_round_pairings(tournament, number, slots, byes)
    return notes, warnings
 