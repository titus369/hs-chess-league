"""The Swiss pairing engine. Pure Python: no database or app needed."""
import pytest

from app.swiss import Entrant, suggest_pairings


def entrant(id, mp=0.0, tpn=None, school=None, sides=(), had_full_bye=False, undefeated=True):
    """A team; by default every team is from its own school and seeded by id."""
    return Entrant(id=id, name=f"T{id}", tpn=tpn or id, school_id=school if school is not None else 100 + id,
                   mp=mp, sides=list(sides), had_full_bye=had_full_bye, undefeated=undefeated)


def field(n, overrides=None):
    """n entrants; overrides maps id -> dict of entrant() keyword arguments."""
    return [entrant(i, **(overrides or {}).get(i, {})) for i in range(1, n + 1)]


def pair_sets(suggestion):
    return {frozenset(pair) for pair in suggestion.pairs}


def everyone_once(suggestion, entrants):
    placed = [team for pair in suggestion.pairs for team in pair]
    if suggestion.bye is not None:
        placed.append(suggestion.bye)
    return sorted(placed) == sorted(e.id for e in entrants)


class TestBasics:
    @pytest.mark.parametrize("n", [2, 4, 6, 8, 12, 20])
    def test_even_field_pairs_everyone_once_with_no_bye(self, n):
        teams = field(n)
        s = suggest_pairings(teams, set())
        assert s.bye is None and len(s.pairs) == n // 2 and everyone_once(s, teams)

    @pytest.mark.parametrize("n", [3, 5, 7, 9, 13])
    def test_odd_field_gives_exactly_one_bye(self, n):
        teams = field(n)
        s = suggest_pairings(teams, set())
        assert s.bye is not None and len(s.pairs) == n // 2 and everyone_once(s, teams)

    def test_single_team_gets_the_bye(self):
        s = suggest_pairings([entrant(1)], set())
        assert (s.pairs, s.bye) == ([], 1)

    def test_no_teams(self):
        s = suggest_pairings([], set())
        assert (s.pairs, s.bye) == ([], None)


class TestScoreGroups:
    def test_one_group_pairs_top_half_against_bottom_half(self):
        assert pair_sets(suggest_pairings(field(4), set())) == {frozenset({1, 3}), frozenset({2, 4})}
        assert pair_sets(suggest_pairings(field(8), set())) == {
            frozenset({1, 5}), frozenset({2, 6}), frozenset({3, 7}), frozenset({4, 8})}

    def test_teams_play_within_their_score_group(self):
        teams = field(4, {1: {"mp": 2}, 2: {"mp": 2}})
        assert pair_sets(suggest_pairings(teams, set())) == {frozenset({1, 2}), frozenset({3, 4})}

    def test_bottom_of_upper_group_floats_down(self):
        teams = field(4, {1: {"mp": 1}, 2: {"mp": 1}, 3: {"mp": 1}})
        s = suggest_pairings(teams, set())
        assert pair_sets(s) == {frozenset({1, 2}), frozenset({3, 4})}
        assert any("plays down" in note for note in s.notes)

    def test_highest_ranked_pair_is_on_table_1(self):
        teams = field(4, {3: {"mp": 2}, 4: {"mp": 2}})
        s = suggest_pairings(teams, set())
        assert set(s.pairs[0]) == {3, 4}


class TestRematchesAndSchools:
    def test_avoids_rematches(self):
        s = suggest_pairings(field(4), {frozenset({1, 3})})
        assert frozenset({1, 3}) not in pair_sets(s)
        assert not any("already played" in note for note in s.notes)

    def test_rematch_avoidance_beats_score_groups(self):
        teams = field(4, {1: {"mp": 1}, 2: {"mp": 1}})
        s = suggest_pairings(teams, {frozenset({1, 2})})
        assert frozenset({1, 2}) not in pair_sets(s)

    def test_unavoidable_rematch_is_paired_and_noted(self):
        s = suggest_pairings(field(2), {frozenset({1, 2})})
        assert pair_sets(s) == {frozenset({1, 2})}
        assert any("already played" in note for note in s.notes)

    def test_avoids_same_school(self):
        teams = field(4, {1: {"school": 7}, 3: {"school": 7}})
        assert frozenset({1, 3}) not in pair_sets(suggest_pairings(teams, set()))

    def test_unavoidable_same_school_is_paired_and_noted(self):
        teams = field(2, {1: {"school": 7}, 2: {"school": 7}})
        s = suggest_pairings(teams, set())
        assert pair_sets(s) == {frozenset({1, 2})}
        assert any("same school" in note for note in s.notes)

    def test_rematch_is_worse_than_same_school(self):
        teams = field(4, {1: {"school": 7}, 2: {"school": 7}})
        played = {frozenset({1, 3}), frozenset({1, 4}), frozenset({2, 3}), frozenset({2, 4})}
        assert pair_sets(suggest_pairings(teams, played)) == {frozenset({1, 2}), frozenset({3, 4})}

    def test_undefeated_teams_from_one_school_may_meet_rather_than_float(self):
        # Current rule: SAME_SCHOOL_UNDEFEATED is a small penalty, less than one float.
        teams = field(4, {1: {"mp": 1, "school": 7}, 2: {"mp": 1, "school": 7}})
        assert pair_sets(suggest_pairings(teams, set())) == {frozenset({1, 2}), frozenset({3, 4})}

    def test_same_school_teams_avoid_each_other_once_one_has_lost(self):
        teams = field(4, {1: {"mp": 1, "school": 7}, 2: {"mp": 1, "school": 7, "undefeated": False}})
        assert frozenset({1, 2}) not in pair_sets(suggest_pairings(teams, set()))


