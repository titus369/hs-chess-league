"""Bye requests (team roster page), late-join byes, and the half-point bye cap."""
import pytest

from app.models import MAX_HALF_BYES, Bye
from app.pairings import first_unpaired_round, late_join_bye_plan
from helpers import flashed, flashes, page_text


def request_bye(league, team, number, bye_type):
    return league.post(f"/tournaments/{team.tournament_id}/teams/{team.id}/roster/byes",
                       {"round_number": str(number), "bye_type": bye_type})


@pytest.fixture
def three(league):
    t = league.tournament(rounds=7)
    return t, [league.team(t, school) for school in ("Alpha", "Bravo", "Charlie")]


class TestRosterPageRequests:
    def test_request_a_half_bye_for_a_future_round(self, league, three):
        t, (a, *_) = three
        response = request_bye(league, a, 3, "half")
        assert flashed(response, "Saved half-point bye request for Alpha A in round 3.")
        assert league.byes_of(a) == {3: ("half", "requested")}

    def test_change_a_request_from_half_to_zero(self, league, three):
        t, (a, *_) = three
        request_bye(league, a, 3, "half")
        request_bye(league, a, 3, "zero")
        assert league.byes_of(a) == {3: ("zero", "requested")}

    def test_remove_a_future_request(self, league, three):
        t, (a, *_) = three
        request_bye(league, a, 3, "half")
        response = request_bye(league, a, 3, "")
        assert flashed(response, "Removed Alpha A's half-point bye for round 3.")
        assert league.byes_of(a) == {}

    def test_removing_a_bye_that_does_not_exist(self, league, three):
        t, (a, *_) = three
        assert flashed(request_bye(league, a, 3, ""), "Alpha A has no bye in round 3.")

    def test_fourth_half_bye_becomes_a_zero_bye(self, league, three):
        t, (a, *_) = three
        for number in (2, 3, 4):
            request_bye(league, a, number, "half")
        response = request_bye(league, a, 5, "half")
        assert flashed(response, f"already has {MAX_HALF_BYES} half-point byes")
        assert league.byes_of(a)[5] == ("zero", "requested")

    def test_changing_an_existing_half_bye_does_not_count_against_itself(self, league, three):
        t, (a, *_) = three
        for number in (2, 3, 4):
            request_bye(league, a, number, "half")
        request_bye(league, a, 4, "half")
        assert league.byes_of(a)[4] == ("half", "requested")

    @pytest.mark.parametrize("number, bye_type, message", [
        ("0", "half", "Choose a valid tournament round."),
        ("8", "half", "Choose a valid tournament round."),
        ("x", "half", "Choose a valid tournament round."),
        ("2", "full", "Choose a half-point or zero-point bye."),
    ])
    def test_invalid_requests(self, league, three, number, bye_type, message):
        t, (a, *_) = three
        assert flashed(request_bye(league, a, number, bye_type), message)
        assert Bye.query.count() == 0

    def test_requests_cannot_be_added_once_a_round_is_paired(self, league, three):
        t, (a, b, c) = three
        league.pair(t, 1, [(a, b)], byes=[(c, "full")])
        response = request_bye(league, a, 1, "half")
        assert flashed(response, "Round 1 has already been paired")
        assert 1 not in league.byes_of(a)

    def test_bye_in_a_completed_round_cannot_be_removed_here(self, league, three):
        t, (a, b, c) = three
        league.pair(t, 1, [(a, b)], byes=[(c, "half")])
        league.finish_round(t, 1)
        response = request_bye(league, c, 1, "")
        assert flashed(response, "Round 1 is complete")
        assert league.byes_of(c) == {1: ("half", "requested")}

    def test_removing_a_bye_in_a_paired_current_round_leaves_the_team_unplaced(self, league, three):
        t, (a, b, c) = three
        league.pair(t, 1, [(a, b)], byes=[(c, "half")])
        response = request_bye(league, c, 1, "")
        assert flashed(response, "Charlie A is not placed in round 1 now")
        assert league.byes_of(c) == {}

    def test_requests_in_a_locked_round_are_refused(self, league, three):
        t, (a, b, c) = three
        league.pair(t, 1, [(a, b)], byes=[(c, "full")])
        league.finish_round(t, 1)
        league.pair(t, 2, [(a, c)], byes=[(b, "full")])
        response = request_bye(league, c, 1, "")
        assert flashed(response, "Round 1 is locked")

    def test_roster_page_lists_each_round(self, league, three):
        t, (a, *_) = three
        request_bye(league, a, 2, "half")
        page = page_text(league.get(f"/tournaments/{t.id}/teams/{a.id}/roster"))
        assert all(f"Round {n}" in page for n in range(1, 8))
        assert '<option value="half" selected>' in page


