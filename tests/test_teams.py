"""Adding teams (at the start and mid-tournament) and editing rosters, including
adding players to a team once the tournament is under way."""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Player, RosterEntry, School, Team
from app.standings import board_standings, team_standings
from helpers import flashed, flashes, page_text


def edit_form(team, school=None, core=None, remove=(), updates=None, new=()):
    """Form data for the Edit team page. `core` is the set of player ids that
    should be core (default: unchanged); `updates` maps player id -> field
    overrides; `new` is a list of dicts with name/username/rating/core/player_id."""
    data = {"school": school if school is not None else team.school.name, "row": []}
    for entry in team.roster:
        key = f"e_{entry.id}"
        data["row"].append(key)
        is_core = entry.player_id in core if core is not None else entry.is_core
        fields = {"name": entry.player.name, "username": entry.player.lichess_username,
                  "rating": "" if entry.player.rating is None else str(entry.player.rating)}
        fields.update((updates or {}).get(entry.player_id, {}))
        data.update({f"{key}_{field}": value for field, value in fields.items()})
        if is_core:
            data[f"{key}_core"] = "1"
        if entry.player_id in remove:
            data[f"{key}_remove"] = "1"
    for number, row in enumerate(new, start=1):
        key = f"n_{number}"
        data["row"].append(key)
        data[f"{key}_name"] = row.get("name", "")
        data[f"{key}_username"] = row.get("username", "")
        data[f"{key}_rating"] = row.get("rating", "")
        data[f"{key}_player_id"] = str(row.get("player_id", ""))
        if row.get("core"):
            data[f"{key}_core"] = "1"
    return data


def edit(league, team, **kwargs):
    return league.post(f"/tournaments/{team.tournament_id}/teams/{team.id}/edit", edit_form(team, **kwargs))


# ---------------------------------------------------------------------------
# Adding teams at the start of a tournament
# ---------------------------------------------------------------------------