class TestBye:
    def test_bye_goes_to_lowest_ranked_team(self):
        teams = field(5, {1: {"mp": 1}, 2: {"mp": 1}})
        assert suggest_pairings(teams, set()).bye == 5

    def test_bye_goes_to_lowest_score_even_for_a_high_seed(self):
        teams = field(5, {i: {"mp": 1} for i in (2, 3, 4, 5)})
        assert suggest_pairings(teams, set()).bye == 1

    def test_team_that_had_a_full_bye_is_skipped(self):
        teams = field(5, {5: {"had_full_bye": True}})
        s = suggest_pairings(teams, set())
        assert s.bye == 4 and not any("already had" in note for note in s.notes)

    def test_second_bye_when_everyone_has_had_one_is_noted(self):
        teams = field(3, {i: {"had_full_bye": True} for i in (1, 2, 3)})
        s = suggest_pairings(teams, set())
        assert s.bye is not None
        assert any("already had a full-point bye" in note for note in s.notes)

    def test_bye_choice_avoids_forcing_a_rematch(self):
        # Giving the lowest team (3) the bye would force the 1-2 rematch, so the
        # bye moves up to team 2 instead.
        s = suggest_pairings(field(3), {frozenset({1, 2})})
        assert s.bye == 2 and pair_sets(s) == {frozenset({1, 3})}


class TestSides:
    def test_first_round_sides_alternate_by_table(self):
        s = suggest_pairings(field(4), set())
        assert s.pairs == [(1, 3), (4, 2)]

    def test_higher_team_switches_side_from_last_round(self):
        teams = [entrant(1, sides=["H"]), entrant(2, sides=["H"])]
        assert suggest_pairings(teams, set()).pairs == [(2, 1)]

    def test_lower_team_switches_when_higher_has_no_history(self):
        teams = [entrant(1), entrant(2, sides=["A"])]
        assert suggest_pairings(teams, set()).pairs == [(2, 1)]

    def test_avoids_a_third_match_in_a_row_on_one_side(self):
        teams = [entrant(1, sides=["H"]), entrant(2, sides=["H", "H"])]
        s = suggest_pairings(teams, set())
        assert s.pairs == [(1, 2)]
        assert not any("third match in a row" in note for note in s.notes)

    def test_pairing_prefers_teams_due_opposite_sides(self):
        teams = field(4, {1: {"sides": ["H", "H"]}, 2: {"sides": ["A", "A"]},
                            3: {"sides": ["H", "H"]}, 4: {"sides": ["A", "A"]}})
        for home, away in suggest_pairings(teams, set()).pairs:
            assert teams[home - 1].streak_side != "H" and teams[away - 1].streak_side != "A"

    @pytest.mark.xfail(reason="Known bug: when both teams have played the same side twice, one gets a "
                              "third in a row but the 'third match in a row' note can never be added.")
    def test_unavoidable_third_same_side_is_noted(self):
        teams = [entrant(1, sides=["A", "A"]), entrant(2, sides=["A", "A"])]
        s = suggest_pairings(teams, set())
        assert any("third match in a row" in note for note in s.notes)


class TestEntrant:
    @pytest.mark.parametrize("sides, last, streak", [
        ([], None, None), (["H"], "H", None), (["H", "A"], "A", None), (["A", "H", "H"], "H", "H"),
    ])
    def test_last_side_and_streak(self, sides, last, streak):
        e = entrant(1, sides=sides)
        assert (e.last_side, e.streak_side) == (last, streak)
