"""Individual grand prix: arena scoring, importing, standings and the page.

Lichess is never contacted: tests pass a fake `fetch` or replace `lichess_get`.
"""
from datetime import date

import pytest
from helpers import flashed, page_text
from sqlalchemy.exc import IntegrityError

from app import db
from app import grand_prix as gp
from app.grand_prix import GrandPrixError, Score, add_arena, grand_prix_standings, remove_arena, score_arena
from app.models import GrandPrixEvent, GrandPrixResult


def ranked(*usernames, team=None):
    """Lichess-style individual results, rank 1 first; arena scores fall by 2 per place."""
    return [{"rank": n, "username": u, "score": 40 - 2 * n, **({"team": team[u]} if team and u in team else {})}
            for n, u in enumerate(usernames, start=1)]


def fake_fetch(scores):
    """A fetch function returning fixed scores; records the calls it got."""
    calls = []

    def fetch(arena_id, kind):
        calls.append((arena_id, kind))
        return {u: (s if isinstance(s, Score) else Score(rank_points=s)) for u, s in scores.items()}

    fetch.calls = calls
    return fetch


class TestScoring:
    def test_weekly_pays_the_top_five(self):
        scores = score_arena("weekly", ranked("A", "b", "c", "d", "e", "f"))
        assert {u: s.rank_points for u, s in scores.items()} == {"a": 20, "b": 12, "c": 8, "d": 6, "e": 4}
        assert scores["a"].rank == 1 and scores["a"].team_bonus == 0

    def test_kickoff_pays_the_top_ten(self):
        scores = score_arena("kickoff", ranked(*[f"p{n}" for n in range(1, 13)]), team_payload=[])
        assert [scores[f"p{n}"].rank_points for n in range(1, 11)] == gp.KICKOFF_PRIZES
        assert "p11" not in scores

    def test_kickoff_team_bonus_needs_a_game_played(self):
        teams = {"t1": "TeamA", "t2": "TeamA", "t3": "TeamB", "far": "TeamC", "solo": "TeamD"}
        results = ranked(*[f"x{n}" for n in range(10)], "t1", "t2", "t3", "far", "solo", team=teams)
        payload = {"teams": [{"id": "teama"}, {"id": "teamb"}, {"id": "teamc"}, {"id": "teamd"}]}
        scores = score_arena("kickoff", results, payload, played_usernames={"T1", "t3", "far", "solo"})
        assert scores["t1"].team_bonus == 5 and scores["t1"].rank_points == 0
        assert "t2" not in scores                       # TeamA but never played
        assert scores["t3"].team_bonus == 3
        assert scores["far"].team_bonus == 2
        assert scores["solo"].team_bonus == 0           # 4th team: no bonus, but listed (played)

    def test_bonus_adds_to_rank_points(self):
        results = ranked("star", team={"star": "TeamA"})
        scores = score_arena("kickoff", results, [{"id": "TeamA"}], {"star"})
        assert (scores["star"].rank_points, scores["star"].team_bonus) == (30, 5)

    def test_players_who_played_are_listed_with_zero_points(self):
        results = ranked("a", "b", "c", "d", "e", "sixth", "joined_only")
        scores = score_arena("weekly", results, played_usernames={"a", "Sixth"})
        assert (scores["sixth"].rank, scores["sixth"].rank_points, scores["sixth"].arena_score) == (6, 0, 28)
        assert "joined_only" not in scores              # in the arena but never played a game
        assert scores["a"].arena_score == 38
        assert scores["b"].rank_points == 12            # prize winners always count

    def test_duplicate_usernames_count_once(self):
        results = [{"rank": 1, "username": "A", "score": 9}, {"rank": 2, "username": "a", "score": 7}]
        assert score_arena("weekly", results, played_usernames={"a"})["a"].rank_points == 20

    def test_weekly_ignores_teams(self):
        scores = score_arena("weekly", ranked("a", team={"a": "TeamA"}), [{"id": "teama"}], {"a"})
        assert scores["a"].team_bonus == 0

    def test_team_id_formats(self):
        assert gp.get_team_id({"id": "Abc"}) == "abc"
        assert gp.get_team_id({"teamId": "Abc"}) == "abc"
        assert gp.get_team_id({"team": {"id": "Abc"}}) == "abc"
        assert gp.get_team_id("nope") is None
        with pytest.raises(GrandPrixError):
            gp.extract_team_list("nope")

    def test_unknown_kind(self):
        with pytest.raises(GrandPrixError, match="weekly or kickoff"):
            score_arena("blitz", [])

    @pytest.mark.parametrize("raw, expected", [
        ("yLyBkTk7", "yLyBkTk7"),
        ("  yLyBkTk7 ", "yLyBkTk7"),
        ("https://lichess.org/tournament/yLyBkTk7", "yLyBkTk7"),
        ("lichess.org/tournament/yLyBkTk7#standings", "yLyBkTk7"),
    ])
    def test_arena_id_from_id_or_link(self, raw, expected):
        assert gp.parse_arena_id(raw) == expected

    @pytest.mark.parametrize("raw", ["", "short", "has spaces!", "https://lichess.org/swiss/abc"])
    def test_bad_arena_id(self, raw):
        with pytest.raises(GrandPrixError, match="8-character"):
            gp.parse_arena_id(raw)


