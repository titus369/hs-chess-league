"""Seeding (pairing numbers) and roster reports."""
from app.rosters import CORE_SIZE, format_roster_report, pairing_numbers, roster_report, seeded_teams


def test_teams_are_seeded_by_average_core_rating(league):
    t = league.tournament()
    low = league.team(t, "Low", ratings=(1000, 1000, 1000, 1000))
    high = league.team(t, "High", ratings=(2000, 2000, 2000, 2000))
    mid = league.team(t, "Mid", ratings=(1500, 1500, 1500, 1500))
    seeds = seeded_teams(t.id)
    assert [s.team for s in seeds] == [high, mid, low]
    assert [s.number for s in seeds] == [1, 2, 3]
    assert [s.avg_rating for s in seeds] == [2000, 1500, 1000]


def test_equal_ratings_seed_by_team_number_then_entry_order(league):
    t = league.tournament()
    first = league.team(t, "Alpha")
    lincoln_a = league.team(t, "Lincoln")
    lincoln_b = league.team(t, "Lincoln")
    assert [s.team for s in seeded_teams(t.id)] == [first, lincoln_a, lincoln_b]


def test_substitutes_do_not_affect_seeding(league):
    t = league.tournament()
    a = league.team(t, "Alpha", ratings=(1500,) * 4)
    b = league.team(t, "Bravo", ratings=(1400,) * 4)
    league.player(b, "Bravo Ringer", rating=2800)
    assert [s.team for s in seeded_teams(t.id)] == [a, b]


def test_core_count_and_completeness(league):
    t = league.tournament()
    team = league.team(t, "Alpha")
    seed = seeded_teams(t.id)[0]
    assert seed.core_count == CORE_SIZE and seed.is_complete


def test_seeds_only_include_this_tournament(league):
    t1, t2 = league.tournament("One"), league.tournament("Two")
    league.team(t1, "Alpha")
    league.team(t2, "Bravo")
    assert [s.team.name for s in seeded_teams(t1.id)] == ["Alpha A"]


def test_pairing_numbers(league):
    t = league.tournament()
    weak = league.team(t, "Weak", ratings=(900,) * 4)
    strong = league.team(t, "Strong", ratings=(1900,) * 4)
    assert pairing_numbers(t.id) == {strong.id: 1, weak.id: 2}


def test_roster_report_lists_core_and_substitutes(league):
    t = league.tournament()
    a = league.team(t, "Lincoln")
    b = league.team(t, "Lincoln", ratings=(1200, 1100, 1000, None))
    league.player(b, "Lincoln Sub", rating=900)
    report = {r.team_name: r for r in roster_report(t.id)}
    lines = report["Lincoln B"].players
    assert [line.is_core for line in lines] == [True, True, True, True, False]
    assert lines[3].rating is None
    assert lines[4].name == "Lincoln Sub" and lines[4].core_team is None
    assert report["Lincoln B"].core_count == 4
    assert report["Lincoln A"].number == 1


def test_roster_report_names_the_core_team_of_a_borrowed_player(league):
    t = league.tournament()
    a = league.team(t, "Lincoln")
    b = league.team(t, "Lincoln")
    from app import db
    from app.models import RosterEntry
    db.session.add(RosterEntry(team=a, player=b.core_players[0], position=9.0, is_core=False))
    db.session.commit()
    line = next(line for line in roster_report(t.id)[0].players if not line.is_core)
    assert line.core_team == "Lincoln B"


def test_format_roster_report(league):
    t = league.tournament()
    league.team(t, "Lincoln", ratings=(1600, None, 1400, 1300))
    text = format_roster_report(roster_report(t.id))
    assert text.startswith("#1  Lincoln A")
    assert "unrated" in text and "CORE" in text
