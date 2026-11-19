"""The Swiss pairing engine. Pure Python: no database or app needed."""
import pytest

from app.swiss import Entrant, suggest_pairings


def entrant(id, mp=0.0, tpn=None, school=None, sides=(), had_full_bye=False, undefeated=True, rating=None):
    """A team; by default every team is from its own school, seeded by id, and
    rated 10 points below the seed above it."""
    return Entrant(id=id, name=f"T{id}", tpn=tpn or id, school_id=school if school is not None else 100 + id,
                   mp=mp, sides=list(sides), had_full_bye=had_full_bye, undefeated=undefeated,
                   rating=2000 - 10 * id if rating is None else rating)


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

    def test_two_undefeated_leaders_from_one_school_are_paired(self):
        # The only two teams tied for first, both undefeated: pair them rather than float.
        teams = field(4, {1: {"mp": 1, "school": 7}, 2: {"mp": 1, "school": 7}})
        s = suggest_pairings(teams, set())
        assert pair_sets(s) == {frozenset({1, 2}), frozenset({3, 4})}
        assert any("same school" in note for note in s.notes)

    def test_leaders_exception_needs_both_teams_undefeated(self):
        teams = field(4, {1: {"mp": 1, "school": 7}, 2: {"mp": 1, "school": 7, "undefeated": False}})
        assert frozenset({1, 2}) not in pair_sets(suggest_pairings(teams, set()))

    def test_leaders_exception_needs_exactly_two_leaders(self):
        teams = field(6, {1: {"mp": 2, "school": 7}, 2: {"mp": 2, "school": 7}, 3: {"mp": 2}, 4: {"mp": 2}})
        assert frozenset({1, 2}) not in pair_sets(suggest_pairings(teams, set()))

    def test_same_school_pair_in_a_lower_group_floats_instead(self):
        # Two undefeated teams on 0.5 (half-point byes) are not tied for first.
        teams = field(6, {1: {"mp": 1}, 2: {"mp": 1}, 3: {"mp": 0.5, "school": 7}, 4: {"mp": 0.5, "school": 7}})
        assert frozenset({3, 4}) not in pair_sets(suggest_pairings(teams, set()))

    def test_same_school_teams_that_lost_every_match_float_up(self):
        teams = field(4, {1: {"mp": 1}, 2: {"mp": 1},
                          3: {"school": 7, "undefeated": False}, 4: {"school": 7, "undefeated": False}})
        s = suggest_pairings(teams, set())
        assert frozenset({3, 4}) not in pair_sets(s)
        assert len([n for n in s.notes if "plays down" in n]) == 2

    def test_same_school_inside_a_big_group_is_avoided_without_floating(self):
        teams = field(8, {i: {"mp": 1} for i in range(1, 5)} | {1: {"mp": 1, "school": 7}, 3: {"mp": 1, "school": 7}})
        s = suggest_pairings(teams, set())
        assert frozenset({1, 3}) not in pair_sets(s)
        assert not any("plays down" in note for note in s.notes)


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

    def test_unavoidable_third_same_side_is_noted(self):
        teams = [entrant(1, sides=["H", "A", "A"]), entrant(2, sides=["H", "A", "A"])]
        s = suggest_pairings(teams, set())
        assert s.pairs == [(1, 2)]  # higher-ranked team 1 gets home
        assert s.notes == ["Table 1: T2 plays a third match in a row away."]

    def test_unavoidable_third_in_a_row_with_imbalance_gets_one_note(self):
        teams = [entrant(1, sides=["A", "A"]), entrant(2, sides=["A", "A"])]
        s = suggest_pairings(teams, set())
        assert s.notes == ["Table 1: T2 plays a third match in a row away."]

    def test_unavoidable_imbalance_is_noted(self):
        # Two over on away without a streak: the loser ends up 3 over.
        over = {"sides": ["A", "A", "H", "A"]}
        teams = [entrant(1, **over), entrant(2, **over)]
        s = suggest_pairings(teams, set())
        assert s.pairs == [(1, 2)]
        assert s.notes == ["Table 1: T2 goes 3 matches over on away."]

    def test_evening_the_count_beats_alternating(self):
        # Home, home, away is due AWAY (count first), not home (alternation).
        teams = [entrant(1, sides=["H", "H", "A"]), entrant(2, sides=["H", "A", "A"])]
        assert suggest_pairings(teams, set()).pairs == [(2, 1)]

    def test_team_two_apart_must_switch_even_if_higher_ranked(self):
        teams = [entrant(1, mp=1, sides=["H", "H"]), entrant(2, sides=["H", "A"])]
        assert suggest_pairings(teams, set()).pairs == [(2, 1)]

    def test_balance_rule_beats_third_in_a_row(self):
        # Both need home; team 2 would go 0-3 (worse) so it gets home and team 1
        # plays a third away in a row instead.
        teams = [entrant(1, mp=1, sides=["H", "A", "A"]), entrant(2, sides=["A", "A"])]
        s = suggest_pairings(teams, set())
        assert s.pairs == [(2, 1)]
        assert s.notes[0] == "Table 1: T1 plays a third match in a row away."

    def test_higher_rank_means_more_match_points_not_better_seed(self):
        # Seed 5 has more points, so it is the higher-ranked team and alternates.
        teams = [entrant(1, mp=0, sides=["H"]), entrant(5, mp=1, sides=["H"])]
        assert suggest_pairings(teams, set()).pairs == [(1, 5)]

    def test_third_in_a_row_avoided_inside_a_group_when_possible(self):
        # 1 and 3 need home, 2 and 4 need away; 1v3/2v4 would break both, 1v4/2v3 neither.
        need_home, need_away = {"sides": ["H", "A", "A"]}, {"sides": ["A", "H", "H"]}
        teams = field(4, {1: need_home, 2: need_away, 3: need_home, 4: need_away})
        s = suggest_pairings(teams, set())
        assert pair_sets(s) == {frozenset({1, 4}), frozenset({2, 3})}
        assert s.notes == []

    def test_score_group_beats_side_rules(self):
        # Pairing across groups would fix every side, but floats are worse.
        teams = field(4, {1: {"mp": 1, "sides": ["A", "A"]}, 2: {"mp": 1, "sides": ["A", "A"]},
                          3: {"sides": ["H", "H"]}, 4: {"sides": ["H", "H"]}})
        s = suggest_pairings(teams, set())
        assert pair_sets(s) == {frozenset({1, 2}), frozenset({3, 4})}
        assert len([n for n in s.notes if "third match in a row" in n]) == 2