class FakeResponse:
    def __init__(self, text="", data=None, status=200):
        self.text, self._data, self.status_code = text, data, status
        self.ok = status < 400

    def json(self):
        return self._data


PGN = """[Event "Arena"]
[White "T1"]
[Black "x0"]
[Result "1-0"]

1. e4 1-0

[Event "Arena"]
[White "?"]
[Black "x1"]
[Result "0-1"]

1. d4 0-1
"""


class TestFetching:
    def test_fetch_parses_lichess_responses(self, monkeypatch):
        responses = {
            "tournament/AAAAAAAA/results": FakeResponse('{"rank": 1, "username": "x0"}\n\n'
                                                         '{"rank": 2, "username": "T1", "team": "teamA"}\n'),
            "tournament/AAAAAAAA/teams": FakeResponse(data={"teams": [{"id": "teamA"}]}),
            "tournament/AAAAAAAA/games": FakeResponse(PGN),
        }
        monkeypatch.setattr(gp, "lichess_get", lambda endpoint, params=None: responses[endpoint])
        scores = gp.fetch_arena_scores("AAAAAAAA", "kickoff")
        assert (scores["x0"].rank_points, scores["x0"].team_bonus) == (30, 0)
        assert (scores["t1"].rank_points, scores["t1"].team_bonus) == (18, 5)

    def test_weekly_fetch_skips_team_calls(self, monkeypatch):
        seen = []
        results = "\n".join(f'{{"rank": {n}, "username": "p{n}", "score": {20 - n}}}' for n in range(1, 7))
        responses = {
            "tournament/AAAAAAAA/results": FakeResponse(results),
            "tournament/AAAAAAAA/games": FakeResponse('[White "p6"]\n[Black "p1"]\n[Result "1-0"]\n\n1. e4 1-0\n'),
        }

        def get(endpoint, params=None):
            seen.append(endpoint)
            return responses[endpoint]

        monkeypatch.setattr(gp, "lichess_get", get)
        scores = gp.fetch_arena_scores("AAAAAAAA", "weekly")
        assert scores["p1"].rank_points == 20
        assert (scores["p6"].rank_points, scores["p6"].arena_score) == (0, 14)   # 6th, played: listed
        assert scores["p5"].rank_points == 4                                     # prize counts without a game
        assert seen == ["tournament/AAAAAAAA/results", "tournament/AAAAAAAA/games"]

    @pytest.mark.parametrize("status, message", [
        (404, "no arena with that ID"), (429, "rate limit"), (500, r"error \(500\)"),
    ])
    def test_lichess_errors_become_messages(self, monkeypatch, status, message):
        monkeypatch.setattr(gp.SESSION, "get", lambda *a, **k: FakeResponse(status=status))
        with pytest.raises(GrandPrixError, match=message):
            gp.lichess_get("tournament/AAAAAAAA/results")

    def test_network_failure_becomes_a_message(self, monkeypatch):
        def boom(*a, **k):
            raise gp.requests.ConnectionError("down")

        monkeypatch.setattr(gp.SESSION, "get", boom)
        with pytest.raises(GrandPrixError, match="Couldn't reach Lichess"):
            gp.lichess_get("x")


