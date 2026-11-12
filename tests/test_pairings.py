"""Rounds: creating, generating, manually setting and switching pairings, adding
tables, clearing, and the rule that earlier rounds lock once a later round is paired."""
import itertools

import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Bye, Match, Round
from app.pairings import (
    PairingError, add_match, create_round, previous_round_problem, resize_round,
    round_has_pairings, round_lock_problem, set_round_pairings,
)
from helpers import flashed, flashes, page_text


def save_form(pairs, byes=()):
    """Form data for the round page's Save pairings form."""
    data = {}
    for table, (home, away) in enumerate(pairs, start=1):
        data[f"home_{table}"] = str(home.id) if home else ""
        data[f"away_{table}"] = str(away.id) if away else ""
    for i, (team, bye_type) in enumerate(byes, start=1):
        data[f"bye_team_{i}"] = str(team.id)
        data[f"bye_type_{i}"] = bye_type
    return data


def save(league, t, number, pairs, byes=()):
    return league.round_action(t, number, "save", **save_form(pairs, byes))


def tables(league, t, number):
    """[(home team id, away team id)] in table order."""
    rd = league.round(t, number)
    return [(m.home_team_id, m.away_team_id) for m in sorted(rd.matches, key=lambda m: m.table_number)]


@pytest.fixture
def four(league):
    t = league.tournament(rounds=5)
    teams = [league.team(t, school, ratings=(r,) * 4)
             for school, r in (("Alpha", 1800), ("Bravo", 1700), ("Charlie", 1600), ("Delta", 1500))]
    return t, teams


# ---------------------------------------------------------------------------

class TestCreateAndResize:
    def test_create_round_adds_empty_tables_with_four_boards(self, league, four):
        t, _ = four
        response = league.round_action(t, 1, "create", num_tables="3")
        assert flashed(response, "Round 1 created.")
        rd = league.round(t, 1)
        assert sorted(m.table_number for m in rd.matches) == [1, 2, 3]
        assert all(len(m.games) == 4 and m.home_team_id is None for m in rd.matches)
        assert not round_has_pairings(rd)

    @pytest.mark.parametrize("value", ["0", "", "-1", "two"])
    def test_table_count_must_be_at_least_one(self, league, four, value):
        t, _ = four
        response = league.round_action(t, 1, "create", num_tables=value)
        assert flashed(response, "Enter a table count of at least 1.")
        assert league.round(t, 1) is None

    def test_round_must_be_within_the_tournament(self, league, four):
        t, _ = four
        with pytest.raises(PairingError, match="outside this tournament's range"):
            create_round(t, 6, 2)
        with pytest.raises(PairingError, match="at least 1 table"):
            create_round(t, 1, 0)

    def test_round_2_needs_round_1_paired(self, league, four):
        t, _ = four
        response = league.round_action(t, 2, "create", num_tables="2")
        assert flashed(response, "Round 1 hasn't been paired yet.")

    def test_round_2_needs_round_1_complete(self, league, four):
        t, (a, b, c, d) = four
        league.pair(t, 1, [(a, b), (c, d)])
        league.set_results(league.match_of(t, 1, a), ["1-0"] * 4)
        assert previous_round_problem(t, 2) == ("Round 1 isn't complete (table 2 still needs results). "
                                                "Finish it before pairing round 2.")
        response = league.generate(t, 2)
        assert flashed(response, "Round 1 isn't complete")
        assert "Pairing locked:" in page_text(league.get(f"/tournaments/{t.id}/rounds/2"))

    def test_resize_adds_and_removes_empty_tables(self, league, four):
        t, _ = four
        league.round_action(t, 1, "create", num_tables="2")
        league.round_action(t, 1, "resize", num_tables="4")
        assert len(league.round(t, 1).matches) == 4
        response = league.round_action(t, 1, "resize", num_tables="1")
        assert flashed(response, "Round 1 table count updated.")
        assert [m.table_number for m in league.round(t, 1).matches] == [1]

    def test_resize_cannot_drop_a_table_with_teams(self, league, four):
        t, (a, b, c, d) = four
        league.pair(t, 1, [(a, b), (c, d)])
        with pytest.raises(PairingError, match="Table 2 has teams"):
            resize_round(t, 1, 1)


# ---------------------------------------------------------------------------