class TestHalves:
    """Inside a score group the top half plays the bottom half."""

    def test_round_2_of_the_2026_27_league(self):
        # The 1-point group after round 1: seeds, schools and round-1 sides.
        # (seed, school, round-1 side, average core rating)
        group = [(1, "Minnetonka", "H", 1634.25), (2, "Burnsville", "A", 1628.75), (3, "Wayzata", "H", 1578.75),
                 (6, "Eastview", "H", 1313.25), (7, "Eden Prairie", "A", 1204.0), (8, "Eastview", "H", 1098.75),
                 (9, "Eden Prairie", "A", 1060.75), (10, "Wayzata", "H", 1038.75), (11, "Minnetonka", "A", 1029.25),
                 (12, "Burnsville", "H", 871.0), (13, "Eastview", "A", 848.0), (14, "Eastview", "H", 615.25),
                 (19, "Andover", "H", 453.25), (31, "Eastview", "A", 100.0)]
        schools = {name: n for n, name in enumerate(sorted({s for _, s, _, _ in group}))}
        teams = [Entrant(id=seed, name=f"#{seed}", tpn=seed, school_id=schools[school], mp=1.0, sides=[side],
                         rating=rating)
                 for seed, school, side, rating in group]
        s = suggest_pairings(teams, set(), rounds_played=1)
        # Standard is 1-10, 2-11, 3-12, 6-13, 7-14, 8-19, 9-31.
        #  * One interchange: #9 Eden Prairie 2 and #10 Wayzata 3 (22 points) swap
        #    halves, fixing two clashes (1-10 both due away, 9-31 both due home).
        #  * 3-12 (both due away) -> 3-13 and 6-12: a 23-point transposition that
        #    also splits up Eastview 1 v Eastview 3.
        #  * 2-11 stays a clash: fixing it means swapping #11 and #12 (158 points).
        #  * No second interchange (#8 Eastview 2 for #11 Minnetonka 2 would fix
        #    2-11): only the boundary teams of the halves may interchange.
        assert pair_sets(s) == {frozenset(p) for p in [(1, 9), (2, 11), (3, 13), (6, 12), (7, 14), (8, 19), (10, 31)]}
        assert s.notes == [
            "Table 1: #1 and #9 are both in the top half of their score group.",
            "Table 7: #10 and #31 are both in the bottom half of their score group.",
        ]
        last = {seed: side for seed, _, side, _ in group}
        for home, away in s.pairs:  # the higher-ranked (better seed) team alternates
            higher = min(home, away)
            assert (higher == home) == (last[higher] == "A")
        assert [(h, a) for h, a in s.pairs if last[h] == last[a]] == [(2, 11), (12, 6), (19, 8)]

    def test_same_side_again_is_accepted_to_keep_top_against_bottom(self):
        teams = field(4, {i: {"sides": ["A"]} for i in range(1, 5)})
        assert pair_sets(suggest_pairings(teams, set())) == {frozenset({1, 3}), frozenset({2, 4})}

    def test_opponents_may_swap_within_the_halves_for_alternating_sides(self):
        teams = field(4, {1: {"sides": ["A"]}, 2: {"sides": ["H"]}, 3: {"sides": ["A"]}, 4: {"sides": ["H"]}})
        assert pair_sets(suggest_pairings(teams, set())) == {frozenset({1, 4}), frozenset({2, 3})}

    def test_third_in_a_row_may_put_two_top_half_teams_together(self):
        # 1v3/2v4 each give a third in a row; 1v4/2v3 are rematches; 1v2/3v4
        # give no third in a row but put two top-half teams together.
        need_home, need_away = {"sides": ["H", "A", "A"]}, {"sides": ["A", "H", "H"]}
        teams = field(4, {1: need_home, 2: need_away, 3: need_home, 4: need_away})
        played = {frozenset({1, 4}), frozenset({2, 3})}
        s = suggest_pairings(teams, played)
        assert pair_sets(s) == {frozenset({1, 2}), frozenset({3, 4})}
        assert not any("in a row" in note for note in s.notes)

    def test_side_rules_beat_top_against_bottom(self):
        # 1v3 is a rematch; 1v4 and 2v3 would each break a side rule; 1v2 and 3v4 break none.
        need_home, need_away = {"sides": ["H", "A", "A"]}, {"sides": ["A", "H", "H"]}
        teams = field(4, {1: need_home, 2: need_away, 3: need_away, 4: need_home})
        s = suggest_pairings(teams, {frozenset({1, 3})})
        assert pair_sets(s) == {frozenset({1, 2}), frozenset({3, 4})}
        assert any("both in the top half" in note for note in s.notes)

    def test_same_school_beats_top_against_bottom(self):
        teams = field(4, {1: {"school": 7, "undefeated": False}, 3: {"school": 7, "undefeated": False}})
        assert frozenset({1, 3}) not in pair_sets(suggest_pairings(teams, set()))