class TestImport:
    def test_add_stores_every_participant(self, league):
        t = league.tournament()
        fetch = fake_fetch({"a": Score(rank=1, rank_points=20, arena_score=30), "b": Score(team_bonus=3),
                            "sixth": Score(rank=6, arena_score=11)})
        event, created = add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fetch)
        db.session.commit()
        assert created and fetch.calls == [("AAAAAAAA", "weekly")]
        assert event.label == "11/6"
        assert sorted((r.lichess_username, r.points, r.arena_score) for r in event.results) == [
            ("a", 20, 30), ("b", 3, 0), ("sixth", 0, 11)]

    def test_readding_replaces_results(self, league):
        t = league.tournament()
        add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({"a": 20, "b": 12}))
        db.session.commit()
        event, created = add_arena(t, "aaaaaaaa", "kickoff", date(2026, 11, 7),
                                   fetch=fake_fetch({"a": 30, "c": 18}))
        db.session.commit()
        assert not created
        assert GrandPrixEvent.query.count() == 1
        assert (event.kind, event.event_date) == ("kickoff", date(2026, 11, 7))
        assert sorted((r.lichess_username, r.points) for r in event.results) == [("a", 30), ("c", 18)]
        assert GrandPrixResult.query.count() == 2

    def test_same_arena_can_count_in_two_tournaments(self, league):
        t1, t2 = league.tournament("One"), league.tournament("Two")
        add_arena(t1, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({"a": 20}))
        add_arena(t2, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({"a": 20}))
        db.session.commit()
        assert GrandPrixEvent.query.count() == 2

    @pytest.mark.parametrize("arena_id, kind, when, message", [
        ("bad", "weekly", date(2026, 1, 1), "8-character"),
        ("AAAAAAAA", "blitz", date(2026, 1, 1), "weekly or kickoff"),
        ("AAAAAAAA", "weekly", None, "date"),
    ])
    def test_validation_happens_before_fetching(self, league, arena_id, kind, when, message):
        t = league.tournament()
        fetch = fake_fetch({})
        with pytest.raises(GrandPrixError, match=message):
            add_arena(t, arena_id, kind, when, fetch=fetch)
        assert fetch.calls == []

    def test_remove(self, league):
        t, other = league.tournament(), league.tournament("Other")
        event, _ = add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({"a": 20}))
        db.session.commit()
        with pytest.raises(GrandPrixError):
            remove_arena(other, event.id)
        remove_arena(t, event.id)
        db.session.commit()
        assert GrandPrixEvent.query.count() == 0 and GrandPrixResult.query.count() == 0

    def test_deleting_a_tournament_deletes_its_grand_prix(self, league):
        t = league.tournament()
        add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({"a": 20}))
        db.session.commit()
        db.session.delete(t)
        db.session.commit()
        assert GrandPrixEvent.query.count() == 0 and GrandPrixResult.query.count() == 0

    def test_one_row_per_username_per_arena(self, league):
        t = league.tournament()
        event, _ = add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({"a": 20}))
        db.session.add(GrandPrixResult(event=event, lichess_username="a", rank_points=1))
        with pytest.raises(IntegrityError):
            db.session.commit()


