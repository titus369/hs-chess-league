from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, UniqueConstraint
from . import db

BOARDS = 4


def _now():
    return datetime.now(timezone.utc)


class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)

    players = db.relationship("Player", back_populates="school")
    teams = db.relationship("Team", back_populates="school")


class Player(db.Model):
    """A student in one tournament, belonging to one school. A core player has
    is_core=True plus the one team and board they are core on (core_team_id, board);
    a substitute has both NULL. Either kind can play for other teams of the school
    when eligible: those appearances live in Game, not here."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    rating = db.Column(db.Integer)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournament.id"), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), nullable=False)
    is_core = db.Column(db.Boolean, nullable=False, default=False)
    core_team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)
    board = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("core_team_id", "board"),  # one core player per board per team
        CheckConstraint("NOT is_core OR (core_team_id IS NOT NULL AND board IS NOT NULL)"),
    )

    school = db.relationship("School", back_populates="players")
    tournament = db.relationship("Tournament", back_populates="players")
    core_team = db.relationship("Team", back_populates="core_players")


class Tournament(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    num_rounds = db.Column(db.Integer, nullable=False, default=7)
    created_at = db.Column(db.DateTime, default=_now)

    players = db.relationship("Player", back_populates="tournament", cascade="all, delete-orphan")
    teams = db.relationship("Team", back_populates="tournament", cascade="all, delete-orphan", order_by="Team.id")
    rounds = db.relationship("Round", back_populates="tournament", cascade="all, delete-orphan", order_by="Round.number")


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournament.id"), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), nullable=False)
    team_number = db.Column(db.Integer, nullable=False, default=1)

    __table_args__ = (UniqueConstraint("tournament_id", "school_id", "team_number"),)

    tournament = db.relationship("Tournament", back_populates="teams")
    school = db.relationship("School", back_populates="teams")
    core_players = db.relationship("Player", back_populates="core_team", order_by="Player.board")

    @property
    def name(self):
        suffix = chr(64 + self.team_number) if 1 <= self.team_number <= 26 else str(self.team_number)
        return f"{self.school.name} {suffix}"

    @property
    def avg_rating(self):
        # Unrated core players count as 100.
        ratings = [p.rating or 100 for p in self.core_players]
        return sum(ratings) / len(ratings) if ratings else 0


class Round(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournament.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)

    __table_args__ = (UniqueConstraint("tournament_id", "number"),)

    tournament = db.relationship("Tournament", back_populates="rounds")
    matches = db.relationship("Match", back_populates="round", cascade="all, delete-orphan", order_by="Match.id")

    @property
    def is_complete(self):
        return bool(self.matches) and all(m.is_complete for m in self.matches)


class Match(db.Model):
    """Team vs Team. team2_id is NULL for a bye."""

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey("round.id"), nullable=False)
    team1_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)
    team2_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)

    round = db.relationship("Round", back_populates="matches")
    team1 = db.relationship("Team", foreign_keys=[team1_id])
    team2 = db.relationship("Team", foreign_keys=[team2_id])
    games = db.relationship("Game", back_populates="match", cascade="all, delete-orphan", order_by="Game.board")

    @property
    def is_bye(self):
        return self.team2_id is None

    @property
    def is_complete(self):
        if self.is_bye:
            return True
        return len(self.games) == BOARDS and all(g.score1 is not None for g in self.games)

    @property
    def team1_score(self):
        return sum(g.score1 for g in self.games if g.score1 is not None)

    @property
    def team2_score(self):
        return sum(1 - g.score1 for g in self.games if g.score1 is not None)


class Game(db.Model):
    """One board. player1 plays for match.team1, player2 for match.team2.
    score1 is player1's score (1, 0.5, 0); NULL until the result is in."""

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("match.id"), nullable=False)
    board = db.Column(db.Integer, nullable=False)
    player1_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=False)
    player2_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=False)
    score1 = db.Column(db.Float, nullable=True)

    __table_args__ = (UniqueConstraint("match_id", "board"),)

    match = db.relationship("Match", back_populates="games")
    player1 = db.relationship("Player", foreign_keys=[player1_id])
    player2 = db.relationship("Player", foreign_keys=[player2_id])

    @property
    def score2(self):
        return None if self.score1 is None else 1 - self.score1