class TestRatingLimits:
    """Opponents swap for sides only between closely rated teams (80/200-point rules)."""

    @staticmethod
    def one_clash(gap):
        # 1 and 3 both due home; 2 and 4 have no history. Swapping 3 and 4 fixes
        # the clash. 1 and 2 are 100 apart, so up to 100 the 3-4 gap is the
        # smaller of the two gaps that measure the transposition.
        return [entrant(1, sides=["A"], rating=2000), entrant(2, rating=1900),
                entrant(3, sides=["A"], rating=1800), entrant(4, rating=1800 - gap)]

    @pytest.mark.parametrize("gap, swapped", [(10, True), (80, True), (81, False), (150, False)])
    def test_80_point_rule(self, gap, swapped):
        s = suggest_pairings(self.one_clash(gap), set())
        expected = {frozenset({1, 4}), frozenset({2, 3})} if swapped else {frozenset({1, 3}), frozenset({2, 4})}
        assert pair_sets(s) == expected

    @staticmethod
    def over_clash(gap):
        # 1 and 3 are both 2 over on home (but not on a streak), so whichever
        # plays home would end 3 over.
        # 1 and 2 are 300 apart, so the 3-4 gap is the smaller of the two.
        over = ["H", "H", "A", "H"]
        return [entrant(1, sides=over, rating=2400), entrant(2, rating=2100),
                entrant(3, sides=over, rating=1800), entrant(4, rating=1800 - gap)]

    @pytest.mark.parametrize("gap, swapped", [(150, True), (200, True), (201, False), (300, False)])
    def test_200_point_rule(self, gap, swapped):
        s = suggest_pairings(self.over_clash(gap), set())
        expected = {frozenset({1, 4}), frozenset({2, 3})} if swapped else {frozenset({1, 3}), frozenset({2, 4})}
        assert pair_sets(s) == expected
        assert any("3 matches over" in note for note in s.notes) != swapped

    @staticmethod
    def interchange_case(gap):
        # Two clashes: 1 and 3 both due home, 2 and 4 both due away. Transposing
        # (1v4, 2v3) is a 200+ point switch. Interchanging 2 and 3 (1v2, 3v4)
        # counts as 80 points plus their gap, against two 80-point clashes fixed.
        due_home, due_away = ["A"], ["H"]
        return [entrant(1, sides=due_home, rating=2500), entrant(2, sides=due_away, rating=2300),
                entrant(3, sides=due_home, rating=2300 - gap), entrant(4, sides=due_away, rating=2000)]

    @pytest.mark.parametrize("gap, interchanged", [(30, True), (80, True), (90, False)])
    def test_interchange_fixing_two_clashes(self, gap, interchanged):
        s = suggest_pairings(self.interchange_case(gap), set())
        expected = {frozenset({1, 2}), frozenset({3, 4})} if interchanged else {frozenset({1, 3}), frozenset({2, 4})}
        assert pair_sets(s) == expected

    def test_no_interchange_for_a_single_80_point_clash(self):
        # Only 1 and 3 clash; interchanging 2 and 3 (10 points) would fix it, but
        # an interchange counts as 80 + 10 points, more than the clash.
        teams = [entrant(1, sides=["A"], rating=2500), entrant(2, rating=2300),
                 entrant(3, sides=["A"], rating=2290), entrant(4, rating=2000)]
        assert pair_sets(suggest_pairings(teams, set())) == {frozenset({1, 3}), frozenset({2, 4})}

    def test_transposition_within_80_beats_a_smaller_interchange(self):
        # US Chess 29E5e, example 1: 2050 WBW v 1850 WBW and 1870 BWB v 1780 BWB.
        # Interchanging 1870/1850 is only 20 points, but transposing 1850/1780
        # (70 points, within the 80-point rule) is preferred.
        wbw, bwb = ["H", "A", "H"], ["A", "H", "A"]
        teams = [entrant(1, sides=wbw, rating=2050), entrant(2, sides=bwb, rating=1870),
                 entrant(3, sides=wbw, rating=1850), entrant(4, sides=bwb, rating=1780)]
        assert suggest_pairings(teams, set()).pairs == [(4, 1), (2, 3)]

    def test_interchange_needs_a_side_reason(self):
        teams = [entrant(1, rating=2400), entrant(2, rating=2300), entrant(3, rating=2299), entrant(4, rating=2000)]
        assert pair_sets(suggest_pairings(teams, set())) == {frozenset({1, 3}), frozenset({2, 4})}

    def test_interchange_in_a_bigger_group(self):
        # 8 teams; 4 and 5 are 10 points apart. 1 and 5 are both due home, and
        # 4 and 8 are both due away; interchanging 4 and 5 fixes both clashes.
        ratings = {1: 2400, 2: 2300, 3: 2200, 4: 2100, 5: 2090, 6: 1900, 7: 1800, 8: 1700}
        sides = {1: ["A"], 5: ["A"], 4: ["H"], 8: ["H"]}
        teams = [entrant(i, rating=ratings[i], sides=sides.get(i, ())) for i in range(1, 9)]
        s = suggest_pairings(teams, set())
        assert pair_sets(s) == {frozenset(p) for p in [(1, 4), (2, 6), (3, 7), (5, 8)]}
        assert sum("both in the" in note for note in s.notes) == 2

    def test_unrated_teams_count_as_equally_rated(self):
        # Unrated players count as 100, so unrated teams all average 100.
        teams = [entrant(i, rating=100, sides=["A"] if i in (1, 3) else ()) for i in range(1, 5)]
        s = suggest_pairings(teams, set())
        assert frozenset({1, 3}) not in pair_sets(s)
        assert not [n for n in s.notes if "in a row" in n or "over" in n]

    def test_without_ratings_teams_swap_freely(self):
        teams = [entrant(i, rating=0, sides=["A"] if i in (1, 3) else ()) for i in range(1, 5)]
        assert pair_sets(suggest_pairings(teams, set())) == {frozenset({1, 4}), frozenset({2, 3})}

    def test_floater_choice_respects_the_80_point_rule(self):
        # 3 would float to 4, but both are due home. Floating 2 instead fixes it
        # only if 2 and 3 are within 80 points.
        def teams(gap):
            return [entrant(1, mp=1, rating=2100), entrant(2, mp=1, rating=1900 + gap),
                    entrant(3, mp=1, sides=["A"], rating=1900), entrant(4, sides=["A"], rating=1800),
                    entrant(5, rating=1700), entrant(6, rating=1600)]
        near = pair_sets(suggest_pairings(teams(50), set()))
        far = pair_sets(suggest_pairings(teams(150), set()))
        assert frozenset({3, 4}) not in near and frozenset({2, 4}) in near
        assert frozenset({3, 4}) in far


