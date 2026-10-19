from dataclasses import dataclass
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload, selectinload
from typing import Optional

from . import db
from .models import (
    BOARDS,
    Game,
    Match,
    Player,
    RosterEntry,
    School,
    Team,
    position_between,
)

CORE_SIZE = BOARDS

@dataclass(frozen=True)
class TeamSeed:
    number: int
    team: Team
    avg_rating: float
    core_count: int

    @property
    def is_complete(self):
        return self.core_count == CORE_SIZE

def seeded_teams(tournament_id):
    teams = (Team.query.filter_by(tournament_id = tournament_id).options(joinedload(Team.school), selectinload(Team.roster).joinedload(RosterEntry.player),).all())

    teams.sort(key=lambda t: (-t.avg_rating, t.team_number, t.id))
    return [TeamSeed(number=i, team=t, avg_rating=t.avg_rating, core_count=len(t.core_players)) for i, t in enumerate(teams, start=1)]

def pairing_numbers(tournament_id):
    return {s.team.id: s.number for s in seeded_teams(tournament_id)}


@dataclass(frozen=True)
class RosterLine:
    player_id: int
    name: str
    lichess_username: str
    rating: Optional[int]
    is_core: bool
    core_team:Optional[str]

@dataclass(frozen=True)
class TeamRoster:
    number: int
    team_id: int
    team_name: str
    avg_rating: float
    core_count: int
    players: tuple

def roster_report(tournament_id):
    seeds = seeded_teams(tournament_id)

    core_of = {
        e.player_id: s.team.name
        for s in seeds
        for e in s.team.roster
        if e.is_core
    }

    report = []
    for s in seeds:
        lines=tuple(
                RosterLine(player_id=e.player.id,
                            name=e.player.name,
                            lichess_username=e.player.lichess_username,
                            rating=e.player.rating,
                            is_core=e.is_core,
                            core_team=core_of.get(e.player_id),
                        )
                for e in s.team.roster)
        
        report.append(
            TeamRoster(
                number=s.number,
                team_id=s.team.id,
                team_name=s.team.name,
                avg_rating=s.avg_rating,
                core_count=s.core_count,
                players=lines,
            )
        )

    return report

def format_roster_report(report):
    out = []
    for t in report:
        note = "" if t.core_count == CORE_SIZE else f"  ** only {t.core_count} core **"
        out.append(f"#{t.number}  {t.team_name}   avg {t.avg_rating:.1f}{note}")
        for p in t.players:
            tag = "CORE" if p.is_core else "sub"
            rating = str(p.rating) if p.rating is not None else "unrated"
            elsewhere = f"   (core on {p.core_team})" if not p.is_core and p.core_team else ""
            out.append(f"    {tag:<5}{p.name:<24}{rating:>8}   lichess: {p.lichess_username}{elsewhere}")
        out.append("")
    return "\n".join(out)