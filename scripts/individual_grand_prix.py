"""Add a Lichess arena to a tournament's individual grand prix from the command line.

The app's "Individual grand prix" page does the same thing; this script is for
batch work. Scoring and standings live in app/grand_prix.py.

    python scripts/individual_grand_prix.py ARENA_ID weekly|kickoff YYYY-MM-DD [--tournament ID]
    python scripts/individual_grand_prix.py --standings [--tournament ID]

Without --tournament, the most recently created tournament is used.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app, db  # noqa: E402
from app.grand_prix import KINDS, GrandPrixError, add_arena, grand_prix_standings  # noqa: E402
from app.models import Tournament  # noqa: E402


def pick_tournament(tournament_id):
    if tournament_id is not None:
        t = db.session.get(Tournament, tournament_id)
        if t is None:
            raise SystemExit(f"No tournament with id {tournament_id}.")
        return t
    t = Tournament.query.order_by(Tournament.created_at.desc(), Tournament.id.desc()).first()
    if t is None:
        raise SystemExit("There are no tournaments yet. Create one in the app first.")
    return t


def print_standings(t):
    events, rows = grand_prix_standings(t)
    print(f"\nIndividual grand prix: {t.name}")
    header = f"{'#':>3}  {'Player':<26}{'School':<18}{'Username':<22}" + "".join(f"{e.label:>7}" for e in events)
    print(header + f"{'Total':>7}{'Arena':>7}")
    for r in rows:
        name = r.player.name if r.player else "(not on a roster)"
        school = r.player.school.name if r.player else ""
        cells = "".join(f"{r.points.get(e.id, ''):>7}" for e in events)
        print(f"{r.rank:>3}  {name:<26}{school:<18}{r.username:<22}{cells}{r.total:>7}{r.arena_total:>7}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("arena_id", nargs="?", help="Lichess arena ID or link")
    parser.add_argument("kind", nargs="?", choices=list(KINDS), help="scoring system")
    parser.add_argument("event_date", nargs="?", type=date.fromisoformat, help="arena date, YYYY-MM-DD")
    parser.add_argument("--tournament", type=int, help="league tournament id (default: newest)")
    parser.add_argument("--standings", action="store_true", help="only print the standings")
    args = parser.parse_args(argv)

    app = create_app()
    with app.app_context():
        t = pick_tournament(args.tournament)
        if not args.standings:
            if not (args.arena_id and args.kind and args.event_date):
                parser.error("give ARENA_ID, weekly|kickoff and YYYY-MM-DD, or use --standings")
            try:
                event, created = add_arena(t, args.arena_id, args.kind, args.event_date)
            except GrandPrixError as err:
                db.session.rollback()
                raise SystemExit(str(err)) from None
            db.session.commit()
            print(f"Arena {event.arena_id} ({event.label}) {'added' if created else 're-imported'}: "
                  f"{len(event.results)} players took part.")
        print_standings(t)


if __name__ == "__main__":
    main()