class TestDropping:
    def test_one_team_dropping_two_groups_beats_two_teams_dropping_one(self):
        teams = field(4, {1: {"mp": 2}, 2: {"mp": 1}, 3: {"mp": 1}})
        s = suggest_pairings(teams, set())
        assert pair_sets(s) == {frozenset({1, 4}), frozenset({2, 3})}
        assert len([n for n in s.notes if "plays down" in n]) == 1


class TestPlusTwo:
    """Same-school teams leave their score group only below plus two."""

    @staticmethod
    def teams():
        # 1 and 2 (same school, both lost a match) lead on 3 points; 3-6 have 2.
        return field(6, {1: {"mp": 3, "school": 7, "undefeated": False},
                         2: {"mp": 3, "school": 7, "undefeated": False},
                         3: {"mp": 2}, 4: {"mp": 2}, 5: {"mp": 2}, 6: {"mp": 2}})

    def test_at_plus_two_the_group_stays_together(self):
        # 3 points after 4 rounds = 3 wins, 1 loss = plus two.
        s = suggest_pairings(self.teams(), set(), rounds_played=4)
        assert frozenset({1, 2}) in pair_sets(s)
        assert any("same school" in note for note in s.notes)

    def test_below_plus_two_a_team_moves(self):
        # 3 points after 5 rounds = plus one.
        s = suggest_pairings(self.teams(), set(), rounds_played=5)
        assert frozenset({1, 2}) not in pair_sets(s)

    @pytest.mark.parametrize("entrants, rounds", [
        ([entrant(1, mp=1)], 1), ([entrant(1, sides=["H", "A", "H"])], 3), ([entrant(1, mp=2.5, sides=["H"])], 3),
        ([], 0),
    ])
    def test_rounds_played_default(self, entrants, rounds):
        from app.swiss import _default_rounds_played
        assert _default_rounds_played(entrants) == rounds