class TestAddTeamAtStart:
    def test_creates_a_team_with_four_core_players_in_board_order(self, league):
        t = league.tournament()
        response = league.post_team(t, "Lincoln", ratings=[1800, 1700, None, 1500])
        assert flashed(response, "Added Lincoln A with 4 core players.")
        team = Team.query.one()
        assert team.name == "Lincoln A" and team.joined_round == 1
        assert [p.name for p in team.core_players] == [f"Lincoln Player {b}" for b in range(1, 5)]
        assert [p.lichess_username for p in team.core_players][0] == "lincoln_player_1"
        assert [p.rating for p in team.core_players] == [1800, 1700, None, 1500]
        assert [e.position for e in team.roster] == [1.0, 2.0, 3.0, 4.0]
        assert all(p.school == team.school for p in team.core_players)
        assert league.byes_of(team) == {}

    def test_second_team_from_a_school_is_team_b(self, league):
        t = league.tournament()
        league.post_team(t, "Lincoln")
        league.post_team(t, "Lincoln", names=[f"Second {b}" for b in range(1, 5)])
        assert sorted(team.name for team in Team.query) == ["Lincoln A", "Lincoln B"]

    def test_school_name_is_matched_ignoring_case_and_spacing(self, league):
        t = league.tournament()
        league.post_team(t, "Lincoln High")
        league.post_team(t, "  lincoln   HIGH ", names=[f"Second {b}" for b in range(1, 5)])
        assert School.query.count() == 1
        assert sorted(team.name for team in Team.query) == ["Lincoln High A", "Lincoln High B"]

    def test_schools_are_shared_across_tournaments_but_players_are_not(self, league):
        spring, fall = league.tournament("Spring"), league.tournament("Fall")
        league.post_team(spring, "Lincoln")
        league.post_team(fall, "Lincoln")
        assert School.query.count() == 1
        assert Player.query.filter_by(tournament_id=spring.id).count() == 4
        assert Player.query.filter_by(tournament_id=fall.id).count() == 4

    def test_requested_byes_are_saved(self, league):
        t = league.tournament()
        team_response = league.post_team(t, "Lincoln", byes={2: "half", 5: "zero"})
        assert flashed(team_response, "Added Lincoln A")
        team = Team.query.one()
        assert league.byes_of(team) == {2: ("half", "requested"), 5: ("zero", "requested")}

    def test_up_to_three_half_byes_may_be_requested(self, league):
        t = league.tournament()
        league.post_team(t, "Lincoln", byes={1: "half", 2: "half", 3: "half"})
        assert len(league.byes_of(Team.query.one())) == 3

    @pytest.mark.parametrize("kwargs, message", [
        ({"school": "  "}, "Enter a school name."),
        ({"names": ["A", "B", "C", ""]}, "Enter a name and Lichess username for all 4 core players."),
        ({"usernames": ["a", "b", "", "d"]}, "Enter a name and Lichess username for all 4 core players."),
        ({"ratings": ["1500", "abc", "", ""]}, "Ratings must be numbers, or left blank."),
        ({"ratings": ["1500", "-5", "", ""]}, "Ratings must be numbers, or left blank."),
        ({"byes": {1: "half", 2: "half", 3: "half", 4: "half"}}, "at most 3 half-point byes"),
        ({"byes": {1: "full"}}, "Invalid bye request for round 1."),
    ])
    def test_invalid_entries_are_rejected_and_nothing_is_saved(self, league, kwargs, message):
        t = league.tournament()
        school = kwargs.pop("school", "Lincoln")
        response = league.post_team(t, school, **kwargs)
        assert flashed(response, message), flashes(response)
        assert Team.query.count() == 0 and Player.query.count() == 0

    def test_new_team_shows_in_standings(self, league):
        t = league.tournament()
        league.post_team(t, "Lincoln")
        page = page_text(league.get(f"/tournaments/{t.id}"))
        assert "Lincoln A" in page

    def test_all_teams_added_before_round_1_are_paired_in_round_1(self, league):
        t = league.tournament()
        for school in ("Alpha", "Bravo", "Charlie", "Delta"):
            league.post_team(t, school)
        league.generate(t, 1)
        placed = {team_id for pair in league.opponents(t, 1) for team_id in pair}
        assert placed == {team.id for team in Team.query}


# ---------------------------------------------------------------------------
# Adding teams in the middle of a tournament
# ---------------------------------------------------------------------------

@pytest.fixture
def underway(league):
    """Four teams; rounds 1 and 2 complete; round 3 not paired."""
    t = league.tournament(rounds=6)
    teams = [league.team(t, school, ratings=(r,) * 4)
             for school, r in (("Alpha", 1800), ("Bravo", 1700), ("Charlie", 1600), ("Delta", 1500))]
    for number in (1, 2):
        league.generate(t, number)
        league.finish_round(t, number)
    return t, teams