class TestManualPairings:
    def test_save_pairings_through_the_round_page(self, league, four):
        t, (a, b, c, d) = four
        league.round_action(t, 1, "create", num_tables="2")
        response = save(league, t, 1, [(a, c), (d, b)])
        assert flashed(response, "Pairings saved.")
        assert tables(league, t, 1) == [(a.id, c.id), (d.id, b.id)]

    def test_unplaced_teams_are_warned_about(self, league, four):
        t, (a, b, c, d) = four
        league.round_action(t, 1, "create", num_tables="2")
        response = save(league, t, 1, [(a, b), (None, None)])
        assert flashed(response, "Warning: Not placed in round 1:")
        assert flashed(response, "Charlie A") and flashed(response, "Delta A")

    def test_full_bye_from_the_form_is_an_automatic_bye(self, league):
        t = league.tournament()
        a, b, c = (league.team(t, s) for s in ("Alpha", "Bravo", "Charlie"))
        league.round_action(t, 1, "create", num_tables="1")
        save(league, t, 1, [(a, b)], byes=[(c, "full")])
        assert league.byes_of(c) == {1: ("full", "odd")}

    @pytest.mark.parametrize("pairs, byes, message", [
        ("aa", (), "a team can't play itself"),
        ("ab_ac", (), "Alpha A has already been used"),
        ("ab_cd", (("a", "half"),), "Alpha A has already been used"),
    ])
    def test_impossible_pairings_are_refused(self, league, four, pairs, byes, message):
        t, teams = four
        by_letter = dict(zip("abcd", teams))
        league.round_action(t, 1, "create", num_tables="2")
        table_pairs = [(by_letter[p[0]], by_letter[p[1]]) for p in pairs.split("_")]
        table_pairs += [(None, None)] * (2 - len(table_pairs))
        response = save(league, t, 1, table_pairs, [(by_letter[x], y) for x, y in byes])
        assert flashed(response, message), flashes(response)
        assert all(home is None for home, _ in tables(league, t, 1))

    def test_function_refuses_tables_and_teams_that_do_not_exist(self, league, four):
        t, (a, b, c, d) = four
        create_round(t, 1, 1)
        with pytest.raises(PairingError, match="Table 5 doesn't exist"):
            set_round_pairings(t, 1, {5: (a.id, b.id)})
        with pytest.raises(PairingError, match="isn't in this tournament"):
            set_round_pairings(t, 1, {1: (a.id, 99999)})
        with pytest.raises(PairingError, match="Unknown bye type"):
            set_round_pairings(t, 1, {1: (a.id, b.id)}, [(c.id, "double")])
        empty = league.tournament("Empty")
        with pytest.raises(PairingError, match="Round 1 hasn't been created yet."):
            set_round_pairings(empty, 1, {})

    def test_rematch_and_same_school_are_warned_about(self, league):
        t = league.tournament()
        la, lb = league.team(t, "Lincoln"), league.team(t, "Lincoln")
        r, w = league.team(t, "Roosevelt"), league.team(t, "Washington")
        warnings = league.pair(t, 1, [(la, lb), (r, w)])
        assert "Lincoln A vs Lincoln B: same school." in warnings
        league.finish_round(t, 1)
        warnings = league.pair(t, 2, [(r, w), (la, lb)])
        assert any("Roosevelt A and Washington A also play in round" in w for w in warnings)


# ---------------------------------------------------------------------------

