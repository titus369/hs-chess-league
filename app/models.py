from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from . import db

BOARDS = 4

WIN_THRESHOLD = 2.5
DRAW_THRESHOLD = 2.0
MAX_HALF_BYES = 3 # requested / late-join half point byes

BYE_POINTS = {
    "full": (1.0, 2.5), 
    "half": (0.5, 2.0),
    "zero": (0.0, 0.0),
}

def _now():
    return datetime.now(timezone.utc)

def match_points_for(game_points):
    if game_points is None:
        return 0.0
    if game_points >= WIN_THRESHOLD:
        return 1.0
    if game_points == DRAW_THRESHOLD:
        return 0.5
    return 0.0


class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)

    players = db.relationship("Player", back_populates="school")
    teams = db.relationship("Team", back_populates="school")

class Tournament(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    num_rounds = db.Column(db.Integer, nullable=False, default=7)
    created_at = db.Column(db.DateTime, default=_now)

    players = db.relationship("Player", back_populates="tournament", cascade="all, delete-orphan")
    teams = db.relationship("Team", back_populates="tournament", cascade="all, delete-orphan", order_by="Team.id")
    rounds = db.relationship("Round", back_populates="tournament", cascade="all, delete-orphan", order_by="Round.number")


class Player(db.Model):
    """A student in one tournament, belonging to one school. A core player has
    is_core=True plus the one team and board they are core on (core_team_id, board);
    a substitute has both NULL. Either kind can play for other teams of the school
    when eligible: those appearances live in Game, not here."""

    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournament.id"), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    uscf_id = db.Column(db.Integer, nullable = True) #players are not required to have a uscf id/rating
    rating = db.Column(db.Integer, nullable = True)
    lichess_username = db.Column(db.String(120), nullable = False)

    school = db.relationship("School", back_populates="players")
    tournament = db.relationship("Tournament", back_populates="players")
    roster_entries = db.relationship("RosterEntry", back_populates="player", cascade="all, delete-orphan")
    
    @property
    def core_team(self):
        return next((e.team for e in self.roster_entries if e.is_core),None)

    @property
    def is_core(self):
        return self.core_team is not None

class RosterEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    team_id=db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)
    player_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=False)
    position = db.Column(db.Float, nullable = False)
    is_core = db.Column(db.Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("team_id", "player_id"),
        Index("uq_one_core_team_per_player", "player_id", unique=True, sqlite_where=text("is_core = 1")),
    )

    team = db.relationship("Team", back_populates="roster")
    player = db.relationship("Player", back_populates="roster_entries")

def position_between( lo, hi):
    if lo is None and hi is None:
        return 1.0
    if lo is None:
        return hi - 1.0
    if hi is None:
        return lo + 1.0
    return (lo + hi) / 2.0

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournament.id"), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), nullable=False)
    team_number = db.Column(db.Integer, nullable=False, default=1)
    joined_round = db.Column(db.Integer, nullable = False, default = 1)

    __table_args__ = (UniqueConstraint("tournament_id", "school_id", "team_number"),)

    tournament = db.relationship("Tournament", back_populates="teams")
    school = db.relationship("School", back_populates="teams")
    roster = db.relationship("RosterEntry", back_populates="team", cascade="all, delete-orphan", order_by="RosterEntry.position")
    
    @property
    def core_players(self):
        return [e.player for e in self.roster if e.is_core]

    @property
    def name(self):
        suffix = chr(64 + self.team_number) if 1 <= self.team_number <= 26 else str(self.team_number)
        return f"{self.school.name} {suffix}"

    @property
    def avg_rating(self):
        # Unrated core players count as 100.
        ratings = [p.rating or 100 for p in self.core_players]
        return sum(ratings) / len(ratings) if ratings else 100


