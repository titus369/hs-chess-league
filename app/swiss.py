"""Suggest team pairings for one Swiss round, modelled on the US Chess rules
(Rule Book chapter 2, rules 27-29), adapted for a 4-board team league.

Pure Python plus networkx: no database access, so it is easy to test.
pairings.py collects the data from the database and calls suggest_pairings().

THE RULES, MOST IMPORTANT FIRST
-------------------------------
"Rank" means match points first, then seed (tpn); seed comes from the team's
average core rating. "Side" is home/away, the team version of a chess color
(the home team has White on boards 1 and 3).

 1. No rematches. No team gets a second full-point bye.          (US Chess 27A1)
 2. Teams from the same school do not play each other, even if it means a team
    plays outside its score group, as long as their score group is below
    "plus two" (see rule 5). Exception: if they are the only two teams tied
    for first and both are undefeated, they simply play.          (28N1b, 28N2)
 3. If the number of teams is odd, the full-point bye goes to the lowest score
    group, and inside it to the lowest-ranked team that has not had one.
 4. Teams play others on the same score whenever possible.         (27A2)
    When someone must play outside their group, it is better for ONE team to
    drop down two groups than for TWO teams to drop down one group each.
                                                                    (29D2)
 5. Same school inside a strong score group: if both teams are at "plus two"
    or better (at least two more match wins than losses; a draw or half-point
    bye counts as half a win), nobody moves to another group to avoid the
    school pairing. The group is rearranged to avoid it if it can be.
                                                                    (28N1a, 28N1c)
 6. The bye: among teams in the lowest group, the lowest-ranked gets it.
 7. No team plays on the same side a third match in a row. This is allowed
    to override rule 8 without any rating limit, so two top-half teams may
    meet to avoid it.                                               (29E5f)
 8. Inside a score group, the top half plays the bottom half: 1 v n/2+1,
    2 v n/2+2, and so on.                                           (27A3, 29C1)
 9. Sides ("due side"), adjusted with the US Chess rating limits:   (27A4, 27A5, 29E4)
      * A team that has had more home than away matches is due away (and
        the reverse). Evening out the count comes before alternating:
        home, home, away is due AWAY, not home.
      * A team with an even count is due the side opposite its last match.
      * To give teams their due sides, the standard pairing may change in
        three ways, but only between teams whose ratings are close:
          - transposition: swap two teams in the same half (new opponents,
            halves unchanged). Measured by the smaller of the two rating
            gaps that give the same pairings: swapping the two bottom-half
            teams, or the two top-half teams.                       (29C2, 29E5c)
          - interchange: the bottom team of the top half swaps halves with
            the top team of the bottom half. Only those two teams, and only
            once per score group, so at most one team plays up. An
            interchange breaks rule 8, so it counts as 80 points plus the
            rating gap: any transposition within 80 points is preferred, and
            an interchange is only used when it fixes at least two side
            clashes (or one 200-point clash) that transpositions can't.
                                                                    (29E5d, 29E5e)
          - a different team floats (rule 10).
        The limits, measured as the rating gap between the swapped teams:
          - 200-point rule: up to 200 rating points when the team is already
            2 matches over on one side (it would otherwise end up 3 over).
                                                                    (29E5b)
          - 80-point rule: up to 80 rating points for any other due side.
                                                                    (29E5a)
      * If two teams due the same side meet, the side goes to the team whose
        need is stronger (avoiding a third in a row, then the 200-point case,
        then the 80-point case); if they are equal, the higher-ranked team
        gets its due side.
10. Which team floats: the lowest-ranked team of the upper group plays the
    highest-ranked team it can in the next group, with the same rating limits
    as rule 9 when sides call for a different choice.               (29D1)
11. Ties are broken toward the standard order 1 v n/2+1, 2 v n/2+2, ...

THE METHOD
----------
Every possible pair of teams gets a penalty (cost). When the pool has an odd
number of teams a dummy BYE node is added, so the bye and the pairings are
decided together. networkx then finds the perfect matching with the lowest
total penalty.

Rule 1-7 penalties, and two same-half teams meeting other than in the one
allowed interchange, are integer tiers about 100x apart or more, so one
penalty from a higher tier always outweighs any combination of lower-tier
penalties. Rules 9-10 share one scale measured in rating points: a
transposition or different floater costs RATING_POINT per rating point
between the teams swapped (an interchange costs 80 points more). A missed due
side costs 80 (or 200) rating points, so the matching leaves the standard
pairing for sides exactly when the swap stays inside the 80 (or 200) point
limit, and never otherwise.

Ratings are each team's average core rating, with unrated players counted as
100. Unrated teams therefore count as equally rated with each other, so they
may always be swapped among themselves.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import networkx as nx

# Penalty tiers, most important first (rule numbers from the docstring).
REMATCH = 10**27                 # rule 1
SECOND_BYE = 10**27              # rule 1
SAME_SCHOOL_MOVE = 10**25        # rule 2: worth a team leaving its score group
SAME_SCHOOL_LEADERS = 0          # rule 2 exception: the only two undefeated leaders
BYE_PER_HALF_POINT = 10**23      # rule 3
DROPPED_TEAM = 10**21            # rule 4: per team paired outside its score group
EXTRA_GROUP = 10**18             # rule 4: per extra group a dropped team falls
SAME_SCHOOL = 10**16             # rule 5: both at plus two; rearrange, never move
BYE_RANK_STEP = 10**14           # rule 6: per rank above the bottom team
THIRD_IN_A_ROW = 10**12          # rule 7
SAME_HALF = 10**10               # rule 8: two same-half teams, other than the interchange
RATING_POINT = 2 * 10**5         # rules 9-10: cost of moving opponents 1 rating point
EQUALIZE = 200 * RATING_POINT + RATING_POINT // 2   # rule 9: 200-point rule
DUE_SIDE = 80 * RATING_POINT + RATING_POINT // 2    # rule 9: 80-point rule
INTERCHANGE = 80 * RATING_POINT  # rule 9: an interchange's cost on top of its rating gap
TIE_STEP = 1                     # rule 11: small tie-breaks, well under 1 rating point

PLUS_TWO = 2                     # match wins minus losses for a "strong" group
OVER_LIMIT = 2                   # matches over on one side that trigger the 200-point rule

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
    rating: float = 0.0           # average core rating (what the seed is based on)

    @property
    def last_side(self):
        return self.sides[-1] if self.sides else None

    @property
    def streak_side(self):
        """The side played in each of the last two matches, if the same."""
        if len(self.sides) >= 2 and self.sides[-1] == self.sides[-2]:
            return self.sides[-1]
        return None

    @property
    def side_diff(self):
        """Home matches minus away matches."""
        return self.sides.count("H") - self.sides.count("A")

    @property
    def due(self):
        """(side, limit): the side this team is due and the rating limit (200 or
        80) for swapping opponents to give it; (None, 0) if it has no history.
        Evening out the home/away count comes before alternating."""
        diff = self.side_diff
        if diff:
            side = "A" if diff > 0 else "H"
            return side, (200 if abs(diff) >= OVER_LIMIT else 80)
        if self.last_side:
            return _other(self.last_side), 80
        return None, 0


@dataclass
class Suggestion:
    pairs: list                   # [(home_id, away_id)] in table order
    bye: int | None               # team id receiving the full-point bye
    notes: list


def _other(side):
    return "A" if side == "H" else "H"


def _side_name(side):
    return "home" if side == "H" else "away"


def _side_cost(e, side):
    """Penalty for team e playing `side` this round."""
    cost = THIRD_IN_A_ROW if e.streak_side == side else 0
    due, limit = e.due
    if due and side != due:
        cost += EQUALIZE if limit == 200 else DUE_SIDE
    return cost


def _orientations(high, low, table_number):
    """Both ways to seat a pair, best first: (cost, preference, high_side).
    Equal costs go to the higher-ranked team's due side, then the lower-ranked
    team's, then home for the higher team on odd tables."""
    options = []
    for high_side in ("H", "A"):
        low_side = _other(high_side)
        cost = _side_cost(high, high_side) + _side_cost(low, low_side)
        pref = (high.due[0] != high_side,
                low.due[0] != low_side,
                (high_side == "H") != (table_number % 2 == 1))
        options.append((cost, pref, high_side))
    return sorted(options)


