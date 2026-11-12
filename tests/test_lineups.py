"""Entering lineups and results on the round page: validation, forfeits,
eligibility, and substitutes added by pasting from Google Sheets."""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Player, RosterEntry
from app.pairings import set_round_pairings
from helpers import flashed, flashes


@pytest.fixture
def setup(league):
    """Lincoln A vs Roosevelt A at table 1, Lincoln B vs Washington A at table 2."""
    t = league.tournament()
    la = league.team(t, "Lincoln", ratings=(1800,) * 4)
    ra = league.team(t, "Roosevelt", ratings=(1700,) * 4)
    lb = league.team(t, "Lincoln", ratings=(1300,) * 4)
    wa = league.team(t, "Washington", ratings=(1200,) * 4)
    league.pair(t, 1, [(la, ra), (lb, wa)])
    return t, la, ra, lb, wa


def table(league, t, team):
    return league.match_of(t, 1, team)


def new_sub_fields(game, side, name, username, rating=""):
    return {f"{side}_new_name_{game.id}": name, f"{side}_new_username_{game.id}": username,
            f"{side}_new_rating_{game.id}": rating}


class TestSavingResults:
    def test_full_lineup_and_results(self, league, setup):
        t, la, ra, *_ = setup
        match = table(league, t, la)
        response = league.lineup(match, ["1-0", "0.5-0.5", "0-1", "1-0"])
        assert flashed(response, "Table 1 lineup saved.")
        assert match.is_complete and (match.home_game_points, match.away_game_points) == (2.5, 1.5)
        assert [g.home_player for g in match.games] == la.core_players

    def test_lineup_without_results(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        response = league.lineup(match, [None] * 4)
        assert flashed(response, "Table 1 lineup saved.")
        assert all(g.home_player_id and g.home_score is None for g in match.games)
        assert not match.is_complete

    def test_partial_results_leave_the_round_incomplete(self, league, setup):
        t, la, *_ = setup
        league.lineup(table(league, t, la), ["1-0", "1-0", None, None])
        assert not league.round(t, 1).is_complete
        assert league.round(t, 1).incomplete_tables == [1, 2]

    def test_completing_every_table_completes_the_round(self, league, setup):
        t, *_ = setup
        league.finish_round(t, 1)
        assert league.round(t, 1).is_complete

    def test_results_can_be_corrected_before_the_next_round(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        league.lineup(match, ["1-0"] * 4)
        league.lineup(match, ["0-1"] * 4)
        assert (match.home_game_points, match.away_game_points) == (0.0, 4.0)

    def test_results_can_be_cleared(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        league.lineup(match, ["1-0"] * 4)
        league.lineup(match, [None] * 4)
        assert all(g.home_score is None for g in match.games)


class TestForfeits:
    def test_away_forfeit_keeps_home_player_and_clears_away_player(self, league, setup):
        t, la, ra, *_ = setup
        match = table(league, t, la)
        response = league.lineup(match, ["1F-0F"] * 4)
        assert flashed(response, "Table 1 lineup saved.")
        game = match.games[0]
        assert game.home_player == la.core_players[0] and game.away_player is None
        assert (game.home_score, game.away_score, game.result_code) == (1.0, 0.0, "1F-0F")

    def test_home_forfeit(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        league.lineup(match, ["0F-1F"] * 4)
        assert all(g.home_player_id is None and g.away_score == 1.0 for g in match.games)

    def test_double_forfeit_needs_no_players(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        response = league.lineup(match, ["0F-0F"] * 4, home=[None] * 4, away=[None] * 4)
        assert flashed(response, "Table 1 lineup saved.")
        assert all(g.result_code == "0F-0F" for g in match.games)

    def test_forfeiting_side_may_be_left_empty(self, league, setup):
        t, la, ra, *_ = setup
        match = table(league, t, la)
        away = [p.id for p in ra.core_players[:3]] + [None]
        response = league.lineup(match, ["1-0", "1-0", "1-0", "1F-0F"], away=away)
        assert flashed(response, "Table 1 lineup saved."), flashes(response)


class TestValidation:
    def _unchanged(self, match):
        return all(g.home_player_id is None and g.home_score is None for g in match.games)

    def test_played_result_needs_both_players(self, league, setup):
        t, la, ra, *_ = setup
        match = table(league, t, la)
        away = [p.id for p in ra.core_players[:3]] + [None]
        response = league.lineup(match, ["1-0"] * 4, away=away)
        assert flashed(response, "Board 4: a 1-0 result needs a Roosevelt A player. Use a forfeit result if nobody played.")
        assert self._unchanged(match)

    def test_unknown_result_code(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        response = league.lineup(match, ["2-0", None, None, None])
        assert flashed(response, "Board 1: choose a valid result.")

    def test_player_id_must_be_a_number(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        response = league.lineup(match, [None] * 4, home=["abc", None, None, None])
        assert flashed(response, "Choose a valid roster player for each board.")

    def test_player_must_be_on_the_team_roster(self, league, setup):
        t, la, ra, lb, wa = setup
        match = table(league, t, la)
        home = [wa.core_players[0].id] + [p.id for p in la.core_players[1:]]
        response = league.lineup(match, [None] * 4, home=home)
        assert flashed(response, "Home board 1: choose a player from Lincoln A's roster.")
        assert self._unchanged(match)

    def test_player_cannot_play_two_boards(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        star = la.core_players[0].id
        response = league.lineup(match, [None] * 4, home=[star, star, None, None])
        assert flashed(response, "A player can only be assigned to one board in this match.")

    def test_player_cannot_play_in_two_matches_of_a_round(self, league, setup):
        t, la, ra, lb, wa = setup
        shared = lb.core_players[0]
        db.session.add(RosterEntry(team=la, player=shared, position=9.0, is_core=False))
        db.session.commit()
        assert flashed(league.lineup(table(league, t, lb), [None] * 4), "Table 2 lineup saved.")
        home = [shared.id] + [p.id for p in la.core_players[1:]]
        response = league.lineup(table(league, t, la), [None] * 4, home=home)
        assert flashed(response, "A player can only be assigned to one match in a round.")

    def test_higher_core_player_cannot_play_for_lower_team(self, league, setup):
        t, la, ra, lb, wa = setup
        star = la.core_players[0]
        db.session.add(RosterEntry(team=lb, player=star, position=9.0, is_core=False))
        db.session.commit()
        home = [star.id] + [p.id for p in lb.core_players[1:]]
        response = league.lineup(table(league, t, lb), [None] * 4, home=home)
        assert flashed(response, "Lincoln A's core player cannot play for the lower team Lincoln B.")

    def test_lower_core_player_may_play_up(self, league, setup):
        t, la, ra, lb, wa = setup
        riser = lb.core_players[0]
        db.session.add(RosterEntry(team=la, player=riser, position=9.0, is_core=False))
        db.session.commit()
        home = [riser.id] + [p.id for p in la.core_players[1:]]
        response = league.lineup(table(league, t, la), ["1-0"] * 4, home=home)
        assert flashed(response, "Table 1 lineup saved."), flashes(response)

    def test_lineup_needs_both_teams(self, league):
        t = league.tournament()
        a, b = league.team(t, "Alpha"), league.team(t, "Bravo")
        league.pair(t, 1, [(a, b)])
        set_round_pairings(t, 1, {1: (a.id, None)}, [])
        db.session.commit()
        match = league.match_of(t, 1, a)
        response = league.post(f"/tournaments/{t.id}/rounds/1", {"action": "lineup", "match_id": match.id})
        assert flashed(response, "Assign both teams before entering player lineups.")

    def test_match_must_belong_to_the_round(self, league, setup):
        t, *_ = setup
        response = league.post(f"/tournaments/{t.id}/rounds/1", {"action": "lineup", "match_id": "99999"})
        assert flashed(response, "That match does not exist in this round.")

    @pytest.mark.xfail(raises=IntegrityError, reason="Known bug B2: swapping two players' boards breaks "
                                                "UNIQUE(match, home_player) mid-update (500).")
    def test_players_can_swap_boards(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        league.lineup(match, [None] * 4)
        order = [p.id for p in la.core_players]
        order[0], order[1] = order[1], order[0]
        league.lineup(match, [None] * 4, home=order)
        assert [g.home_player_id for g in match.games] == order


class TestPastedSubstitutes:
    def test_new_player_is_added_as_a_substitute_and_plays(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        game = match.games[3]
        home = [p.id for p in la.core_players[:3]] + ["new"]
        response = league.lineup(match, ["1-0"] * 4, home=home,
                                 extra=new_sub_fields(game, "home", "Pasted Pat", "PastedPat", "1450"))
        assert flashed(response, "Table 1 lineup saved."), flashes(response)
        pat = Player.query.filter_by(lichess_username="PastedPat").one()
        assert (pat.name, pat.rating, pat.school_id) == ("Pasted Pat", 1450, la.school_id)
        entry = next(e for e in la.roster if e.player == pat)
        assert not entry.is_core and entry.position == 5.0
        assert match.games[3].home_player == pat

    def test_existing_school_player_is_reused_by_username(self, league, setup):
        t, la, ra, lb, wa = setup
        known = lb.core_players[0]
        match = table(league, t, la)
        home = [p.id for p in la.core_players[:3]] + ["new"]
        extra = new_sub_fields(match.games[3], "home", "Typo Name", known.lichess_username.upper())
        response = league.lineup(match, ["1-0"] * 4, home=home, extra=extra)
        assert flashed(response, "Table 1 lineup saved."), flashes(response)
        assert match.games[3].home_player == known
        assert Player.query.count() == 16

    def test_username_from_another_school_is_refused(self, league, setup):
        t, la, ra, lb, wa = setup
        outsider = wa.core_players[0]
        match = table(league, t, la)
        home = [p.id for p in la.core_players[:3]] + ["new"]
        extra = new_sub_fields(match.games[3], "home", "Someone", outsider.lichess_username)
        response = league.lineup(match, ["1-0"] * 4, home=home, extra=extra)
        assert flashed(response, f"belongs to {outsider.name} of Washington")
        assert outsider not in [e.player for e in la.roster]

    def test_higher_core_player_pasted_onto_lower_team_is_refused(self, league, setup):
        t, la, ra, lb, wa = setup
        star = la.core_players[0]
        match = table(league, t, lb)
        home = [p.id for p in lb.core_players[:3]] + ["new"]
        extra = new_sub_fields(match.games[3], "home", star.name, star.lichess_username)
        response = league.lineup(match, ["1-0"] * 4, home=home, extra=extra)
        assert flashed(response, "is core on Lincoln A and can't join the lower team Lincoln B.")
        assert star not in [e.player for e in lb.roster]

    @pytest.mark.parametrize("name, username, rating, message", [
        ("", "nouser", "", "a new substitute needs a name and Lichess username."),
        ("No Username", "", "", "a new substitute needs a name and Lichess username."),
        ("Bad Rating", "badrating", "strong", "rating 'strong' isn't a number."),
    ])
    def test_incomplete_pasted_player_is_refused_and_nothing_is_saved(self, league, setup, name, username, rating, message):
        t, la, *_ = setup
        match = table(league, t, la)
        home = [p.id for p in la.core_players[:3]] + ["new"]
        response = league.lineup(match, ["1-0"] * 4, home=home,
                                 extra=new_sub_fields(match.games[3], "home", name, username, rating))
        assert flashed(response, message), flashes(response)
        assert Player.query.count() == 16 and len(la.roster) == 4
        assert all(g.home_score is None for g in match.games)

    def test_pasted_player_on_a_forfeiting_side_is_ignored(self, league, setup):
        t, la, *_ = setup
        match = table(league, t, la)
        home = [p.id for p in la.core_players[:3]] + ["new"]
        extra = new_sub_fields(match.games[3], "home", "Ghost", "ghost")
        response = league.lineup(match, ["1-0", "1-0", "1-0", "0F-1F"], home=home, extra=extra)
        assert flashed(response, "Table 1 lineup saved."), flashes(response)
        assert Player.query.filter_by(lichess_username="ghost").count() == 0