class TestFloaters:
    def test_odd_group_floats_its_bottom_team_to_the_top_of_the_next(self):
        teams = field(6, {1: {"mp": 1}, 2: {"mp": 1}, 3: {"mp": 1}, 4: {"mp": 0.5}, 5: {"mp": 0.5}, 6: {"mp": 0.5}})
        s = suggest_pairings(teams, set())
        assert pair_sets(s) == {frozenset({1, 2}), frozenset({3, 4}), frozenset({5, 6})}
        assert s.notes == ["Table 2: T3 (1) plays down to T4 (0.5)."]

    def test_even_groups_never_float(self):
        teams = field(8, {i: {"mp": 1, "sides": ["H"]} for i in range(1, 5)})
        s = suggest_pairings(teams, set())
        assert not any("plays down" in note for note in s.notes)

    def test_floater_choice_adapts_to_avoid_a_rematch(self):
        teams = field(6, {1: {"mp": 1}, 2: {"mp": 1}, 3: {"mp": 1}})
        s = suggest_pairings(teams, {frozenset({3, 4})})
        cross = [p for p in pair_sets(s) if len({teams[i - 1].mp for i in p}) == 2]
        assert len(cross) == 1 and frozenset({3, 4}) not in pair_sets(s)

    def test_floater_choice_adapts_to_avoid_a_side_conflict(self):
        # The natural floater pair (3 v 4) both need home; another pairing avoids that.
        need_home = {"sides": ["H", "A", "A"]}
        teams = field(6, {1: {"mp": 1}, 2: {"mp": 1}, 3: {"mp": 1, **need_home}, 4: need_home})
        s = suggest_pairings(teams, set())
        cross = [p for p in pair_sets(s) if len({teams[i - 1].mp for i in p}) == 2]
        assert len(cross) == 1 and frozenset({3, 4}) not in pair_sets(s)
        assert not any("in a row" in note for note in s.notes)


class TestEntrant:
    @pytest.mark.parametrize("sides, last, streak", [
        ([], None, None), (["H"], "H", None), (["H", "A"], "A", None), (["A", "H", "H"], "H", "H"),
    ])
    def test_last_side_and_streak(self, sides, last, streak):
        e = entrant(1, sides=sides)
        assert (e.last_side, e.streak_side) == (last, streak)

    @pytest.mark.parametrize("sides, due", [
        ([], (None, 0)), (["H"], ("A", 80)), (["A"], ("H", 80)),
        (["H", "A"], ("H", 80)),                      # even count: alternate
        (["H", "H", "A"], ("A", 80)),                 # one over on home: even it out
        (["H", "A", "A"], ("H", 80)),
        (["H", "H"], ("A", 200)), (["A", "A"], ("H", 200)),
        (["H", "H", "A", "H"], ("A", 200)),           # two over: 200-point rule
        (["H", "H", "A", "A"], ("H", 80)),
    ])
    def test_due(self, sides, due):
        assert entrant(1, sides=sides).due == due
