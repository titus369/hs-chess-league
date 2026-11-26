"""Individual grand prix: points from Lichess arenas held alongside the league.

Scoring
  * Weekly arena: ranks 1-5 earn WEEKLY_PRIZES.
  * Kickoff arena: ranks 1-10 earn KICKOFF_PRIZES. Members of the top 3 Lichess
    teams also earn TEAM_BONUS, but only if they played at least one game.
  * Everyone who played at least one game in an arena is listed for it, even
    with 0 grand prix points, so participation always shows.
  * A player's total is the sum over every arena. Ties are broken by the total
    Lichess arena score (the points earned inside the arenas); players equal on
    both share a rank. Lichess usernames are matched without regard to capital
    letters, and to league players by their Lichess username, so the standings
    can show names and schools.

Adding an arena that is already in the grand prix replaces its results, so an
arena can be re-imported safely (e.g. after Lichess finalises the standings).
"""
import io
import json
import re
from dataclasses import dataclass, field

import chess.pgn
import requests

from . import db
from .models import GrandPrixEvent, GrandPrixResult, Player

WEEKLY_PRIZES = [20, 12, 8, 6, 4]
KICKOFF_PRIZES = [30, 18, 12, 9, 6, 5, 4, 3, 2, 1]
TEAM_BONUS = [5, 3, 2]

KINDS = {
    "weekly": "Weekly arena (top 5 score)",
    "kickoff": "Kickoff arena (top 10 score, plus team bonus)",
}

BASE_URL = "https://lichess.org/api"
SESSION = requests.Session()
ARENA_ID = re.compile(r"^[A-Za-z0-9]{8}$")
ARENA_URL = re.compile(r"lichess\.org/tournament/([A-Za-z0-9]{8})")


class GrandPrixError(ValueError):
    """Raised when an arena can't be added; the message is shown to the director."""


@dataclass
class Score:
    rank: int | None = None
    rank_points: int = 0
    team_bonus: int = 0
    arena_score: int = 0


@dataclass
class StandingRow:
    username: str
    player: Player | None
    points: dict = field(default_factory=dict)        # event id -> grand prix points
    arena_scores: dict = field(default_factory=dict)  # event id -> Lichess arena score
    total: int = 0
    arena_total: int = 0                              # tiebreak
    rank: int = 0


# ---- Lichess -----------------------------------------------------------

def parse_arena_id(value):
    """An arena ID from either the ID itself or a lichess.org tournament link."""
    value = (value or "").strip()
    match = ARENA_URL.search(value)
    if match:
        return match.group(1)
    if ARENA_ID.match(value):
        return value
    raise GrandPrixError(
        "Enter the arena's 8-character Lichess tournament ID (e.g. yLyBkTk7) or its lichess.org link."
    )


def lichess_get(endpoint, params=None):
    try:
        response = SESSION.get(f"{BASE_URL}/{endpoint}", params=params, timeout=60)
    except requests.RequestException as err:
        raise GrandPrixError(f"Couldn't reach Lichess ({err.__class__.__name__}). Try again in a moment.") from None
    if response.status_code == 429:
        raise GrandPrixError("Lichess rate limit reached. Wait one minute before trying again.")
    if response.status_code == 404:
        raise GrandPrixError("Lichess has no arena with that ID. Check the tournament ID.")
    if not response.ok:
        raise GrandPrixError(f"Lichess returned an error ({response.status_code}). Try again later.")
    return response


def get_individual_results(arena_id):
    response = lichess_get(f"tournament/{arena_id}/results", params={"nb": 1000})
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def get_team_results(arena_id):
    return lichess_get(f"tournament/{arena_id}/teams").json()


def get_played_usernames(arena_id):
    """Usernames (lower case) that played at least one game in the arena."""
    response = lichess_get(f"tournament/{arena_id}/games", params={"pgnInJson": "false"})
    pgn_file = io.StringIO(response.text)
    played = set()
    while (game := chess.pgn.read_game(pgn_file)) is not None:
        for color in ("White", "Black"):
            username = game.headers.get(color, "").strip()
            if username and username != "?":
                played.add(username.lower())
    return played


def fetch_arena_scores(arena_id, kind):
    """{username: Score} for everyone who played in an arena, fetched from Lichess."""
    individual = get_individual_results(arena_id)
    played = get_played_usernames(arena_id)
    teams = get_team_results(arena_id) if kind == "kickoff" else None
    return score_arena(kind, individual, teams, played)


# ---- Scoring (pure) ----------------------------------------------------

def normalize_username(username):
    return str(username or "").strip().lower()


def award_by_rank(results, prizes):
    """{username: (rank, points)} for players ranked within the prize list."""
    awards = {}
    for player in results:
        username = normalize_username(player.get("username"))
        rank = player.get("rank")
        if username and username not in awards and rank is not None and 1 <= int(rank) <= len(prizes):
            awards[username] = (int(rank), prizes[int(rank) - 1])
    return awards


