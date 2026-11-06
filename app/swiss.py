"""Suggest team pairings for one Swiss round.

Pure Python plus networkx: no database access, so it is easy to test.
pairings.py collects the data from the database and calls suggest_pairings().

Method: every possible pair of teams gets a penalty (cost). When the pool has
an odd number of teams a dummy BYE node is added, so the bye and the pairings
are decided together. networkx then finds the perfect matching with the lowest
total penalty. Penalties are integers in widely separated tiers, so a more
important rule always beats any number of less important ones.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

# Penalty tiers, most important first.
REMATCH = 10**13
SECOND_BYE = 10**13
SAME_SCHOOL = 10**10
BYE_PER_HALF_POINT = 10**8      # bye goes to the lowest score group
SCORE_GAP = 10**6               # times (half-point gap)^2
SAME_SCHOOL_UNDEFEATED = 5 * 10**5
THIRD_IN_A_ROW = 10**5          # both teams would have the same 3rd-in-a-row side
BYE_RANK_STEP = 10**5           # per rank above the bottom team
SAME_SIDE_AGAIN = 100           # both teams last played the same side

BYE = "BYE"


@dataclass
class Entrant:
    id: int
    name: str
    tpn: int                      # tournament pairing number (1 = top seed)
    school_id: int | None
    mp: float                     # match points before this round
    sides: list = field(default_factory=list)   # "H"/"A" per earlier match
    had_full_bye: bool = False
    undefeated: bool = True

    @property
    def last_side(self):
        return self.sides[-1] if self.sides else None

    @property
    def streak_side(self):
        """The side played in each of the last two matches, if the same."""
        if len(self.sides) >= 2 and self.sides[-1] == self.sides[-2]:
            return self.sides[-1]
        return None


@dataclass
class Suggestion:
    pairs: list                   # [(home_id, away_id)] in table order
    bye: int | None               # team id receiving the full-point bye
    notes: list


def _other(side):
    return "A" if side == "H" else "H"


def _pair_cost(a, b, ctx):
    cost = 0
    if frozenset((a.id, b.id)) in ctx["played"]:
        cost += REMATCH
    if a.school_id is not None and a.school_id == b.school_id:
        both = a.undefeated and b.undefeated
        cost += SAME_SCHOOL_UNDEFEATED if both else SAME_SCHOOL

    pa, pb = ctx["pos"][a.id], ctx["pos"][b.id]
    ga, gb = ctx["gsize"][a.id], ctx["gsize"][b.id]
    gap2 = abs(round(2 * a.mp) - round(2 * b.mp))
    if gap2 == 0:
        i, j = sorted((pa, pb))
        dev = abs((j - i) - ga // 2)
        cost += dev * (2 * ga - i)
    else:
        cost += SCORE_GAP * gap2 * gap2
        # Floaters: prefer the bottom of the upper group and top of the lower.
        up, lo = (a, b) if a.mp > b.mp else (b, a)
        cost += 10 * (ctx["gsize"][up.id] - ctx["pos"][up.id])
        cost += 10 * (ctx["pos"][lo.id] - 1)

    # Colours (home/away): only matters a little.
    if a.streak_side and a.streak_side == b.streak_side:
        cost += THIRD_IN_A_ROW
    if a.last_side and a.last_side == b.last_side:
        cost += SAME_SIDE_AGAIN
    return cost


def _bye_cost(a, rank_from_bottom):
    cost = BYE_PER_HALF_POINT * round(2 * a.mp) + BYE_RANK_STEP * rank_from_bottom
    if a.had_full_bye:
        cost += SECOND_BYE
    return cost


def _context(pool, played):
    ranked = sorted(pool, key=lambda e: (-e.mp, e.tpn))
    counts, pos, gsize = {}, {}, {}
    for e in ranked:
        counts[e.mp] = counts.get(e.mp, 0) + 1
        pos[e.id] = counts[e.mp]
    for e in ranked:
        gsize[e.id] = counts[e.mp]
    return {
        "ranked": ranked,
        "rank": {e.id: n for n, e in enumerate(ranked)},
        "pos": pos,
        "gsize": gsize,
        "played": played,
    }


def _match(pool, played, with_bye):
    ctx = _context(pool, played)
    nodes = {e.id: e for e in pool}
    edges = []
    ids = list(nodes)
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            edges.append((ids[x], ids[y], _pair_cost(nodes[ids[x]], nodes[ids[y]], ctx)))
    if with_bye:
        n = len(ctx["ranked"])
        for e in pool:
            rank_from_bottom = n - 1 - ctx["rank"][e.id]
            edges.append((e.id, BYE, _bye_cost(e, rank_from_bottom)))
    ceiling = max(c for _, _, c in edges) + 1
    g = nx.Graph()
    for u, v, c in edges:
        g.add_edge(u, v, weight=ceiling - c)
    matching = nx.max_weight_matching(g, maxcardinality=True)
    return ctx, matching


def _assign_sides(high, low, table_number):
    """Return (home, away). high is the higher-ranked team."""
    if high.last_side:
        want_high = _other(high.last_side)
    elif low.last_side:
        want_high = low.last_side   # low swaps sides, so high takes low's last side
    else:
        want_high = "H" if table_number % 2 == 1 else "A"
    note = None
    want_low = _other(want_high)
    if low.streak_side == want_low:
        if high.streak_side != want_high:
            want_high = want_low
            want_low = _other(want_high)
        else:
            note = (f"{high.name} and {low.name} would each have a third "
                    "match in a row on the same side")
    return (high, low) if want_high == "H" else (low, high), note


def suggest_pairings(entrants, played):
    """entrants: list of Entrant. played: set of frozenset({id, id})."""
    notes = []
    pool = list(entrants)
    bye_id = None
    if len(pool) % 2 == 1:
        _, matching = _match(pool, played, with_bye=True)
        for u, v in matching:
            if u == BYE:
                bye_id = v
            elif v == BYE:
                bye_id = u
        pool = [e for e in pool if e.id != bye_id]
    if len(pool) >= 2:
        ctx, matching = _match(pool, played, with_bye=False)
    else:
        ctx, matching = _context(pool, played), set()

    by_id = {e.id: e for e in pool}
    ordered = []
    for u, v in matching:
        a, b = sorted((u, v), key=lambda i: ctx["rank"][i])
        ordered.append((a, b))
    ordered.sort(key=lambda p: (ctx["rank"][p[0]], ctx["rank"][p[1]]))

    pairs = []
    for table, (a, b) in enumerate(ordered, start=1):
        high, low = by_id[a], by_id[b]
        (home, away), note = _assign_sides(high, low, table)
        pairs.append((home.id, away.id))
        if note:
            notes.append(f"Table {table}: {note}.")
        if frozenset((a, b)) in played:
            notes.append(f"Table {table}: {high.name} and {low.name} have already played.")
        if high.school_id is not None and high.school_id == low.school_id:
            notes.append(f"Table {table}: {high.name} and {low.name} are from the same school.")
        if high.mp != low.mp:
            notes.append(f"Table {table}: {high.name} ({high.mp:g}) plays down to "
                         f"{low.name} ({low.mp:g}).")
    if bye_id is not None:
        e = next(x for x in entrants if x.id == bye_id)
        if e.had_full_bye:
            notes.append(f"{e.name} already had a full-point bye.")
    return Suggestion(pairs=pairs, bye=bye_id, notes=notes)