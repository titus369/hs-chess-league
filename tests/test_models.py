"""Model properties, scoring rules, eligibility and database constraints."""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    BYE_POINTS, Bye, Game, Match, RosterEntry, Team, match_points_for, position_between,
)


@pytest.mark.parametrize("game_points, match_points", [
    (None, 0.0), (4.0, 1.0), (3.5, 1.0), (3.0, 1.0), (2.5, 1.0), (2.0, 0.5), (1.5, 0.0), (0.0, 0.0),
])
def test_match_points_for(game_points, match_points):
    assert match_points_for(game_points) == match_points


@pytest.mark.parametrize("lo, hi, expected", [(None, None, 1.0), (None, 3.0, 2.0), (2.0, None, 3.0), (1.0, 2.0, 1.5)])
def test_position_between(lo, hi, expected):
    assert position_between(lo, hi) == expected


@pytest.mark.parametrize("bye_type, match_points, game_points", [
    ("full", 1.0, 2.5), ("half", 0.5, 2.0), ("zero", 0.0, 0.0),
])
def test_bye_points(league, bye_type, match_points, game_points):
    t = league.tournament()
    team = league.team(t, "Lincoln")
    league.add_bye(t, 1, team, bye_type)
    bye = Bye.query.one()
    assert (bye.match_points, bye.game_points) == (match_points, game_points) == BYE_POINTS[bye_type]


class TestTeam:
    def test_name_uses_a_letter_per_team_of_the_school(self, league):
        t = league.tournament()
        assert [league.team(t, "Lincoln").name, league.team(t, "Lincoln").name] == ["Lincoln A", "Lincoln B"]

    def test_name_falls_back_to_a_number_after_z(self, league):
        t = league.tournament()
        team = league.team(t, "Lincoln")
        team.team_number = 27
        assert team.name == "Lincoln 27"

    def test_core_players_are_in_board_order(self, league):
        t = league.tournament()
        team = league.team(t, "Lincoln")
        league.player(team, "Sub", rating=2000)
        assert [p.name for p in team.core_players] == [f"Lincoln A B{b}" for b in range(1, 5)]

    def test_avg_rating_counts_core_players_only_and_unrated_as_100(self, league):
        t = league.tournament()
        team = league.team(t, "Lincoln", ratings=(1600, None, 1400, None))
        league.player(team, "Strong Sub", rating=3000)
        assert team.avg_rating == (1600 + 100 + 1400 + 100) / 4

    def test_avg_rating_without_core_players_is_100(self, league):
        t = league.tournament()
        team = Team(tournament=t, school=league.school("Empty"), team_number=1)
        db.session.add(team)
        db.session.commit()
        assert team.avg_rating == 100

    def test_player_core_team_and_is_core(self, league):
        t = league.tournament()
        team = league.team(t, "Lincoln")
        sub = league.player(team, "Sub")
        core = team.core_players[0]
        assert (core.core_team, core.is_core) == (team, True)
        assert (sub.core_team, sub.is_core) == (None, False)


class TestBlockingCoreTeam:
    """A core player of a school's higher team can't play for its lower team."""

    @pytest.fixture
    def teams(self, league):
        t = league.tournament()
        return {
            "LA": league.team(t, "Lincoln"), "LB": league.team(t, "Lincoln"),
            "RA": league.team(t, "Roosevelt"), "RB": league.team(t, "Roosevelt"),
        }

    def test_higher_core_player_is_blocked_from_lower_team(self, teams):
        assert teams["LA"].core_players[0].blocking_core_team(teams["LB"]) == teams["LA"]

    def test_core_player_may_play_for_own_team(self, teams):
        assert teams["LA"].core_players[0].blocking_core_team(teams["LA"]) is None

    def test_lower_core_player_may_play_up(self, teams):
        assert teams["LB"].core_players[0].blocking_core_team(teams["LA"]) is None

    def test_rule_only_applies_within_a_school(self, teams):
        assert teams["LA"].core_players[0].blocking_core_team(teams["RB"]) is None

    def test_substitutes_are_never_blocked(self, league, teams):
        sub = league.player(teams["LA"], "Lincoln Sub")
        assert sub.blocking_core_team(teams["LB"]) is None

    def test_school_override_checks_a_team_changing_school(self, teams):
        player = teams["LA"].core_players[0]
        assert player.blocking_core_team(teams["RB"], school_id=teams["LA"].school_id) == teams["LA"]


