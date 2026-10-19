from collections import defaultdict
from sqlalchemy.orm import joinedload, selectinload
from .models import BOARDS, Game, Match, Round, match_points_for

def team_standings(tournament):
    rounds = (
        Round.query.filter_by(tournament_id = tournament.id)
        .order_by(Round.number)
        .options(
            selectinload(Round.matches).selectinload(Match.games),
            selectinload(Round.byes),
        )
        .all()
    )

    return build_team_standings(tournament.teams, rounds)

def build_team_standings(teams, rounds):
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
        if rd.number > reached:
            continue
        for bye in rd.byes:
            row  = rows[bye.team_id]
            row["mp"] += bye.match_points
            row["gp"] += bye.game_points
            row["byes"] += 1

    ordered = sorted(rows.values(), key=lambda r: (-r["mp"], -r["gp"], r["team"].name),)

    rank, prev = 0, None
    for position, r in enumerate(ordered, start=1):
        key=(r["mp"], r["gp"])
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