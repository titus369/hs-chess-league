from collections import defaultdict
from sqlalchemy.orm import joinedload, selectinload
from .models import BOARDS, Game, Match, Round, match_points_for

def team_standings(tournament, before_round=None):
    rounds_query = (
        Round.query.filter_by(tournament_id = tournament.id)
        .order_by(Round.number)
        .options(
            selectinload(Round.matches).selectinload(Match.games),
            selectinload(Round.byes),
        )
    )
    if before_round is not None:
        rounds_query = rounds_query.filter(Round.number < before_round)
    rounds = rounds_query.all()

    return build_team_standings(tournament.teams, rounds, include_uncompleted_byes=before_round is not None)

def build_team_standings(teams, rounds, include_uncompleted_byes=False):
    rows = {
        t.id: {"team": t, "mp": 0.0, "gp": 0.0, "played": 0, "byes": 0} for t in teams
    }

    for rd in rounds:
        for m in rd.matches:
            if not m.is_complete:
                continue
            sides = ((m.home_team_id, m.home_game_points), (m.away_team_id, m.away_game_points),)
            for team_id, gp in sides:
                row = rows[team_id]
                row["gp"] += gp
                row["mp"] += match_points_for(gp)
                row["played"] += 1
    
    reached = max((rd.number for rd in rounds if any(m.is_complete for m in rd.matches)), default=0)
    for rd in rounds:
        if not include_uncompleted_byes and rd.number > reached:
            continue
        for bye in rd.byes:
            row  = rows[bye.team_id]
            row["mp"] += bye.match_points
            row["gp"] += bye.game_points
            row["byes"] += 1

    ordered = sorted(
        rows.values(),
        key=lambda r: (-r["mp"], -r["team"].avg_rating, r["team"].name),
    )

    rank, prev = 0, None
    for position, r in enumerate(ordered, start=1):
        key = (r["mp"], r["team"].avg_rating)
        if key != prev:
            rank, prev = position, key
        r["rank"] = rank
    return ordered

def board_standings(tournament):
    games = (
        Game.query.join(Match, Game.match_id == Match.id)
        .join(Round, Match.round_id == Round.id)
        .filter(Round.tournament_id == tournament.id, Game.home_score.isnot(None))
        .options(joinedload(Game.home_player), joinedload(Game.away_player))
        .all()
    )
    return build_board_standings(games)

def build_board_standings(games):
    stats = {}
    for g in games:
        for player, score in ((g.home_player, g.home_score), (g.away_player, g.away_score)):
            if player is None:
                continue
            row = stats.setdefault(player.id, {"player": player, "points": 0.0, "games": 0, "board": 0},)
            row["points"] += score
            row["games"] += 1
            row["board"] = max(row["board"], g.board)
    
    by_board = defaultdict(list)
    for row in stats.values():
        by_board[row["board"]].append(row)
    
    return {
        b: sorted(by_board.get(b, []), key=lambda row: (-row["points"], row["player"].name))
        for b in range(1, BOARDS + 1)
    }

RESULT_LETTER = {1.0: "W", 0.5: "D", 0.0: "L"}


def wallchart(tournament):
    """Team standings plus, for each team, one cell per round of the tournament."""
    rounds = (
        Round.query.filter_by(tournament_id=tournament.id)
        .order_by(Round.number)
        .options(
            selectinload(Round.matches).selectinload(Match.games),
            selectinload(Round.byes),
        )
        .all()
    )
    standings = build_team_standings(tournament.teams, rounds)
    return build_wallchart(standings, rounds, tournament.num_rounds)


def build_wallchart(standings, rounds, num_rounds):
    """Each standings row gets "line" (its place in the chart, used to refer to
    opponents) and "rounds": a list with one cell per round, or None.

    A match cell: {"kind": "match", "opponent", "line", "side" ("H"/"A"),
    "result" ("W"/"D"/"L", or None until the match is complete), "score"
    ("3-1" from this team's side, or None)}. A bye cell: {"kind": "bye",
    "bye_type" ("full"/"half"/"zero"), "points"}."""
    line = {row["team"].id: n for n, row in enumerate(standings, start=1)}
    cells = {team_id: {} for team_id in line}
    for rd in rounds:
        for m in rd.matches:
            if m.home_team_id is None or m.away_team_id is None:
                continue
            done = m.is_complete
            for team_id, opponent, side, gp, opp_gp, mp in (
                (m.home_team_id, m.away_team, "H", m.home_game_points, m.away_game_points, m.home_match_points),
                (m.away_team_id, m.home_team, "A", m.away_game_points, m.home_game_points, m.away_match_points),
            ):
                cells.setdefault(team_id, {})[rd.number] = {
                    "kind": "match",
                    "opponent": opponent,
                    "line": line.get(opponent.id),
                    "side": side,
                    "result": RESULT_LETTER[mp] if done else None,
                    "score": f"{gp:g}-{opp_gp:g}" if done else None,
                }
        for bye in rd.byes:
            cells.setdefault(bye.team_id, {})[rd.number] = {
                "kind": "bye",
                "bye_type": bye.bye_type,
                "points": bye.match_points,
            }
    for row in standings:
        row["line"] = line[row["team"].id]
        team_cells = cells[row["team"].id]
        row["rounds"] = [team_cells.get(n) for n in range(1, num_rounds + 1)]
    return standings