class Round(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournament.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)

    __table_args__ = (UniqueConstraint("tournament_id", "number"),)

    tournament = db.relationship("Tournament", back_populates="rounds")
    matches = db.relationship("Match", back_populates="round", cascade="all, delete-orphan", order_by="Match.id")
    byes = db.relationship("Bye", back_populates="round", cascade="all, delete-orphan")

    @property
    def is_complete(self):
        return bool(self.matches) and all(m.is_complete for m in self.matches)


class Match(db.Model):

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey("round.id"), nullable=False)
    table_number = db.Column(db.Integer, nullable=False)
    home_team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)
    away_team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("round_id", "table_number"),
        UniqueConstraint("round_id", "home_team_id"),
        UniqueConstraint("round_id", "away_team_id"),
        CheckConstraint("home_team_id != away_team_id"),
    )

    round = db.relationship("Round", back_populates="matches")
    home_team = db.relationship("Team", foreign_keys=[home_team_id])
    away_team = db.relationship("Team", foreign_keys=[away_team_id])
    games = db.relationship("Game", back_populates="match", cascade="all, delete-orphan", order_by="Game.board")

    @property
    def is_complete(self):
        return len(self.games) == BOARDS and all(g.home_score is not None for g in self.games)

    @property
    def home_game_points(self):
        return sum(g.home_score for g in self.games if g.home_score is not None)

    @property
    def away_game_points(self):
        return sum(g.away_score for g in self.games if g.away_score is not None)

    @property
    def home_match_points(self):
        return match_points_for(self.home_game_points) if self.is_complete else None

    @property
    def away_match_points(self):
        return match_points_for(self.away_game_points) if self.is_complete else None

class Bye(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey("round.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)
    bye_type = db.Column(db.String(8), nullable=False)
    source = db.Column(db.String(12), nullable=False)

    __table_args__ = (
        UniqueConstraint("round_id", "team_id"),
        CheckConstraint("bye_type IN ('full', 'half', 'zero')"),
        CheckConstraint("source IN ('odd', 'requested', 'late_join', 'default')"),
    )

    round = db.relationship("Round", back_populates="byes")
    team = db.relationship("Team")

    @property
    def match_points(self):
        return BYE_POINTS[self.bye_type][0]

    @property
    def game_points(self):
        return BYE_POINTS[self.bye_type][1]

class Game(db.Model):

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("match.id"), nullable=False)
    board = db.Column(db.Integer, nullable=False)
    home_player_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=True)
    away_player_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=True)
    home_score = db.Column(db.Float, nullable=True)
    away_score = db.Column(db.Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("match_id", "board"),
        UniqueConstraint("match_id", "home_player_id"),
        UniqueConstraint("match_id", "away_player_id"),
        CheckConstraint(f"board BETWEEN 1 AND {BOARDS}"),
        CheckConstraint("home_score IS NULL OR home_score IN (0, 0.5, 1)"),
        CheckConstraint("away_score IS NULL OR away_score IN (0, 0.5, 1)"),
        CheckConstraint("(home_score IS NULL) = (away_score IS NULL)"),
        CheckConstraint("home_player_id IS NOT NULL OR home_score IS NULL OR home_score = 0"),
        CheckConstraint("away_player_id IS NOT NULL OR away_score IS NULL OR away_score = 0"),
        CheckConstraint("home_player_id IS NULL OR away_player_id IS NULL OR home_score IS NULL OR home_score + away_score = 1"),
    )

    match = db.relationship("Match", back_populates="games")
    home_player = db.relationship("Player", foreign_keys=[home_player_id])
    away_player = db.relationship("Player", foreign_keys=[away_player_id])

    @property
    def is_forfeit(self):
        return self.home_score is not None and (self.home_player_id is None or self.away_player_id is None)

    @property
    def home_is_white(self):
        return self.board % 2 == 1
    
    @property
    def white_player(self):
        return self.home_player if self.home_is_white else self.away_player
    
    @property
    def black_player(self):
        return self.away_player if self.home_is_white else self.home_player
    
    @property
    def code(self):
        return f"{self.match.round.number}.{self.match.table_number}.{self.board}"