def extract_team_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("teams"), list):
        return payload["teams"]
    raise GrandPrixError("Lichess sent team standings in an unexpected format.")


def get_team_id(team):
    if not isinstance(team, dict):
        return None
    team_id = team.get("id") or team.get("teamId")
    if not team_id and isinstance(team.get("team"), dict):
        team_id = team["team"].get("id")
    return str(team_id).lower() if team_id else None


def calculate_team_bonus(individual_results, team_payload, played_usernames):
    """{username: bonus} for members of the top teams who played a game."""
    bonus_by_team = {}
    for rank, team in enumerate(extract_team_list(team_payload)[:len(TEAM_BONUS)], start=1):
        team_id = get_team_id(team)
        if team_id:
            bonus_by_team[team_id] = TEAM_BONUS[rank - 1]
    bonuses = {}
    for player in individual_results:
        username = normalize_username(player.get("username"))
        team_id = str(player.get("team") or "").lower()
        if username in played_usernames and team_id in bonus_by_team:
            bonuses[username] = bonus_by_team[team_id]
    return bonuses


def score_arena(kind, individual_results, team_payload=None, played_usernames=()):
    """{username: Score} for everyone in one arena who played at least one game
    or earned grand prix points (0-point players included)."""
    if kind not in KINDS:
        raise GrandPrixError("Choose a scoring system: weekly or kickoff.")
    prizes = KICKOFF_PRIZES if kind == "kickoff" else WEEKLY_PRIZES
    played = {normalize_username(u) for u in played_usernames}
    awards = award_by_rank(individual_results, prizes)
    bonuses = (calculate_team_bonus(individual_results, team_payload, played)
               if kind == "kickoff" and team_payload is not None else {})
    scores = {}
    for player in individual_results:
        username = normalize_username(player.get("username"))
        if not username or username in scores:
            continue
        rank = player.get("rank")
        _, points = awards.get(username, (None, 0))
        bonus = bonuses.get(username, 0)
        if username in played or points or bonus:
            scores[username] = Score(rank=int(rank) if rank is not None else None, rank_points=points,
                                     team_bonus=bonus, arena_score=int(player.get("score") or 0))
    return scores


# ---- Database ----------------------------------------------------------

def add_arena(tournament, arena_id, kind, event_date, fetch=None):
    """Add an arena to the grand prix, or refresh it if it's already there.
    Returns (event, created)."""
    arena_id = parse_arena_id(arena_id)
    if kind not in KINDS:
        raise GrandPrixError("Choose a scoring system: weekly or kickoff.")
    if event_date is None:
        raise GrandPrixError("Enter the arena's date.")
    scores = (fetch or fetch_arena_scores)(arena_id, kind)

    event = GrandPrixEvent.query.filter(
        GrandPrixEvent.tournament_id == tournament.id,
        db.func.lower(GrandPrixEvent.arena_id) == arena_id.lower(),
    ).first()
    created = event is None
    if created:
        event = GrandPrixEvent(tournament=tournament, arena_id=arena_id)
        db.session.add(event)
    event.kind = kind
    event.event_date = event_date
    # Delete the old results first: inserting before deleting would break the
    # one-row-per-username rule on a re-import.
    for old in list(event.results):
        db.session.delete(old)
    db.session.flush()
    event.results = [
        GrandPrixResult(lichess_username=username, rank=s.rank, rank_points=s.rank_points,
                        team_bonus=s.team_bonus, arena_score=s.arena_score)
        for username, s in scores.items()
    ]
    db.session.flush()
    return event, created


def remove_arena(tournament, event_id):
    event = db.session.get(GrandPrixEvent, event_id)
    if event is None or event.tournament_id != tournament.id:
        raise GrandPrixError("That arena isn't in this grand prix.")
    db.session.delete(event)
    db.session.flush()
    return event


def grand_prix_standings(tournament):
    """(events, rows): rows sorted by grand prix total, then total arena score;
    players equal on both share a rank. Each row's player is the league player
    with that Lichess username, or None."""
    events = list(tournament.grand_prix_events)
    players = {}
    for p in sorted(Player.query.filter_by(tournament_id=tournament.id), key=lambda p: p.id):
        players.setdefault(normalize_username(p.lichess_username), p)

    rows = {}
    for event in events:
        for result in event.results:
            username = normalize_username(result.lichess_username)
            row = rows.setdefault(username, StandingRow(username=username, player=players.get(username)))
            row.points[event.id] = row.points.get(event.id, 0) + result.points
            row.arena_scores[event.id] = row.arena_scores.get(event.id, 0) + result.arena_score
            row.total += result.points
            row.arena_total += result.arena_score

    def standing(row):
        return (row.total, row.arena_total)

    ordered = sorted(rows.values(), key=lambda r: (-r.total, -r.arena_total,
                                                   (r.player.name if r.player else r.username).lower()))
    for n, row in enumerate(ordered):
        tied = n and standing(row) == standing(ordered[n - 1])
        row.rank = ordered[n - 1].rank if tied else n + 1
    return events, ordered