class TestMatchAndGame:
    @pytest.fixture
    def match(self, league):
        t = league.tournament()
        home, away = league.team(t, "Lincoln"), league.team(t, "Roosevelt")
        league.pair(t, 1, [(home, away)])
        return league.match_of(t, 1, home)

    def test_new_match_has_one_empty_game_per_board(self, match):
        assert [g.board for g in match.games] == [1, 2, 3, 4]
        assert all(g.home_score is None and g.home_player_id is None for g in match.games)

    def test_incomplete_match_has_no_match_points(self, league, match):
        league.set_results(match, ["1-0", "1-0", "1-0", None])
        assert not match.is_complete
        assert match.home_game_points == 3.0
        assert match.home_match_points is None and match.away_match_points is None

    @pytest.mark.parametrize("results, home_mp, away_mp, home_gp, away_gp", [
        (["1-0", "1-0", "1-0", "0-1"], 1.0, 0.0, 3.0, 1.0),
        (["1-0", "1-0", "0.5-0.5", "0-1"], 1.0, 0.0, 2.5, 1.5),
        (["1-0", "1-0", "0-1", "0-1"], 0.5, 0.5, 2.0, 2.0),
        (["0.5-0.5"] * 4, 0.5, 0.5, 2.0, 2.0),
        (["0-1", "0-1", "0-1", "1-0"], 0.0, 1.0, 1.0, 3.0),
        (["1F-0F", "1F-0F", "1F-0F", "1F-0F"], 1.0, 0.0, 4.0, 0.0),
    ])
    def test_complete_match_points(self, league, match, results, home_mp, away_mp, home_gp, away_gp):
        league.set_results(match, results)
        assert match.is_complete
        assert (match.home_game_points, match.away_game_points) == (home_gp, away_gp)
        assert (match.home_match_points, match.away_match_points) == (home_mp, away_mp)

    @pytest.mark.xfail(reason="Known bug B3: match points use fixed thresholds (2.5 wins, 2 draws), "
                              "so a 2-1 win with a double-forfeit board scores 0.5-0. "
                              "Assumes the league rule is 'more game points wins'.")
    def test_double_forfeit_board_does_not_turn_a_win_into_a_draw(self, league, match):
        league.set_results(match, ["1-0", "1-0", "0-1", "0F-0F"])
        assert (match.home_match_points, match.away_match_points) == (1.0, 0.0)

    @pytest.mark.parametrize("code", ["1-0", "0-1", "0.5-0.5", "1F-0F", "0F-1F", "0F-0F"])
    def test_result_code_round_trips(self, league, match, code):
        league.set_results(match, [code] * 4)
        assert all(g.result_code == code for g in match.games)
        assert all(g.is_forfeit == ("F" in code) for g in match.games)

    def test_unplayed_game_has_no_result_code(self, match):
        assert match.games[0].result_code is None and not match.games[0].is_forfeit

    def test_home_has_white_on_boards_1_and_3(self, league, match):
        league.set_results(match, [None] * 4)
        for game in match.games:
            assert game.home_is_white == (game.board in (1, 3))
            white, black = (game.home_player, game.away_player) if game.board in (1, 3) else (game.away_player, game.home_player)
            assert (game.white_player, game.black_player) == (white, black)

    def test_game_code(self, match):
        assert [g.code for g in match.games] == ["1.1.1", "1.1.2", "1.1.3", "1.1.4"]


class TestConstraints:
    """The database rejects impossible data even if a route forgets to check."""

    @pytest.fixture
    def match(self, league):
        t = league.tournament()
        home, away = league.team(t, "Lincoln"), league.team(t, "Roosevelt")
        league.pair(t, 1, [(home, away)])
        return league.match_of(t, 1, home)

    def _rejects(self):
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_score_must_be_0_half_or_1(self, match):
        game = match.games[0]
        game.home_score, game.away_score = 2.0, 0.0
        self._rejects()

    def test_both_sides_must_be_scored_together(self, match):
        match.games[0].home_score = 1.0
        self._rejects()

    def test_played_game_scores_must_add_to_one(self, league, match):
        game = match.games[0]
        game.home_player_id = match.home_team.core_players[0].id
        game.away_player_id = match.away_team.core_players[0].id
        game.home_score, game.away_score = 1.0, 1.0
        self._rejects()

    def test_absent_side_cannot_score(self, match):
        game = match.games[0]
        game.home_score, game.away_score = 1.0, 0.0
        self._rejects()

    def test_team_cannot_play_itself(self, match):
        match.away_team_id = match.home_team_id
        self._rejects()

    def test_team_cannot_be_home_twice_in_a_round(self, league, match):
        db.session.add(Match(round=match.round, table_number=2, home_team_id=match.home_team_id))
        self._rejects()

    def test_two_games_cannot_share_a_board(self, match):
        db.session.add(Game(match=match, board=1))
        self._rejects()

    def test_player_is_core_on_at_most_one_team(self, league):
        t = league.tournament()
        a, b = league.team(t, "Lincoln"), league.team(t, "Lincoln")
        db.session.add(RosterEntry(team=b, player=a.core_players[0], position=9.0, is_core=True))
        self._rejects()

    def test_player_appears_once_per_roster(self, league):
        t = league.tournament()
        a = league.team(t, "Lincoln")
        db.session.add(RosterEntry(team=a, player=a.core_players[0], position=9.0, is_core=False))
        self._rejects()

    def test_bye_type_and_source_are_checked(self, league):
        t = league.tournament()
        team = league.team(t, "Lincoln")
        with pytest.raises(IntegrityError):
            league.add_bye(t, 1, team, "double")
        db.session.rollback()
        with pytest.raises(IntegrityError):
            league.add_bye(t, 1, team, "half", source="whenever")
        db.session.rollback()

    def test_team_has_one_bye_per_round(self, league):
        t = league.tournament()
        team = league.team(t, "Lincoln")
        league.add_bye(t, 1, team, "half")
        with pytest.raises(IntegrityError):
            league.add_bye(t, 1, team, "zero")
        db.session.rollback()