class TestLateJoinPlan:
    @pytest.mark.parametrize("join_round, plan", [
        (1, []),
        (2, [(1, "half")]),
        (4, [(1, "half"), (2, "half"), (3, "half")]),
        (6, [(1, "half"), (2, "half"), (3, "half"), (4, "zero"), (5, "zero")]),
    ])
    def test_half_byes_up_to_the_cap_then_zero(self, join_round, plan):
        assert late_join_bye_plan(join_round) == plan

    def test_first_unpaired_round(self, league, three):
        t, (a, b, c) = three
        assert first_unpaired_round(t) == 1
        league.add_bye(t, 1, a, "half")  # a requested bye alone doesn't pair a round
        assert first_unpaired_round(t) == 1
        league.pair(t, 1, [(b, c)], byes=[(a, "half")])
        assert first_unpaired_round(t) == 2

    def test_first_unpaired_round_after_the_last_round(self, league):
        t = league.tournament(rounds=1)
        a, b = league.team(t, "Alpha"), league.team(t, "Bravo")
        league.pair(t, 1, [(a, b)])
        assert first_unpaired_round(t) == 2


class TestLateJoinByes:
    def pair_rounds(self, league, t, count):
        for number in range(1, count + 1):
            league.generate(t, number)
            league.finish_round(t, number)

    def test_joiner_after_one_round_may_request_two_more_half_byes(self, league, three):
        t, _ = three
        self.pair_rounds(league, t, 1)
        response = league.post_team(t, "Delta", byes={3: "half", 5: "half"})
        assert flashed(response, "Added Delta A"), flashes(response)
        delta = league.team_named(t, "Delta A")
        assert league.byes_of(delta) == {1: ("half", "late_join"), 3: ("half", "requested"), 5: ("half", "requested")}

    def test_joiner_cannot_go_over_the_half_bye_cap(self, league, three):
        t, _ = three
        self.pair_rounds(league, t, 1)
        response = league.post_team(t, "Delta", byes={3: "half", 4: "half", 5: "half"})
        assert flashed(response, "already gets 1 late-join half-point bye, so it can request at most 2 more.")
        assert all(team.name != "Delta A" for team in t.teams)

    def test_joiner_after_four_rounds_gets_three_half_and_one_zero_bye(self, league, three):
        t, _ = three
        self.pair_rounds(league, t, 4)
        response = league.post_team(t, "Delta")
        assert flashed(response, "(3 half-point and 1 zero-point)")
        delta = league.team_named(t, "Delta A")
        assert league.byes_of(delta) == {1: ("half", "late_join"), 2: ("half", "late_join"),
                                         3: ("half", "late_join"), 4: ("zero", "late_join")}

    def test_joiner_who_used_the_cap_may_only_request_zero_byes(self, league, three):
        t, _ = three
        self.pair_rounds(league, t, 3)
        assert "only zero-point byes can be requested" in page_text(league.get(f"/tournaments/{t.id}"))
        response = league.post_team(t, "Delta", byes={5: "half"})
        assert flashed(response, "at most 0 more")
        league.post_team(t, "Delta", byes={5: "zero"})
        assert league.byes_of(league.team_named(t, "Delta A"))[5] == ("zero", "requested")

    def test_late_join_byes_are_fixed_on_the_roster_page(self, league, three):
        t, _ = three
        self.pair_rounds(league, t, 1)
        league.post_team(t, "Delta")
        delta = league.team_named(t, "Delta A")
        response = request_bye(league, delta, 1, "")
        assert flashed(response, "Delta A joined late; its round 1 bye is fixed")
        assert league.byes_of(delta) == {1: ("half", "late_join")}
        page = page_text(league.get(f"/tournaments/{t.id}/teams/{delta.id}/roster"))
        assert "Half-point bye (late join)" in page and "Joined late, in round 2." in page

    def test_late_join_byes_count_toward_later_requests(self, league, three):
        t, _ = three
        self.pair_rounds(league, t, 2)
        league.post_team(t, "Delta")  # 2 late-join half byes
        delta = league.team_named(t, "Delta A")
        request_bye(league, delta, 4, "half")
        response = request_bye(league, delta, 5, "half")
        assert flashed(response, "already has 3 half-point byes")
        assert league.byes_of(delta)[5] == ("zero", "requested")


class TestRoundPageByes:
    def test_manual_fourth_half_bye_is_saved_as_zero_with_a_warning(self, league):
        t = league.tournament()
        a, b, c = (league.team(t, s) for s in ("Alpha", "Bravo", "Charlie"))
        for number in (1, 2, 3):
            league.add_bye(t, number + 3, c, "half")
        warnings = league.pair(t, 1, [(a, b)], byes=[(c, "half")])
        assert any("defaulted as a zero-point bye" in w for w in warnings)
        assert league.byes_of(c)[1] == ("zero", "requested")

    def test_requested_byes_are_shown_on_the_round_page(self, league):
        t = league.tournament()
        a = league.team(t, "Alpha")
        league.add_bye(t, 1, a, "half")
        page = page_text(league.get(f"/tournaments/{t.id}/rounds/1"))
        assert "Alpha A: half-point bye" in page

    def test_generated_pairings_keep_requested_byes(self, league):
        t = league.tournament()
        teams = [league.team(t, s) for s in ("Alpha", "Bravo", "Charlie", "Delta", "Echo")]
        league.add_bye(t, 1, teams[0], "half")
        league.generate(t, 1)
        placed = {team_id for pair in league.opponents(t, 1) for team_id in pair}
        assert teams[0].id not in placed and len(placed) == 4
        assert league.byes_of(teams[0]) == {1: ("half", "requested")}
        assert Bye.query.filter_by(source="odd").count() == 0