class TestSwitchingPairings:
    @pytest.fixture
    def paired(self, league, four):
        t, (a, b, c, d) = four
        league.round_action(t, 1, "create", num_tables="2")
        save(league, t, 1, [(a, c), (b, d)])
        return t, (a, b, c, d)

    def test_swap_home_and_away_at_a_table(self, league, paired):
        t, (a, b, c, d) = paired
        response = save(league, t, 1, [(c, a), (b, d)])
        assert flashed(response, "Pairings saved.")
        assert tables(league, t, 1) == [(c.id, a.id), (b.id, d.id)]

    @pytest.mark.xfail(raises=IntegrityError, reason="Known bug B1: exchanging the away teams of two tables "
                                                "breaks UNIQUE(round, away_team) mid-update (500).")
    def test_switch_opponents_across_tables(self, league, paired):
        t, (a, b, c, d) = paired
        save(league, t, 1, [(a, d), (b, c)])  # away teams change tables
        assert tables(league, t, 1) == [(a.id, d.id), (b.id, c.id)]

    def test_move_a_home_team_to_away_at_another_table(self, league, paired):
        t, (a, b, c, d) = paired
        response = save(league, t, 1, [(d, c), (b, a)])
        assert flashed(response, "Pairings saved."), flashes(response)
        assert tables(league, t, 1) == [(d.id, c.id), (b.id, a.id)]

    @pytest.mark.xfail(raises=IntegrityError, reason="Known bug B1: exchanging the home teams of two tables "
                                                "breaks UNIQUE(round, home_team) mid-update (500).")
    def test_exchange_home_teams_between_tables(self, league, paired):
        t, (a, b, c, d) = paired
        save(league, t, 1, [(b, c), (a, d)])
        assert tables(league, t, 1) == [(b.id, c.id), (a.id, d.id)]

    def test_replace_a_team_with_one_that_had_a_bye(self, league):
        t = league.tournament()
        a, b, c = (league.team(t, s) for s in ("Alpha", "Bravo", "Charlie"))
        league.round_action(t, 1, "create", num_tables="1")
        save(league, t, 1, [(a, b)], byes=[(c, "full")])
        response = save(league, t, 1, [(a, c)], byes=[(b, "full")])
        assert flashed(response, "Pairings saved.")
        assert tables(league, t, 1) == [(a.id, c.id)]
        assert league.byes_of(b) == {1: ("full", "odd")} and league.byes_of(c) == {}

    def test_replacing_a_team_leaves_it_unplaced(self, league):
        t = league.tournament()
        a, b, c = (league.team(t, s) for s in ("Alpha", "Bravo", "Charlie"))
        league.round_action(t, 1, "create", num_tables="1")
        save(league, t, 1, [(a, b)], byes=[(c, "full")])
        response = save(league, t, 1, [(a, c)])
        assert flashed(response, "Not placed in round 1: Bravo A")

    def test_cannot_switch_a_table_once_results_are_entered(self, league, paired):
        t, (a, b, c, d) = paired
        league.lineup(league.match_of(t, 1, a), ["1-0", None, None, None])
        response = save(league, t, 1, [(a, d), (b, c)])
        assert flashed(response, "already has players or results entered, so it can't be changed")
        assert tables(league, t, 1) == [(a.id, c.id), (b.id, d.id)]

    def test_can_switch_other_tables_while_one_has_results(self, league):
        t = league.tournament()
        teams = [league.team(t, s) for s in ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot")]
        a, b, c, d, e, f = teams
        league.round_action(t, 1, "create", num_tables="3")
        save(league, t, 1, [(a, b), (c, d), (e, f)])
        league.lineup(league.match_of(t, 1, a), ["1-0"] * 4)
        response = save(league, t, 1, [(a, b), (f, d), (e, c)])  # C and F trade places
        assert flashed(response, "Pairings saved."), flashes(response)
        assert tables(league, t, 1) == [(a.id, b.id), (f.id, d.id), (e.id, c.id)]

    def test_regenerate_replaces_a_manual_pairing(self, league, paired):
        t, (a, b, c, d) = paired
        save(league, t, 1, [(a, b), (c, d)])
        league.generate(t, 1)
        assert league.opponents(t, 1) == {frozenset({a.id, c.id}), frozenset({b.id, d.id})}


# ---------------------------------------------------------------------------

class TestGeneratePairings:
    def test_round_1_pairs_top_half_against_bottom_half(self, league, four):
        t, (a, b, c, d) = four
        response = league.generate(t, 1)
        assert flashed(response, "Suggested pairings generated.")
        assert tables(league, t, 1) == [(a.id, c.id), (d.id, b.id)]

    def test_odd_field_gets_one_automatic_full_bye(self, league):
        t = league.tournament()
        teams = [league.team(t, s, ratings=(r,) * 4) for s, r in (("Alpha", 1800), ("Bravo", 1700), ("Charlie", 1600))]
        league.generate(t, 1)
        rd = league.round(t, 1)
        assert len(rd.matches) == 1
        assert [(b.team, b.bye_type, b.source) for b in rd.byes] == [(teams[2], "full", "odd")]

    def test_round_2_avoids_rematches_and_pairs_winners(self, league, four):
        t, (a, b, c, d) = four
        league.generate(t, 1)
        league.finish_round(t, 1)  # home wins 4-0: Alpha beat Charlie, Delta beat Bravo
        league.generate(t, 2)
        assert league.opponents(t, 2) == {frozenset({a.id, d.id}), frozenset({b.id, c.id})}

    # League-sized fields. (A round-robin-sized field, e.g. 6 teams over 5 rounds,
    # can force a rematch: pairing one round at a time has no lookahead.)
    @pytest.mark.parametrize("n_teams, n_rounds", [(8, 4), (9, 5), (12, 7), (13, 7), (16, 7)])
    def test_full_tournament_has_no_rematches_and_no_double_byes(self, league, n_teams, n_rounds):
        t = league.tournament(rounds=n_rounds)
        teams = [league.team(t, f"School{i}", ratings=(2000 - 50 * i,) * 4) for i in range(n_teams)]
        results = itertools.cycle([["1-0"] * 4, ["0-1"] * 4, ["1-0", "0-1", "0.5-0.5", "0.5-0.5"]])
        seen, full_byes = set(), {}
        for number in range(1, n_rounds + 1):
            response = league.generate(t, number)
            assert flashed(response, "Suggested pairings generated."), flashes(response)
            rd = league.round(t, number)
            for m in rd.matches:
                league.set_results(m, next(results))
            pairs = league.opponents(t, number)
            assert not (pairs & seen), f"rematch in round {number}"
            seen |= pairs
            placed = [x for pair in pairs for x in pair] + [b.team_id for b in rd.byes]
            assert sorted(placed) == sorted(team.id for team in teams)
            for b in rd.byes:
                full_byes[b.team_id] = full_byes.get(b.team_id, 0) + 1
        assert all(count <= 1 for count in full_byes.values())

    def test_regenerate_redecides_the_automatic_bye(self, league):
        t = league.tournament()
        teams = [league.team(t, s) for s in ("Alpha", "Bravo", "Charlie")]
        league.generate(t, 1)
        league.generate(t, 1)
        assert Bye.query.count() == 1 and len(league.round(t, 1).matches) == 1

    def test_generate_resizes_to_the_number_of_matches_needed(self, league, four):
        t, _ = four
        league.round_action(t, 1, "create", num_tables="5")
        league.generate(t, 1)
        assert len(league.round(t, 1).matches) == 2

    def test_generate_is_refused_once_lineups_are_entered(self, league, four):
        t, (a, *_) = four
        league.generate(t, 1)
        league.lineup(league.match_of(t, 1, a), [None] * 4)
        response = league.generate(t, 1)
        assert flashed(response, "already has lineups or results. Use Clear this round first")

    def test_generate_with_no_teams(self, league):
        t = league.tournament()
        assert flashed(league.generate(t, 1), "There are no teams available to pair.")

    def test_generate_with_one_team(self, league):
        t = league.tournament()
        league.team(t, "Alpha")
        assert flashed(league.generate(t, 1), "No matches could be generated for this round.")
        assert Match.query.count() == 0

    def test_generate_notes_unavoidable_problems(self, league):
        t = league.tournament()
        la, lb = league.team(t, "Lincoln"), league.team(t, "Lincoln")
        response = league.generate(t, 1)
        assert flashed(response, "Note: Table 1: Lincoln A and Lincoln B are from the same school.")
        assert flashed(response, "Warning: Lincoln A vs Lincoln B: same school.")


# ---------------------------------------------------------------------------

class TestAddMatch:
    def test_adds_a_table_for_two_teams_with_byes(self, league):
        t = league.tournament()
        a, b, c, d = (league.team(t, s) for s in ("Alpha", "Bravo", "Charlie", "Delta"))
        league.pair(t, 1, [(a, b)], byes=[(c, "half"), (d, "half")])
        response = league.round_action(t, 1, "add_match", home_team=str(c.id), away_team=str(d.id))
        assert flashed(response, "Added table 2.")
        assert tables(league, t, 1) == [(a.id, b.id), (c.id, d.id)]
        assert league.byes_of(c) == {} and league.byes_of(d) == {}

    def test_refuses_a_team_already_playing(self, league, four):
        t, (a, b, c, d) = four
        league.pair(t, 1, [(a, b)])
        with pytest.raises(PairingError, match="Alpha A is already playing at table 1"):
            add_match(t, 1, a.id, c.id)
        with pytest.raises(PairingError, match="can't play itself"):
            add_match(t, 1, c.id, c.id)
        with pytest.raises(PairingError, match="Choose two teams"):
            add_match(t, 1, c.id, None)


# ---------------------------------------------------------------------------

class TestClearRound:
    def test_clear_removes_teams_lineups_results_and_automatic_byes(self, league):
        t = league.tournament()
        a, b, c, d = (league.team(t, s) for s in ("Alpha", "Bravo", "Charlie", "Delta"))
        league.add_bye(t, 1, d, "half")
        league.generate(t, 1)
        league.finish_round(t, 1)
        response = league.round_action(t, 1, "clear")
        assert flashed(response, "Round 1 pairings, lineups, and results cleared.")
        rd = league.round(t, 1)
        assert rd.matches and all(m.home_team_id is None and m.away_team_id is None for m in rd.matches)
        assert all(g.home_player_id is None and g.home_score is None for m in rd.matches for g in m.games)
        assert [(b.team, b.source) for b in rd.byes] == [(d, "requested")]
        assert not round_has_pairings(rd)

    def test_clear_keeps_late_join_byes(self, league, four):
        t, teams = four
        league.generate(t, 1)
        league.post_team(t, "Echo")
        league.round_action(t, 1, "clear")
        echo = league.team_named(t, "Echo A")
        assert league.byes_of(echo) == {1: ("half", "late_join")}


# ---------------------------------------------------------------------------

class TestRoundLock:
    """Once a later round is paired, earlier rounds are read-only. The director
    unlocks a round by clearing the later rounds, latest first."""

    @pytest.fixture
    def two_rounds(self, league, four):
        t, teams = four
        league.generate(t, 1)
        league.finish_round(t, 1)
        league.generate(t, 2)
        return t, teams

    def test_lock_message(self, league, two_rounds):
        t, _ = two_rounds
        assert round_lock_problem(t, 1) == ("Round 1 is locked because round 2 has been paired. "
                                            "To change round 1, clear round 2.")
        assert round_lock_problem(t, 2) is None

    def test_lock_message_lists_every_round_to_clear(self, league, two_rounds):
        t, _ = two_rounds
        league.finish_round(t, 2)
        league.generate(t, 3)
        assert "clear rounds 3 down to 2, starting with round 3" in round_lock_problem(t, 1)

    @pytest.mark.parametrize("action, form", [
        ("clear", {}), ("generate", {}), ("save", {}), ("resize", {"num_tables": "3"}),
        ("create", {"num_tables": "3"}), ("add_match", {"home_team": "1", "away_team": "2"}),
    ])
    def test_every_round_action_is_refused(self, league, two_rounds, action, form):
        t, _ = two_rounds
        before = tables(league, t, 1)
        response = league.round_action(t, 1, action, **form)
        assert flashed(response, "Round 1 is locked"), flashes(response)
        assert tables(league, t, 1) == before and league.round(t, 1).is_complete

    def test_results_cannot_be_changed(self, league, two_rounds):
        t, (a, *_) = two_rounds
        match = league.match_of(t, 1, a)
        before = [g.result_code for g in match.games]
        response = league.lineup(match, ["0-1"] * 4)
        assert flashed(response, "Round 1 is locked")
        assert [g.result_code for g in match.games] == before

    def test_locked_round_page_shows_banner_and_disables_buttons(self, league, two_rounds):
        t, _ = two_rounds
        page = page_text(league.get(f"/tournaments/{t.id}/rounds/1"))
        assert "Round locked:" in page
        assert "<button disabled>Save pairings</button>" in page
        assert 'type="submit" disabled>Clear this round</button>' in page
        assert "<button disabled>Save table 1 lineup</button>" in page
        assert "Round locked:" not in page_text(league.get(f"/tournaments/{t.id}/rounds/2"))

    def test_clearing_the_later_round_unlocks_and_allows_a_correction(self, league, two_rounds):
        t, (a, *_) = two_rounds
        assert flashed(league.round_action(t, 2, "clear"), "Round 2 pairings, lineups, and results cleared.")
        assert round_lock_problem(t, 1) is None
        response = league.lineup(league.match_of(t, 1, a), ["0-1"] * 4)
        assert flashed(response, "Table 1 lineup saved.")
        assert flashed(league.generate(t, 2), "Suggested pairings generated.")
        assert round_lock_problem(t, 1) is not None

    def test_must_clear_latest_round_first(self, league, two_rounds):
        t, _ = two_rounds
        league.finish_round(t, 2)
        league.generate(t, 3)
        assert flashed(league.round_action(t, 2, "clear"), "Round 2 is locked")
        league.round_action(t, 3, "clear")
        assert flashed(league.round_action(t, 2, "clear"), "Round 2 pairings, lineups, and results cleared.")

    def test_future_requested_byes_do_not_lock(self, league, four):
        t, (a, *_) = four
        league.generate(t, 1)
        league.add_bye(t, 4, a, "half")
        assert round_lock_problem(t, 1) is None

    def test_empty_later_round_does_not_lock(self, league, four):
        t, _ = four
        league.generate(t, 1)
        league.finish_round(t, 1)
        league.round_action(t, 2, "create", num_tables="2")
        assert round_lock_problem(t, 1) is None
