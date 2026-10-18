from collections import defaultdict
from . models import BOARDS, Game, Match, Round

MP_THRESHOLD = 2.0
FULL_BYE_GAME_POINTS = 2.5
HALF_BYE_GAME_POINTS = 2.0
ZERO_BYE_GAME_POINTS = 0.0

def completed_results(tournament):
    for rd in tournament.rounds:
        for m in rd.matches:
            if not m.is_complete:
                continue
            if m.is_bye:
                yield m.team1_id, None, HALF_BYE_GAME_POINTS
            else:
                yield m.team1_id, m.team2_id, m.team1_score
                yield m.team2_id, m.team1_id, m.team2_score

def team_standings(tournament):
    rows = {t.id: {"team": t, "mp": 0, "tb": 0.0, "gp": 0.0, "played": 0}
    for t in tournament.teams}
    results = list(completed_results(tournament))

    for team_id, opp, gp in results:
        rows[team_id]["gp"] += gp
        rows[team_id]["played"] += 1
        if gp > MP_THRESHOLD:
            rows[team_id] += 1
        if gp == MP_THRESHOLD:
            rows[team_id] += 0.5

    for team_id, opp, gp in results:
        if opp is not None:
            rows[team_id]["tb"] += gp * rows[opp]["mp"]

    ordered = sorted(rows.values(), key=lambda r: (-r["mp"], -r["tb"], -r["gp"], r["team"].name), )
    for i, r in enumerate(ordered, 1):
        r["rank"] = i
    return ordered

def board_standings(tournament):
    games = (Game.query.join(Match, Game.match_id == Match.id)
    .join(Round, Match.round_id == Round.id)
    .filter(Round.tournament_id == tournament.id, Game.score1.isnot(None))
    .all()
    )

    stats = {}
    for g in games:
        for player, score in ((g.player1, g.score1), (g.player2, g.score2)):
            row = stats.setdefault(player.id, {"player": player, "points": 0.0, "games": 0, "board": eligible_board, }, )
            row["points"] += score
            row["games"] += 1
            row["board"] = max(row["board"], g.board)

    by_board = defaultdict(list)
    for row in stats.values():
        by_board[row["board"]].append(row)

    return {
        b: sorted(by_board.get(b, []), key=lambda row: (-row["points"], row["player"].name), )
        for b in range(1, BOARDS + 1)
    }