"""Team standings and individual board standings."""
import pytest

from app.standings import board_standings, team_standings

WIN_3_1 = ["1-0", "1-0", "1-0", "0-1"]
DRAW = ["1-0", "1-0", "0-1", "0-1"]


@pytest.fixture
def four(league):
    """A tournament with four teams, strongest first."""
    t = league.tournament()
    teams = [league.team(t, name, ratings=(r,) * 4)
             for name, r in (("Alpha", 1800), ("Bravo", 1700), ("Charlie", 1600), ("Delta", 1500))]
    return t, teams


def rows(t, **kwargs):
    return {row["team"].name: row for row in team_standings(t, **kwargs)}


class TestTeamStandings:
    def test_teams_without_games_start_at_zero(self, four):
        t, teams = four
        standings = team_standings(t)
        for row in standings:
            assert (row["mp"], row["gp"], row["played"], row["byes"]) == (0, 0, 0, 0)
        # With no results, teams are listed (and ranked) by rating.
        assert [row["team"] for row in standings] == teams
        assert [row["rank"] for row in standings] == [1, 2, 3, 4]

    def test_win_and_loss(self, league, four):
        t, (a, b, c, d) = four
        league.pair(t, 1, [(a, b), (c, d)])
        league.set_results(league.match_of(t, 1, a), WIN_3_1)
        r = rows(t)
        assert (r["Alpha A"]["mp"], r["Alpha A"]["gp"], r["Alpha A"]["played"]) == (1.0, 3.0, 1)
        assert (r["Bravo A"]["mp"], r["Bravo A"]["gp"], r["Bravo A"]["played"]) == (0.0, 1.0, 1)

    def test_two_two_is_a_drawn_match(self, league, four):
        t, (a, b, c, d) = four
        league.pair(t, 1, [(a, b), (c, d)])
        league.set_results(league.match_of(t, 1, a), DRAW)
        r = rows(t)
        assert r["Alpha A"]["mp"] == r["Bravo A"]["mp"] == 0.5

    def test_incomplete_match_is_not_counted(self, league, four):
        t, (a, b, c, d) = four
        league.pair(t, 1, [(a, b), (c, d)])
        league.set_results(league.match_of(t, 1, a), ["1-0", "1-0", "1-0", None])
        r = rows(t)
        assert (r["Alpha A"]["mp"], r["Alpha A"]["gp"], r["Alpha A"]["played"]) == (0, 0, 0)

    def test_results_accumulate_over_rounds(self, league, four):
        t, (a, b, c, d) = four
        league.pair(t, 1, [(a, b), (c, d)])
        league.set_results(league.match_of(t, 1, a), WIN_3_1)
        league.set_results(league.match_of(t, 1, c), DRAW)
        league.pair(t, 2, [(a, c), (b, d)])
        league.set_results(league.match_of(t, 2, a), ["0.5-0.5"] * 4)
        league.set_results(league.match_of(t, 2, b), ["0-1"] * 4)
        r = rows(t)
        assert (r["Alpha A"]["mp"], r["Alpha A"]["gp"], r["Alpha A"]["played"]) == (1.5, 5.0, 2)
        assert (r["Delta A"]["mp"], r["Delta A"]["gp"]) == (1.5, 6.0)

    @pytest.mark.parametrize("bye_type, mp, gp", [("full", 1.0, 2.5), ("half", 0.5, 2.0), ("zero", 0.0, 0.0)])
    def test_byes_score_once_their_round_has_a_result(self, league, bye_type, mp, gp):
        t = league.tournament()
        a, b, c = (league.team(t, n) for n in ("Alpha", "Bravo", "Charlie"))
        league.pair(t, 1, [(a, b)], byes=[(c, bye_type)])
        assert rows(t)["Charlie A"]["mp"] == 0  # round 1 has no results yet
        league.set_results(league.match_of(t, 1, a), WIN_3_1)
        row = rows(t)["Charlie A"]
        assert (row["mp"], row["gp"], row["byes"], row["played"]) == (mp, gp, 1, 0)

    def test_future_requested_byes_are_not_counted_yet(self, league, four):
        t, (a, b, c, d) = four
        league.add_bye(t, 3, a, "half")
        assert rows(t)["Alpha A"]["byes"] == 0

    def test_before_round_counts_only_earlier_rounds_including_their_byes(self, league):
        t = league.tournament()
        a, b, c = (league.team(t, n) for n in ("Alpha", "Bravo", "Charlie"))
        league.pair(t, 1, [(a, b)], byes=[(c, "full")])
        league.set_results(league.match_of(t, 1, a), WIN_3_1)
        league.pair(t, 2, [(a, c)], byes=[(b, "full")])
        league.set_results(league.match_of(t, 2, a), ["1-0"] * 4)
        before_2 = rows(t, before_round=2)
        assert (before_2["Alpha A"]["mp"], before_2["Charlie A"]["mp"], before_2["Bravo A"]["mp"]) == (1.0, 1.0, 0.0)
        assert rows(t, before_round=1)["Alpha A"]["mp"] == 0
        assert rows(t)["Alpha A"]["mp"] == 2.0

    def test_sorted_by_match_points(self, league, four):
        t, (a, b, c, d) = four
        league.pair(t, 1, [(a, b), (c, d)])
        league.set_results(league.match_of(t, 1, a), ["0-1"] * 4)
        league.set_results(league.match_of(t, 1, c), ["0-1"] * 4)
        assert [row["team"] for row in team_standings(t)][:2] == [b, d]

    def test_equal_match_points_are_ordered_by_rating(self, league, four):
        t, (a, b, c, d) = four
        league.pair(t, 1, [(a, b), (c, d)])
        league.set_results(league.match_of(t, 1, a), ["0-1"] * 4)
        league.set_results(league.match_of(t, 1, c), ["0-1"] * 4)
        ordered = [row["team"] for row in team_standings(t)]
        assert ordered == [b, d, a, c]
        assert [row["rank"] for row in team_standings(t)] == [1, 2, 3, 4]

    def test_tied_teams_share_a_rank_and_the_next_rank_is_skipped(self, league):
        t = league.tournament()
        a, b, c = (league.team(t, n, ratings=(1500,) * 4) for n in ("Alpha", "Bravo", "Charlie"))
        league.pair(t, 1, [(a, b)], byes=[(c, "zero")])
        league.set_results(league.match_of(t, 1, a), ["0-1"] * 4)
        ranks = {row["team"].name: row["rank"] for row in team_standings(t)}
        assert ranks == {"Bravo A": 1, "Alpha A": 2, "Charlie A": 2}

    @pytest.mark.xfail(reason="Known bug B4: equal match points are ranked by rating, not game points.")
    def test_equal_match_points_are_ordered_by_game_points(self, league, four):
        t, (a, b, c, d) = four
        league.pair(t, 1, [(a, b), (c, d)])
        league.set_results(league.match_of(t, 1, a), ["1-0", "1-0", "0.5-0.5", "0-1"])  # Alpha 2.5
        league.set_results(league.match_of(t, 1, c), ["1-0"] * 4)                       # Charlie 4
        assert [row["team"] for row in team_standings(t)][:2] == [c, a]

    def test_other_tournaments_are_ignored(self, league, four):
        t, (a, b, c, d) = four
        other = league.tournament("Other")
        x, y = league.team(other, "Xray"), league.team(other, "Yankee")
        league.pair(other, 1, [(x, y)])
        league.set_results(league.match_of(other, 1, x), ["1-0"] * 4)
        assert {row["team"] for row in team_standings(t)} == {a, b, c, d}
        assert all(row["mp"] == 0 for row in team_standings(t))


