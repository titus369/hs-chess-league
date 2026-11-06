import io, re, requests
import pandas as pd
import chess.pgn
from pathlib import Path
import json

INPUT_FILE = "grand_prix.xlsx"
OUTPUT_FILE = "grand_prix_new.xlsx"
TOURNAMENT_ID = "yLyBkTk7"
TOURNAMENT_TYPE = "kickoff"

WEEKLY_PRIZES = [20, 12, 8, 6, 4]
KICKOFF_PRIZES = [30, 18, 12, 9, 6, 5, 4, 3, 2, 1]
TEAM_BONUS = [5, 3, 2]

# Column names in the spreadsheet
NAME_COL = "Name"
USERNAME_COL = "Username"
SCHOOL_COL = "School"
TOTAL_COL = "Total Points"
KICKOFF_COL = "10/30"
ROUND_ONE_COL = "11/6"
ROUND_TWO_COL = "11/20"
ROUND_THREE_COL = "12/4"
ROUND_FOUR_COL = "12/11"
MIDSEASON_COL = "12/18"
ROUND_FIVE_COL = "1/15"
ROUND_SIX_COL = "1/22"
ROUND_SEVEN_COL = "1/29"

TOURNAMENT_COLUMNS = [
    KICKOFF_COL,
    ROUND_ONE_COL,
    ROUND_TWO_COL,
    ROUND_THREE_COL,
    ROUND_FOUR_COL,
    MIDSEASON_COL,
    ROUND_FIVE_COL,
    ROUND_SIX_COL,
    ROUND_SEVEN_COL,
]

BASE_URL = "https://lichess.org/api"
SESSION = requests.Session()

def lichess_get(endpoint, params=None):
    response = SESSION.get(
        f"{BASE_URL}/{endpoint}",
        params=params,
        timeout=60,
    )

    if response.status_code == 429:
        raise RuntimeError(
            "Lichess rate limit reached. Wait one minute "
            "before trying again."
        )

    response.raise_for_status()
    return response
    
def get_individual_results(tournament_id):
    response = lichess_get(
        f"tournament/{tournament_id}/results",
        params={"nb": 1000},
    )

    return [json.loads(line) for line in response.text.splitlines() if line.strip()]

def get_team_results(tournament_id):
    response = lichess_get(
        f"tournament/{tournament_id}/teams",
    )
    return response.json()

def get_played_usernames(tournament_id):
    """
    Parse tournament PGN and identify players who played
    at least one game.
    """
    response = lichess_get(
        f"tournament/{tournament_id}/games",
        params={"pgnInJson": "false"},
    )

    pgn_file = io.StringIO(response.text)
    played = set()

    while True:
        game = chess.pgn.read_game(pgn_file)

        if game is None:
            break

        for color in ("White", "Black"):
            username = game.headers.get(color, "").strip()

            if username and username != "?":
                played.add(username.lower())

    return played

def normalize_username(username):
    if pd.isna(username):
        return ""

    return str(username).strip().lower()


def award_by_rank(results, prize_array):
    """
    Array index 0 is rank 1.
    Array index 1 is rank 2, etc.
    """
    awards = {}

    for player in results:
        username = normalize_username(
            player.get("username")
        )

        rank = player.get("rank")

        if not username or rank is None:
            continue

        rank = int(rank)

        if 1 <= rank <= len(prize_array):
            awards[username] = prize_array[rank - 1]

    return awards

def extract_team_list(payload):
    """Normalize the team standings response."""
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        teams = payload.get("teams")

        if isinstance(teams, list):
            return teams

    raise ValueError(
        "Unexpected team standings format. "
        "Inspect the Lichess response."
    )

def get_team_id(team):
    """Extract a Lichess team ID."""
    if not isinstance(team, dict):
        return None

    team_id = team.get("id") or team.get("teamId")

    if team_id:
        return str(team_id).lower()

    nested = team.get("team")

    if isinstance(nested, dict):
        if nested.get("id"):
            return str(nested["id"]).lower()

    return None

def calculate_team_bonus(
    individual_results,
    team_payload,
    played_usernames,
):
    """
    Award bonuses to members of the top 3 teams
    who played at least one game.
    """
    teams = extract_team_list(team_payload)

    bonus_by_team = {}

    for rank, team in enumerate(
        teams[:len(TEAM_BONUS)],
        start=1,
    ):
        team_id = get_team_id(team)

        if team_id:
            bonus_by_team[team_id] = TEAM_BONUS[rank - 1]

    bonuses = {}

    for player in individual_results:
        username = normalize_username(
            player.get("username")
        )

        team_id = player.get("team")

        if not username or not team_id:
            continue

        team_id = str(team_id).lower()

        if username in played_usernames:
            if team_id in bonus_by_team:
                bonuses[username] = bonus_by_team[team_id]

    return bonuses