class TestAddTeamMidTournament:
    def test_joins_at_the_first_unpaired_round_with_half_byes_for_missed_rounds(self, league, underway):
        t, _ = underway
        response = league.post_team(t, "Echo")
        echo = league.team_named(t, "Echo A")
        assert echo.joined_round == 3
        assert league.byes_of(echo) == {1: ("half", "late_join"), 2: ("half", "late_join")}
        assert flashed(response, "joins in round 3; rounds 1 to 2 were recorded as late-join byes (2 half-point)")

    def test_team_added_during_a_round_in_progress_joins_the_next_round(self, league, underway):
        t, _ = underway
        league.generate(t, 3)  # round 3 paired, no results yet
        league.post_team(t, "Echo")
        echo = league.team_named(t, "Echo A")
        assert echo.joined_round == 4
        assert set(league.byes_of(echo)) == {1, 2, 3}

    def test_late_join_byes_count_in_standings(self, league, underway):
        t, _ = underway
        league.post_team(t, "Echo")
        row = next(r for r in team_standings(t) if r["team"].name == "Echo A")
        assert (row["mp"], row["gp"], row["byes"], row["played"]) == (1.0, 4.0, 2, 0)

    def test_late_joiner_is_paired_from_its_join_round(self, league, underway):
        t, _ = underway
        league.post_team(t, "Echo")
        league.post_team(t, "Foxtrot")
        league.generate(t, 3)
        placed = {team_id for pair in league.opponents(t, 3) for team_id in pair}
        assert {league.team_named(t, "Echo A").id, league.team_named(t, "Foxtrot A").id} <= placed
        assert len(placed) == 6

    def test_late_joiner_with_odd_field_can_take_the_odd_bye(self, league, underway):
        t, teams = underway
        league.post_team(t, "Echo")
        league.generate(t, 3)
        rd = league.round(t, 3)
        assert len(rd.paired_matches) == 2 and len(rd.byes) == 1
        assert rd.byes[0].source == "odd"

    def test_late_joiner_sits_out_its_missed_rounds_when_they_are_regenerated(self, league, underway):
        t, _ = underway
        league.generate(t, 3)
        league.post_team(t, "Echo")  # joins round 4, late-join bye in round 3
        echo = league.team_named(t, "Echo A")
        league.round_action(t, 3, "clear")
        league.generate(t, 3)
        placed = {team_id for pair in league.opponents(t, 3) for team_id in pair}
        assert echo.id not in placed
        assert league.byes_of(echo)[3] == ("half", "late_join")

    def test_add_team_form_explains_the_late_entry(self, league, underway):
        t, _ = underway
        page = page_text(league.get(f"/tournaments/{t.id}"))
        assert "a team added now joins in round 3" in page
        assert 'name="bye_round_2"' not in page and 'name="bye_round_3"' in page

    def test_no_team_can_join_once_every_round_is_paired(self, league):
        t = league.tournament(rounds=1)
        league.team(t, "Alpha"), league.team(t, "Bravo")
        league.generate(t, 1)
        response = league.post_team(t, "Charlie")
        assert flashed(response, "Every round has already been paired")
        assert Team.query.count() == 2
        assert "no more teams can join" in page_text(league.get(f"/tournaments/{t.id}"))


# ---------------------------------------------------------------------------
# Adding players to a team in the middle of a tournament (Edit team page)
# ---------------------------------------------------------------------------

@pytest.fixture
def midseason(league):
    """Lincoln A/B, Roosevelt A/B; round 1 complete, round 2 paired."""
    t = league.tournament()
    la = league.team(t, "Lincoln", ratings=(1800,) * 4)
    lb = league.team(t, "Lincoln", ratings=(1300,) * 4)
    ra = league.team(t, "Roosevelt", ratings=(1700,) * 4)
    rb = league.team(t, "Roosevelt", ratings=(1200,) * 4)
    league.pair(t, 1, [(la, ra), (lb, rb)])
    league.finish_round(t, 1)
    league.pair(t, 2, [(la, rb), (lb, ra)])
    return t, la, lb, ra, rb