class TestBoardStandings:
    @pytest.fixture
    def match(self, league):
        t = league.tournament()
        home, away = league.team(t, "Home"), league.team(t, "Away")
        league.pair(t, 1, [(home, away)])
        return t, league.match_of(t, 1, home)

    def by_name(self, t):
        return {row["player"].name: (board, row["points"], row["games"])
                for board, board_rows in board_standings(t).items() for row in board_rows}

    def test_every_board_is_listed_even_when_empty(self, league):
        t = league.tournament()
        assert board_standings(t) == {1: [], 2: [], 3: [], 4: []}

    def test_players_score_on_their_board(self, league, match):
        t, m = match
        league.set_results(m, ["1-0", "0-1", "0.5-0.5", "1-0"])
        result = self.by_name(t)
        assert result["Home A B1"] == (1, 1.0, 1)
        assert result["Away A B2"] == (2, 1.0, 1)
        assert result["Home A B3"] == result["Away A B3"] == (3, 0.5, 1)
        assert result["Away A B4"] == (4, 0.0, 1)

    def test_unscored_games_are_ignored(self, league, match):
        t, m = match
        league.set_results(m, ["1-0", None, None, None])
        assert set(self.by_name(t)) == {"Home A B1", "Away A B1"}

    def test_forfeit_win_counts_and_absent_player_is_not_listed(self, league, match):
        t, m = match
        league.set_results(m, ["1F-0F", "0F-1F", "0F-0F", None])
        result = self.by_name(t)
        assert result["Home A B1"] == (1, 1.0, 1) and "Away A B1" not in result
        assert result["Away A B2"] == (2, 1.0, 1) and "Home A B2" not in result
        assert "Home A B3" not in result and "Away A B3" not in result

    def test_player_qualifies_for_the_lowest_board_played_with_all_points(self, league):
        t = league.tournament()
        home, away, third = league.team(t, "Home"), league.team(t, "Away"), league.team(t, "Third")
        league.pair(t, 1, [(home, away)])
        league.set_results(league.match_of(t, 1, home), ["1-0"] * 4)
        star = home.core_players[0]
        league.pair(t, 2, [(home, third)])
        players = [p.id for p in home.core_players]
        players[0], players[2] = players[2], players[0]  # star moves down to board 3
        league.set_results(league.match_of(t, 2, home), ["1-0"] * 4, home=players)
        board, points, games = self.by_name(t)[star.name]
        assert (board, points, games) == (3, 2.0, 2)
        assert star.name not in [row["player"].name for row in board_standings(t)[1]]

    def test_each_board_is_sorted_by_points_then_name(self, league):
        t = league.tournament()
        teams = [league.team(t, n) for n in ("Alpha", "Bravo", "Charlie", "Delta")]
        league.pair(t, 1, [(teams[0], teams[1]), (teams[2], teams[3])])
        league.set_results(league.match_of(t, 1, teams[0]), ["0-1"] * 4)
        league.set_results(league.match_of(t, 1, teams[2]), ["1-0"] * 4)
        names = [row["player"].name for row in board_standings(t)[1]]
        assert names == ["Bravo A B1", "Charlie A B1", "Alpha A B1", "Delta A B1"]

    def test_other_tournaments_are_ignored(self, league, match):
        t, m = match
        league.set_results(m, ["1-0"] * 4)
        other = league.tournament("Other")
        assert board_standings(other) == {1: [], 2: [], 3: [], 4: []}
