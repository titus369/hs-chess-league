"""Every page renders, links between pages work, and bad URLs give 404s."""
import pytest

from app.models import Tournament
from helpers import flashed, page_text


@pytest.fixture
def season(league):
    """A tournament part-way through: 5 teams, round 1 complete, round 2
    paired with partial results, a requested bye, a late joiner and a substitute."""
    t = league.tournament(name="Fall League", rounds=5)
    for school in ("Alpha", "Bravo", "Charlie", "Delta", "Echo"):
        league.post_team(t, school)
    league.add_bye(t, 4, league.team_named(t, "Alpha A"), "half")
    league.generate(t, 1)
    league.finish_round(t, 1, ["1-0", "0.5-0.5", "0-1", "1F-0F"])
    league.generate(t, 2)
    first = league.round(t, 2).paired_matches[0]
    league.lineup(first, ["1-0", None, None, None])
    league.post_team(t, "Foxtrot")
    league.player(league.team_named(t, "Bravo A"), "Bravo Sub")
    return t


def all_urls(t):
    urls = ["/", "/tournaments/new", f"/tournaments/{t.id}", f"/tournaments/{t.id}/boards"]
    urls += [f"/tournaments/{t.id}/rounds/{n}" for n in range(1, t.num_rounds + 1)]
    for team in t.teams:
        urls += [f"/tournaments/{t.id}/teams/{team.id}/{page}" for page in ("roster", "history", "edit")]
    return urls


def test_every_page_renders_mid_tournament(league, season):
    for url in all_urls(season):
        response = league.get(url)
        assert response.status_code == 200, url
        assert "HS Chess League" in response.get_data(as_text=True), f"{url} should extend base.html"


def test_every_page_renders_for_an_empty_tournament(league):
    t = league.tournament()
    for url in all_urls(t):
        assert league.get(url).status_code == 200, url


@pytest.mark.parametrize("path", [
    "/tournaments/999",
    "/tournaments/999/boards",
    "/tournaments/{t}/rounds/0",
    "/tournaments/{t}/rounds/6",
    "/tournaments/{t}/teams/999/roster",
    "/tournaments/{t}/teams/999/history",
    "/tournaments/{t}/teams/999/edit",
])
def test_unknown_pages_are_404(league, season, path):
    assert league.get(path.format(t=season.id)).status_code == 404


def test_team_from_another_tournament_is_404(league, season):
    other = league.tournament("Other")
    team = league.team(other, "Zulu")
    assert league.get(f"/tournaments/{season.id}/teams/{team.id}/roster").status_code == 404


class TestIndexAndNewTournament:
    def test_index_lists_tournaments_with_team_counts(self, league, season):
        page = page_text(league.get("/"))
        assert "Fall League" in page and "(6 teams)" in page

    def test_index_with_no_tournaments(self, league):
        assert "No tournaments yet." in page_text(league.get("/"))

    def test_create_a_tournament(self, league):
        response = league.post("/tournaments/new", {"name": "Spring League", "num_rounds": "6"})
        t = Tournament.query.one()
        assert (t.name, t.num_rounds) == ("Spring League", 6)
        assert "<h1>Spring League</h1>" in page_text(response)

    @pytest.mark.parametrize("form, message", [
        ({"name": " ", "num_rounds": "7"}, "Give the tournament a name."),
        ({"name": "X", "num_rounds": "0"}, "Rounds must be at least 1."),
        ({"name": "X", "num_rounds": "seven"}, "Rounds must be at least 1."),
    ])
    def test_invalid_tournament(self, league, form, message):
        assert flashed(league.post("/tournaments/new", form), message)
        assert Tournament.query.count() == 0


class TestTournamentPage:
    def test_shows_standings_and_links(self, league, season):
        page = page_text(league.get(f"/tournaments/{season.id}"))
        assert "Team standings" in page and "Individual board standings" in page
        assert all(f"Round {n}</a>" in page for n in range(1, 6))
        for team in season.teams:
            assert f"/teams/{team.id}/history" in page


class TestTeamPages:
    def test_roster_page_links_to_history_and_edit(self, league, season):
        team = league.team_named(season, "Bravo A")
        page = page_text(league.get(f"/tournaments/{season.id}/teams/{team.id}/roster"))
        assert f"/teams/{team.id}/history" in page and f"/teams/{team.id}/edit" in page
        assert f'href="/tournaments/{season.id}"' in page
        assert "Bravo Sub (Substitute)" in page

    def test_history_shows_each_round(self, league, season):
        alpha = league.team_named(season, "Alpha A")
        page = page_text(league.get(f"/tournaments/{season.id}/teams/{alpha.id}/history"))
        assert all(f"<td>{n}</td>" in page for n in range(1, 6))
        assert "Half-point bye" in page and "(requested)" in page

    def test_history_shows_late_join_byes(self, league, season):
        foxtrot = league.team_named(season, "Foxtrot A")
        page = page_text(league.get(f"/tournaments/{season.id}/teams/{foxtrot.id}/history"))
        assert "(late join)" in page


class TestRoundPage:
    def test_unpaired_round_offers_create_and_generate(self, league):
        t = league.tournament()
        page = page_text(league.get(f"/tournaments/{t.id}/rounds/1"))
        assert "Create round 1" in page and "Generate suggested pairings" in page

    def test_paired_round_shows_tables_and_lineup_forms(self, league, season):
        page = page_text(league.get(f"/tournaments/{season.id}/rounds/2"))
        assert "Save pairings" in page and "Save table 1 lineup" in page and "Fill table" in page

    def test_unknown_action(self, league, season):
        assert flashed(league.round_action(season, 2, "explode"), "Unknown round action.")


class TestBoardsPage:
    def test_lists_players_by_board(self, league, season):
        page = page_text(league.get(f"/tournaments/{season.id}/boards"))
        assert all(f"<h2>Board {b}</h2>" in page for b in range(1, 5))
        assert "Player 1" in page

    def test_empty_tournament_shows_no_games(self, league):
        t = league.tournament()
        assert page_text(league.get(f"/tournaments/{t.id}/boards")).count("No games yet.") == 4