class TestStandings:
    def test_totals_add_up_across_arenas_and_link_players(self, league):
        t = league.tournament()
        team = league.team(t, "Eastview")
        ace = league.player(team, "Ada Ace", username="AdaChess")
        add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({"adachess": 20, "stranger": 12}))
        add_arena(t, "BBBBBBBB", "kickoff", date(2026, 10, 30),
                  fetch=fake_fetch({"adachess": Score(rank=2, rank_points=18, team_bonus=5), "stranger": 30}))
        db.session.commit()

        events, rows = grand_prix_standings(t)
        assert [e.label for e in events] == ["10/30", "11/6"]           # in date order
        first, second = rows
        assert (first.player, first.total, first.rank) == (ace, 43, 1)
        assert first.points == {events[0].id: 23, events[1].id: 20}
        assert (second.player, second.username, second.total, second.rank) == (None, "stranger", 42, 2)

    def test_ties_share_a_rank(self, league):
        t = league.tournament()
        add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({"bo": 8, "al": 8, "cy": 20}))
        db.session.commit()
        _, rows = grand_prix_standings(t)
        assert [(r.username, r.rank) for r in rows] == [("cy", 1), ("al", 2), ("bo", 2)]

    def test_arena_points_break_ties(self, league):
        t = league.tournament()
        add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({
            "al": Score(rank=3, rank_points=8, arena_score=20), "bo": Score(rank=6, arena_score=15)}))
        add_arena(t, "BBBBBBBB", "weekly", date(2026, 11, 13), fetch=fake_fetch({
            "bo": Score(rank=3, rank_points=8, arena_score=22), "al": Score(rank=7, arena_score=12),
            "cy": Score(rank=4, rank_points=6, arena_score=40)}))
        db.session.commit()
        _, rows = grand_prix_standings(t)
        # al and bo both have 8 grand prix points; bo scored 37 arena points to al's 32.
        assert [(r.username, r.total, r.arena_total, r.rank) for r in rows] == [
            ("bo", 8, 37, 1), ("al", 8, 32, 2), ("cy", 6, 40, 3)]

    def test_zero_point_participation_is_shown(self, league):
        t = league.tournament()
        event, _ = add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6),
                             fetch=fake_fetch({"sixth": Score(rank=6, arena_score=11)}))
        db.session.commit()
        _, rows = grand_prix_standings(t)
        assert [(r.username, r.points, r.total, r.rank) for r in rows] == [("sixth", {event.id: 0}, 0, 1)]

    def test_players_from_other_tournaments_are_not_linked(self, league):
        t, other = league.tournament(), league.tournament("Other")
        league.player(league.team(other, "Eastview"), "Ada Ace", username="adachess")
        add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({"adachess": 20}))
        db.session.commit()
        _, rows = grand_prix_standings(t)
        assert rows[0].player is None

    def test_empty(self, league):
        assert grand_prix_standings(league.tournament()) == ([], [])