def load_spreadsheet():
    path = Path(INPUT_FILE)

    if not path.exists():
        raise FileNotFoundError(
            f"Input file not found: {INPUT_FILE}"
        )

    df = pd.read_excel(path)

    required = [
        NAME_COL,
        USERNAME_COL,
        SCHOOL_COL,
        TOTAL_COL,
    ]

    for column in required:
        if column not in df.columns:
            df[column] = ""

    df[USERNAME_COL] = df[USERNAME_COL].fillna("")

    df[TOTAL_COL] = pd.to_numeric(
        df[TOTAL_COL],
        errors="coerce",
    ).fillna(0)

    # Ensure all tournament columns exist.
    for column in TOURNAMENT_COLUMNS:
        if column not in df.columns:
            df[column] = 0

    return df


def choose_tournament_column(df):
    """
    Kickoff uses the kickoff column.
    Weekly events use the next empty weekly column.
    """
    if TOURNAMENT_TYPE == "kickoff":
        return KICKOFF_COL

    weekly_columns = [
        column
        for column in TOURNAMENT_COLUMNS
        if column != KICKOFF_COL
    ]

    for column in weekly_columns:
        values = pd.to_numeric(
            df[column],
            errors="coerce",
        ).fillna(0)

        if not (values != 0).any():
            return column

    raise ValueError(
        "No empty weekly columns remain. "
        "Add another date to TOURNAMENT_COLUMNS."
    )


def update_spreadsheet(
    df,
    tournament_points,
    tournament_column,
):
    """
    Update existing usernames or append new users.
    New users receive blank name and school fields.
    """
    username_to_index = {}

    for index, row in df.iterrows():
        username = normalize_username(
            row[USERNAME_COL]
        )

        if username:
            username_to_index[username] = index

    for username, points in tournament_points.items():
        username = normalize_username(username)

        if not username:
            continue

        if username in username_to_index:
            index = username_to_index[username]

        else:
            new_row = {
                column: 0
                for column in df.columns
            }

            new_row[NAME_COL] = ""
            new_row[USERNAME_COL] = username
            new_row[SCHOOL_COL] = ""
            new_row[TOTAL_COL] = 0

            df.loc[len(df)] = new_row

            index = df.index[-1]
            username_to_index[username] = index

        current = pd.to_numeric(
            pd.Series([
                df.at[index, tournament_column]
            ]),
            errors="coerce",
        ).fillna(0).iloc[0]

        df.at[index, tournament_column] = (
            current + points
        )

    # Recalculate grand total from tournament columns.
    df[TOTAL_COL] = (
        df[TOURNAMENT_COLUMNS]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0)
        .sum(axis=1)
    )

    # Sort highest total first.
    df = df.sort_values(
        TOTAL_COL,
        ascending=False,
        kind="stable",
    ).reset_index(drop=True)

    return df


# ============================================================
# MAIN
# ============================================================

def main():

    if TOURNAMENT_TYPE not in {
        "weekly",
        "kickoff",
    }:
        raise ValueError(
            "TOURNAMENT_TYPE must be 'weekly' or 'kickoff'."
        )

    df = load_spreadsheet()

    tournament_column = choose_tournament_column(df)

    print(f"Using column: {tournament_column}")

    print("Fetching individual standings...")
    individual_results = get_individual_results(
        TOURNAMENT_ID
    )

    if TOURNAMENT_TYPE == "weekly":

        tournament_points = award_by_rank(
            individual_results,
            WEEKLY_PRIZES,
        )

    else:

        tournament_points = award_by_rank(
            individual_results,
            KICKOFF_PRIZES,
        )

        print("Fetching team standings...")
        team_results = get_team_results(
            TOURNAMENT_ID
        )

        print("Checking game participation...")
        played_usernames = get_played_usernames(
            TOURNAMENT_ID
        )

        team_bonuses = calculate_team_bonus(
            individual_results,
            team_results,
            played_usernames,
        )

        # Combine individual and team bonus points.
        for username, bonus in team_bonuses.items():

            tournament_points[username] = (
                tournament_points.get(username, 0)
                + bonus
            )

    print("Updating spreadsheet...")

    df = update_spreadsheet(
        df,
        tournament_points,
        tournament_column,
    )

    df.to_excel(
        OUTPUT_FILE,
        index=False,
    )

    print(f"Saved updated standings: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