def _rating_gap(x, y):
    return abs(round(x.rating) - round(y.rating))


def _pair_cost(a, b, ctx):
    cost = 0
    if frozenset((a.id, b.id)) in ctx["played"]:
        cost += REMATCH
    if a.school_id is not None and a.school_id == b.school_id:
        if ctx["leaders"] == {a.id, b.id}:
            cost += SAME_SCHOOL_LEADERS
        elif ctx["strong"][a.id] and ctx["strong"][b.id]:
            cost += SAME_SCHOOL
        else:
            cost += SAME_SCHOOL_MOVE

    levels = abs(ctx["level"][a.id] - ctx["level"][b.id])
    if levels == 0:
        ia, ib = ctx["hidx"].get(a.id), ctx["hidx"].get(b.id)
        if ia is not None and ib is not None:
            members = ctx["homog"][a.mp]
            n, k = len(members), len(members) // 2
            i, j = sorted((ia, ib))

            def standard(idx):
                return members[idx + k] if idx < k else members[idx - k]

            # How far this pair is from the standard pairing, in rating points.
            # Each swap touches two pairs, so each pair carries half of it.
            if (i < k) != (j < k):
                # Top v bottom. A transposition is measured by the smaller of
                # the two rating differences that give the same pairings (29E5c):
                # swapping two bottom teams, or the two top teams it equals.
                gap = min(_rating_gap(members[j], standard(i)),
                          _rating_gap(members[i], standard(j)))
                cost += (RATING_POINT // 2) * gap
            elif (k - 1 if i < k else k) in (i, j):
                # Part of THE interchange: the bottom team of the top half
                # swaps with the top team of the bottom half (29E5d). It costs
                # INTERCHANGE (80 points) plus their rating gap, so any
                # transposition within 80 points is preferred (29E5e).
                gap = _rating_gap(members[k - 1], members[k])
                cost += (INTERCHANGE + RATING_POINT * gap) // 2
            else:
                # Any other two teams from the same half.
                cost += SAME_HALF
            cost += TIE_STEP * abs((j - i) - k) * (2 * n - i)
    else:
        cost += DROPPED_TEAM + EXTRA_GROUP * (levels - 1)
        up, lo = (a, b) if a.mp > b.mp else (b, a)
        # Rating limits on choosing a different floater or float opponent.
        cost += RATING_POINT * _rating_gap(up, ctx["float_out"][up.mp])
        cost += RATING_POINT * _rating_gap(lo, ctx["float_in"][lo.mp])
        cost += TIE_STEP * (ctx["gsize"][up.id] - ctx["pos"][up.id])
        cost += TIE_STEP * (ctx["pos"][lo.id] - 1)

    high, low = (a, b) if ctx["rank"][a.id] < ctx["rank"][b.id] else (b, a)
    cost += _orientations(high, low, 1)[0][0]
    return cost


def _bye_cost(a, rank_from_bottom):
    cost = BYE_PER_HALF_POINT * round(2 * a.mp) + BYE_RANK_STEP * rank_from_bottom
    if a.had_full_bye:
        cost += SECOND_BYE
    return cost


def _context(pool, played, rounds_played):
    """Ranks, score groups, and each group's planned top and bottom halves.

    A group's planned layout: a team floating in from the group above meets the
    group's top team; if the group is then odd, its bottom team floats down; the
    teams left are split into a top half and a bottom half. The plan only sets
    penalties, so the matching can still float different teams if that pairs
    the round better overall."""
    ranked = sorted(pool, key=lambda e: (-e.mp, e.tpn))
    groups = {}
    for e in ranked:
        groups.setdefault(e.mp, []).append(e)

    pos, gsize, level, hidx, homog = {}, {}, {}, {}, {}
    float_out, float_in = {}, {}
    carry = 0
    for lvl, mp in enumerate(sorted(groups, reverse=True)):
        residents = groups[mp]
        float_out[mp], float_in[mp] = residents[-1], residents[0]
        for n, e in enumerate(residents, start=1):
            pos[e.id], gsize[e.id], level[e.id] = n, len(residents), lvl
        incoming = min(carry, len(residents))
        outgoing = (carry + len(residents)) % 2
        homog[mp] = residents[incoming:len(residents) - outgoing]
        for n, e in enumerate(homog[mp]):
            hidx[e.id] = n
        carry = outgoing

    top_group = groups[max(groups)] if groups else []
    leaders = ({e.id for e in top_group}
               if len(top_group) == 2 and all(e.undefeated for e in top_group) else set())
    half = {}
    for mp, members in homog.items():
        k = len(members) // 2
        for n, e in enumerate(members):
            half[e.id] = "top" if n < k else "bottom"
    return {
        "ranked": ranked,
        "rank": {e.id: n for n, e in enumerate(ranked)},
        "pos": pos,
        "gsize": gsize,
        "level": level,
        "hidx": hidx,
        "homog": homog,
        "half": half,
        "float_out": float_out,
        "float_in": float_in,
        "strong": {e.id: 2 * e.mp - rounds_played >= PLUS_TWO for e in pool},
        "leaders": leaders,
        "played": played,
    }


def _match(pool, played, rounds_played, with_bye):
    ctx = _context(pool, played, rounds_played)
    nodes = {e.id: e for e in pool}
    ids = list(nodes)
    edges = [
        (ids[x], ids[y], _pair_cost(nodes[ids[x]], nodes[ids[y]], ctx))
        for x in range(len(ids)) for y in range(x + 1, len(ids))
    ]
    if with_bye:
        n = len(ctx["ranked"])
        for e in pool:
            edges.append((e.id, BYE, _bye_cost(e, n - 1 - ctx["rank"][e.id])))
    ceiling = max(c for _, _, c in edges) + 1
    g = nx.Graph()
    for u, v, c in edges:
        g.add_edge(u, v, weight=ceiling - c)
    return ctx, nx.max_weight_matching(g, maxcardinality=True)


def _assign_sides(high, low, table_number):
    """Return ((home, away), notes). high is the higher-ranked team."""
    _, _, high_side = _orientations(high, low, table_number)[0]
    notes = []
    for team, side in ((high, high_side), (low, _other(high_side))):
        if team.streak_side == side:
            notes.append(f"{team.name} plays a third match in a row {_side_name(side)}")
        elif team.due == (_other(side), 200):
            notes.append(f"{team.name} goes {OVER_LIMIT + 1} matches over on {_side_name(side)}")
    return ((high, low) if high_side == "H" else (low, high)), notes


def _default_rounds_played(entrants):
    """Earlier rounds, when the caller doesn't say: at least the longest side
    history, and at least the highest score (a team can't outscore its rounds)."""
    longest = max((len(e.sides) for e in entrants), default=0)
    best = max((e.mp for e in entrants), default=0)
    return max(longest, math.ceil(best))


def suggest_pairings(entrants, played, rounds_played=None):
    """entrants: list of Entrant. played: set of frozenset({id, id}).
    rounds_played: rounds before this one (byes included); used for plus two."""
    if rounds_played is None:
        rounds_played = _default_rounds_played(entrants)
    notes = []
    pool = list(entrants)
    bye_id = None
    if len(pool) % 2 == 1:
        _, matching = _match(pool, played, rounds_played, with_bye=True)
        for u, v in matching:
            if u == BYE:
                bye_id = v
            elif v == BYE:
                bye_id = u
        pool = [e for e in pool if e.id != bye_id]
    if len(pool) >= 2:
        ctx, matching = _match(pool, played, rounds_played, with_bye=False)
    else:
        ctx, matching = _context(pool, played, rounds_played), set()

    by_id = {e.id: e for e in pool}
    ordered = []
    for u, v in matching:
        a, b = sorted((u, v), key=lambda i: ctx["rank"][i])
        ordered.append((a, b))
    ordered.sort(key=lambda p: (ctx["rank"][p[0]], ctx["rank"][p[1]]))

    pairs = []
    for table, (a, b) in enumerate(ordered, start=1):
        high, low = by_id[a], by_id[b]
        (home, away), side_notes = _assign_sides(high, low, table)
        pairs.append((home.id, away.id))
        notes.extend(f"Table {table}: {note}." for note in side_notes)
        if frozenset((a, b)) in played:
            notes.append(f"Table {table}: {high.name} and {low.name} have already played.")
        if high.school_id is not None and high.school_id == low.school_id:
            notes.append(f"Table {table}: {high.name} and {low.name} are from the same school.")
        if high.mp != low.mp:
            notes.append(f"Table {table}: {high.name} ({high.mp:g}) plays down to "
                         f"{low.name} ({low.mp:g}).")
        elif ctx["half"].get(a) and ctx["half"].get(a) == ctx["half"].get(b):
            notes.append(f"Table {table}: {high.name} and {low.name} are both in the "
                         f"{ctx['half'][a]} half of their score group.")
    if bye_id is not None:
        e = next(x for x in entrants if x.id == bye_id)
        if e.had_full_bye:
            notes.append(f"{e.name} already had a full-point bye.")
    return Suggestion(pairs=pairs, bye=bye_id, notes=notes)
