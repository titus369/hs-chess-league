"""Shared fixtures for the test suite.

Run everything with `python -m pytest` from the project root.

Every test gets a brand-new in-memory SQLite database, so tests never touch
instance/league.db and never affect each other. The `league` fixture builds
tournaments, teams, pairings and results either directly in the database (fast,
for unit tests) or through the real HTTP routes (for testing the pages a
director uses).

When you add or change a feature, add tests for it in the matching file:

    test_app.py        app factory, database setup, schema migration
    test_models.py     model properties, scoring rules, eligibility, constraints
    test_swiss.py      the pairing engine (pure Python, no database)
    test_rosters.py    seeding and roster reports
    test_standings.py  team and individual board standings
    test_teams.py      adding/editing teams and players, start and mid-tournament
    test_byes.py       bye requests, late-join byes, the half-point bye cap
    test_pairings.py   creating, generating, switching, clearing and locking rounds
    test_lineups.py    lineups, results, forfeits and pasted substitutes
    test_pages.py      every page renders; navigation and 404s
"""
import pytest

from app import create_app, db
from app.models import BOARDS, Bye, Player, RosterEntry, Round, School, Team, Tournament
from app.pairings import create_round, set_round_pairings
from app.routes import RESULTS
from helpers import flashed, flashes


@pytest.fixture
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": "test",
    })
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def league(client):
    return League(client)