class TestAddPlayersMidTournament:
    def test_add_a_new_substitute(self, league, midseason):
        t, la, *_ = midseason
        response = edit(league, la, new=[{"name": "New Sub", "username": "newsub", "rating": "1650"}])
        assert flashed(response, "Team and roster updated.")
        entry = next(e for e in la.roster if e.player.name == "New Sub")
        assert not entry.is_core and entry.player.rating == 1650
        assert entry.player.school_id == la.school_id and entry.player.tournament_id == t.id
        assert len(la.roster) == 5 and len(la.core_players) == 4
        assert la.avg_rating == 1800

    def test_new_substitute_can_play_in_the_current_round(self, league, midseason):
        t, la, *_ = midseason
        edit(league, la, new=[{"name": "New Sub", "username": "newsub"}])
        sub = Player.query.filter_by(name="New Sub").one()
        assert "New Sub (Substitute)" in page_text(league.get(f"/tournaments/{t.id}/rounds/2"))
        match = league.match_of(t, 2, la)
        home = [p.id for p in la.core_players][:3] + [sub.id]
        response = league.lineup(match, ["1-0"] * 4, home=home)
        assert flashed(response, "Table 1 lineup saved."), flashes(response)
        assert any(row["player"] == sub for row in board_standings(t)[4])

    def test_add_an_existing_player_from_a_lower_team_as_substitute(self, league, midseason):
        t, la, lb, *_ = midseason
        borrowed = lb.core_players[0]
        response = edit(league, la, new=[{"player_id": borrowed.id, "name": borrowed.name,
                                          "username": borrowed.lichess_username}])
        assert flashed(response, "Team and roster updated."), flashes(response)
        assert borrowed in [e.player for e in la.roster]
        assert borrowed.core_team == lb  # still core on Lincoln B
        assert Player.query.count() == 16

    def test_higher_team_core_player_cannot_join_lower_team(self, league, midseason):
        t, la, lb, *_ = midseason
        star = la.core_players[0]
        response = edit(league, lb, new=[{"player_id": star.id, "name": star.name, "username": star.lichess_username}])
        assert flashed(response, "Lincoln A's core player cannot join the lower team Lincoln B."), flashes(response)
        assert star not in [e.player for e in lb.roster]

    def test_edit_page_only_offers_eligible_existing_players(self, league, midseason):
        t, la, lb, *_ = midseason
        lower_page = page_text(league.get(f"/tournaments/{t.id}/teams/{lb.id}/edit"))
        assert la.core_players[0].name not in lower_page
        upper_page = page_text(league.get(f"/tournaments/{t.id}/teams/{la.id}/edit"))
        assert lb.core_players[0].name in upper_page
        assert "Roosevelt" not in upper_page.split("Add players")[1]

    def test_player_core_elsewhere_can_only_be_added_as_substitute(self, league, midseason):
        t, la, lb, *_ = midseason
        borrowed = lb.core_players[0]
        core = {p.id for p in la.core_players[:3]} | {borrowed.id}
        response = edit(league, la, core=core, new=[{"player_id": borrowed.id, "name": borrowed.name,
                                                     "username": borrowed.lichess_username, "core": True}])
        assert flashed(response, "can only be added as a substitute")

    def test_promote_a_substitute_and_demote_a_core_player_who_has_played(self, league, midseason):
        t, la, *_ = midseason
        sub = league.player(la, "Rising Star", rating=2200)
        demoted = la.core_players[3]
        core = {p.id for p in la.core_players[:3]} | {sub.id}
        response = edit(league, la, core=core)
        assert flashed(response, "Team and roster updated."), flashes(response)
        assert set(la.core_players) == set(la.core_players[:3]) | {sub} and demoted not in la.core_players
        assert demoted in [e.player for e in la.roster]
        assert la.avg_rating == (1800 * 3 + 2200) / 4

    @pytest.mark.parametrize("extra_core", [True, False])
    def test_team_must_keep_exactly_four_core_players(self, league, midseason, extra_core):
        t, la, *_ = midseason
        if extra_core:
            response = edit(league, la, new=[{"name": "Fifth", "username": "fifth", "core": True}])
        else:
            response = edit(league, la, core={p.id for p in la.core_players[:3]})
        assert flashed(response, "A team must have exactly 4 core players.")
        assert len(la.core_players) == 4 and len(la.roster) == 4

    def test_player_who_has_played_cannot_be_removed(self, league, midseason):
        t, la, *_ = midseason
        veteran = la.core_players[0]
        sub = league.player(la, "Spare")
        core = {p.id for p in la.core_players[1:]} | {sub.id}
        response = edit(league, la, core=core, remove={veteran.id})
        assert flashed(response, "is already assigned in a Lincoln A lineup and cannot be removed.")
        assert veteran in [e.player for e in la.roster]

    def test_unplayed_substitute_can_be_removed(self, league, midseason):
        t, la, *_ = midseason
        sub = league.player(la, "Spare")
        response = edit(league, la, remove={sub.id})
        assert flashed(response, "Team and roster updated.")
        assert sub not in [e.player for e in la.roster]

    def test_roster_page_remove_button(self, league, midseason):
        t, la, *_ = midseason
        sub = league.player(la, "Spare")
        response = league.post(f"/tournaments/{t.id}/teams/{la.id}/roster/{sub.id}/remove", {})
        assert flashed(response, "Removed Spare from Lincoln A's roster.")
        veteran = la.core_players[0]
        response = league.post(f"/tournaments/{t.id}/teams/{la.id}/roster/{veteran.id}/remove", {})
        assert flashed(response, "has played for Lincoln A, so they can't be removed")
        assert veteran in [e.player for e in la.roster]

    def test_edit_player_details(self, league, midseason):
        t, la, *_ = midseason
        player = la.core_players[1]
        response = edit(league, la, updates={player.id: {"name": "Renamed", "rating": "1900"}})
        assert flashed(response, "Team and roster updated.")
        assert (player.name, player.rating) == ("Renamed", 1900)

    def test_roster_order_follows_the_form(self, league, midseason):
        t, la, *_ = midseason
        edit(league, la, new=[{"name": "Sub One", "username": "sub1"}, {"name": "Sub Two", "username": "sub2"}])
        assert [e.player.name for e in la.roster][-2:] == ["Sub One", "Sub Two"]

    @pytest.mark.parametrize("new, message", [
        ([{"name": "No Username"}], "Every new player needs a name and Lichess username"),
        ([{"name": "Bad", "username": "bad", "rating": "high"}], "Ratings must be numbers, or left blank."),
        ([{"player_id": "abc", "name": "x", "username": "x"}], "Choose a valid existing player."),
        ([{"player_id": 99999, "name": "x", "username": "x"}], "Choose an existing player from this school's roster"),
    ])
    def test_invalid_new_players_are_rejected(self, league, midseason, new, message):
        t, la, *_ = midseason
        before = Player.query.count()
        response = edit(league, la, new=new)
        assert flashed(response, message), flashes(response)
        assert Player.query.count() == before and len(la.roster) == 4

    def test_same_existing_player_twice_is_rejected(self, league, midseason):
        t, la, lb, *_ = midseason
        p = lb.core_players[0]
        row = {"player_id": p.id, "name": p.name, "username": p.lichess_username}
        response = edit(league, la, new=[row, row])
        assert flashed(response, "A player can only appear once on this team roster.")

    def test_tampered_row_keys_are_rejected(self, league, midseason):
        t, la, *_ = midseason
        data = edit_form(la)
        data["row"].append("e_99999")
        response = league.post(f"/tournaments/{t.id}/teams/{la.id}/edit", data)
        assert flashed(response, "Invalid roster form submission.")

    def test_blank_new_rows_are_ignored(self, league, midseason):
        t, la, *_ = midseason
        response = edit(league, la, new=[{}, {}, {}])
        assert flashed(response, "Team and roster updated.") and len(la.roster) == 4

    def test_school_can_be_changed_to_a_school_without_teams(self, league, midseason):
        t, la, *_ = midseason
        response = edit(league, la, school="Washington")
        assert flashed(response, "Team and roster updated.")
        assert la.name == "Washington A"

    @pytest.mark.xfail(raises=IntegrityError, reason="Known bug B5: moving a team to a school that already "
                                                "has a team with the same letter crashes (500).")
    def test_school_change_to_a_school_with_teams_does_not_crash(self, league, midseason):
        t, la, lb, ra, rb = midseason
        response = edit(league, lb, school="Roosevelt")
        assert response.status_code == 200