class TestGrandPrixPage:
    def url(self, t):
        return f"/tournaments/{t.id}/grand-prix"

    def test_linked_from_the_tournament_page(self, league):
        t = league.tournament()
        assert self.url(t) in page_text(league.get(f"/tournaments/{t.id}"))

    def test_empty_page(self, league):
        page = page_text(league.get(self.url(league.tournament())))
        assert "Individual grand prix" in page and "No grand prix points yet" in page

    def test_add_arena_through_the_page(self, league, monkeypatch):
        t = league.tournament()
        team = league.team(t, "Eastview")
        league.player(team, "Ada Ace", username="adachess")
        monkeypatch.setattr(gp, "fetch_arena_scores", fake_fetch({
            "adachess": Score(rank=1, rank_points=20, arena_score=31), "stranger": Score(rank=2, rank_points=12),
            "sixth": Score(rank=6, arena_score=9)}))
        response = league.post(self.url(t), {"action": "add", "event_date": "2026-11-06", "kind": "weekly",
                                             "arena_id": "https://lichess.org/tournament/AAAAAAAA"})
        assert flashed(response, "Arena AAAAAAAA (11/6) added. 3 players took part.")
        page = page_text(response)
        assert "Ada Ace" in page and "Eastview" in page and "<b>20</b>" in page
        assert "Not on a roster" in page and "stranger" in page
        assert "Arena points (tiebreak)" in page and 'title="31 arena points"' in page
        assert 'title="9 arena points">0</td>' in page          # 6th place still shows, with 0

        response = league.post(self.url(t), {"action": "add", "event_date": "2026-11-06", "kind": "weekly",
                                             "arena_id": "AAAAAAAA"})
        assert flashed(response, "re-imported; its earlier results were replaced")

    @pytest.mark.parametrize("form, message", [
        ({"event_date": "2026-11-06", "kind": "weekly", "arena_id": "nope"}, "8-character"),
        ({"event_date": "", "kind": "weekly", "arena_id": "AAAAAAAA"}, "Enter the arena's date"),
        ({"event_date": "11/6", "kind": "weekly", "arena_id": "AAAAAAAA"}, "isn't a date"),
        ({"event_date": "2026-11-06", "kind": "blitz", "arena_id": "AAAAAAAA"}, "weekly or kickoff"),
    ])
    def test_bad_input_is_explained(self, league, monkeypatch, form, message):
        t = league.tournament()
        monkeypatch.setattr(gp, "fetch_arena_scores", fake_fetch({"a": 20}))
        assert flashed(league.post(self.url(t), {"action": "add", **form}), message)
        assert GrandPrixEvent.query.count() == 0

    def test_lichess_error_is_shown_and_nothing_saved(self, league, monkeypatch):
        t = league.tournament()

        def fail(arena_id, kind):
            raise GrandPrixError("Lichess has no arena with that ID. Check the tournament ID.")

        monkeypatch.setattr(gp, "fetch_arena_scores", fail)
        response = league.post(self.url(t), {"action": "add", "event_date": "2026-11-06", "kind": "weekly",
                                             "arena_id": "AAAAAAAA"})
        assert flashed(response, "no arena with that ID")
        assert GrandPrixEvent.query.count() == 0

    def test_remove_through_the_page(self, league):
        t = league.tournament()
        event, _ = add_arena(t, "AAAAAAAA", "weekly", date(2026, 11, 6), fetch=fake_fetch({"a": 20}))
        db.session.commit()
        response = league.post(self.url(t), {"action": "remove", "event_id": str(event.id)})
        assert flashed(response, "Arena AAAAAAAA (11/6) removed")
        assert GrandPrixEvent.query.count() == 0

    def test_unknown_action_and_missing_tournament(self, league):
        t = league.tournament()
        assert flashed(league.post(self.url(t), {"action": "nope"}), "Unknown grand prix action")
        assert league.get("/tournaments/999/grand-prix").status_code == 404


class TestScript:
    """scripts/individual_grand_prix.py, run against the test database."""

    @pytest.fixture
    def script(self, app, monkeypatch):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "scripts" / "individual_grand_prix.py"
        spec = importlib.util.spec_from_file_location("individual_grand_prix", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, "create_app", lambda: app)
        return module

    def test_adds_an_arena_to_the_newest_tournament(self, league, script, monkeypatch, capsys):
        league.tournament("Old")
        new = league.tournament("New")
        monkeypatch.setattr(gp, "fetch_arena_scores", fake_fetch({"adachess": 20}))
        script.main(["AAAAAAAA", "weekly", "2026-11-06"])
        out = capsys.readouterr().out
        assert "Arena AAAAAAAA (11/6) added: 1 players took part." in out
        assert "Individual grand prix: New" in out and "adachess" in out
        assert [e.arena_id for e in new.grand_prix_events] == ["AAAAAAAA"]

    def test_standings_only_and_errors(self, league, script, monkeypatch, capsys):
        t = league.tournament()
        script.main(["--standings", "--tournament", str(t.id)])
        assert "Individual grand prix" in capsys.readouterr().out
        with pytest.raises(SystemExit, match="8-character"):
            script.main(["nope", "weekly", "2026-11-06"])
        with pytest.raises(SystemExit, match="No tournament with id 999"):
            script.main(["--standings", "--tournament", "999"])