class League:
    """Builders for tournament data. Methods that start with `post_` or that say
    "HTTP" go through the routes; the rest write to the database directly."""

    def __init__(self, client):
        self.client = client

    # ---- HTTP -------------------------------------------------------------

    def get(self, url):
        response = self.client.get(url, follow_redirects=True)
        db.session.expire_all()
        return response

    def post(self, url, data):
        response = self.client.post(url, data=data, follow_redirects=True)
        db.session.expire_all()
        return response

    def post_team(self, t, school, names=None, usernames=None, ratings=None, byes=None):
        """Add a team through the tournament page's Add team form (HTTP)."""
        names = names or [f"{school} Player {b}" for b in range(1, BOARDS + 1)]
        usernames = usernames or [n.lower().replace(" ", "_") for n in names]
        ratings = ratings or [str(1600 - 100 * b) for b in range(BOARDS)]
        data = {"school": school, "name": list(names), "lichess_username": list(usernames),
                "rating": [str(r) if r is not None else "" for r in ratings]}
        for number, bye_type in (byes or {}).items():
            data[f"bye_round_{number}"] = bye_type
        return self.post(f"/tournaments/{t.id}/teams", data)

    def round_action(self, t, number, action, **form):
        """Submit one of the round page's forms (HTTP)."""
        return self.post(f"/tournaments/{t.id}/rounds/{number}", {"action": action, **form})

    def generate(self, t, number):
        return self.round_action(t, number, "generate")

    def lineup(self, match, results, home=None, away=None, extra=None):
        """Save a table's lineup and results through the round page (HTTP).
        `results` is one result code per board ("" or None for no result).
        `home`/`away` are player ids per board; default is the core players in
        board order."""
        home = home if home is not None else [p.id for p in match.home_team.core_players]
        away = away if away is not None else [p.id for p in match.away_team.core_players]
        data = {"action": "lineup", "match_id": match.id}
        for game, code, home_id, away_id in zip(match.games, results, home, away):
            data[f"home_player_{game.id}"] = "" if home_id is None else str(home_id)
            data[f"away_player_{game.id}"] = "" if away_id is None else str(away_id)
            data[f"result_{game.id}"] = code or ""
        data.update(extra or {})
        return self.post(f"/tournaments/{match.round.tournament_id}/rounds/{match.round.number}", data)

    def finish_round(self, t, number, results=("1-0", "1-0", "1-0", "1-0")):
        """Enter the same results at every paired table of a round (HTTP)."""
        rd = self.round(t, number)
        for match in list(rd.matches):
            if match.home_team_id is not None and match.away_team_id is not None:
                response = self.lineup(match, results)
                assert flashed(response, "lineup saved"), flashes(response)
        db.session.expire_all()

    # ---- Direct database builders ----------------------------------------

    def tournament(self, name="Test League", rounds=7):
        t = Tournament(name=name, num_rounds=rounds)
        db.session.add(t)
        db.session.commit()
        return t

    def school(self, name):
        school = School.query.filter_by(name=name).first()
        if school is None:
            school = School(name=name)
            db.session.add(school)
            db.session.flush()
        return school

    def team(self, t, school_name, ratings=(1600, 1500, 1400, 1300), joined_round=1):
        """A team with BOARDS core players in board order."""
        school = self.school(school_name)
        existing = [x.team_number for x in Team.query.filter_by(tournament_id=t.id, school_id=school.id)]
        team = Team(tournament=t, school=school, team_number=max(existing, default=0) + 1,
                    joined_round=joined_round)
        db.session.add(team)
        db.session.flush()
        for board, rating in enumerate(ratings, start=1):
            self.player(team, f"{team.name} B{board}", rating=rating, core=True)
        db.session.commit()
        return team

    def player(self, team, name, rating=None, core=False, username=None):
        """Add a player to `team`'s roster (a substitute unless core=True)."""
        player = Player(tournament_id=team.tournament_id, school_id=team.school_id, name=name,
                        lichess_username=username or name.lower().replace(" ", "_"), rating=rating)
        last = max((e.position for e in team.roster), default=0.0)
        db.session.add(RosterEntry(team=team, player=player, position=last + 1.0, is_core=core))
        db.session.commit()
        return player

    def pair(self, t, number, pairs, byes=()):
        """Set a round's pairings directly. `pairs` is [(home_team, away_team)],
        `byes` is [(team, bye_type)] or [(team, bye_type, source)]. Returns warnings."""
        if self.round(t, number) is None or not self.round(t, number).matches:
            create_round(t, number, max(len(pairs), 1))
        slots = {table: (home.id, away.id) for table, (home, away) in enumerate(pairs, start=1)}
        warnings = set_round_pairings(t, number, slots, [(b[0].id, *b[1:]) for b in byes])
        db.session.commit()
        return warnings

    def set_results(self, match, results, home=None, away=None):
        """Store results directly, without the lineup form's checks."""
        home = home if home is not None else [p.id for p in match.home_team.core_players]
        away = away if away is not None else [p.id for p in match.away_team.core_players]
        for game, code, home_id, away_id in zip(match.games, results, home, away):
            if not code:
                game.home_player_id, game.away_player_id = home_id, away_id
                game.home_score = game.away_score = None
                continue
            home_score, away_score, home_present, away_present = RESULTS[code]
            game.home_player_id = home_id if home_present else None
            game.away_player_id = away_id if away_present else None
            game.home_score, game.away_score = home_score, away_score
        db.session.commit()

    def add_bye(self, t, number, team, bye_type, source="requested"):
        rd = self.round(t, number)
        if rd is None:
            rd = Round(tournament_id=t.id, number=number)
            db.session.add(rd)
            db.session.flush()
        db.session.add(Bye(round=rd, team=team, bye_type=bye_type, source=source))
        db.session.commit()

    # ---- Lookups ----------------------------------------------------------

    def round(self, t, number):
        return Round.query.filter_by(tournament_id=t.id, number=number).first()

    def match_of(self, t, number, team):
        rd = self.round(t, number)
        return next((m for m in rd.matches if team.id in (m.home_team_id, m.away_team_id)), None) if rd else None

    def byes_of(self, team):
        """{round_number: (bye_type, source)} for a team."""
        return {b.round.number: (b.bye_type, b.source) for b in Bye.query.filter_by(team_id=team.id)}

    def team_named(self, t, name):
        return next(team for team in Team.query.filter_by(tournament_id=t.id) if team.name == name)

    def opponents(self, t, number):
        """{frozenset(team ids)} paired in a round."""
        rd = self.round(t, number)
        return {frozenset((m.home_team_id, m.away_team_id)) for m in rd.matches
                if m.home_team_id is not None and m.away_team_id is not None}
