"""Printed market labels -> canonical market identity.

Semantics are inferred from the label printed in the offer (period, overtime, line) and are
marked ``rules_unverified`` until compared with the operator's regulations. Two markets are
comparable only when every field of :class:`MarketSpec` that defines settlement is equal.
"""

from dataclasses import dataclass

from sports_edge.offers.text import fold

MAPPING_VERSION = "2026-10-31.1"

SPORTS = {
    "pilka nozna": "football",
    "tenis": "tennis",
    "koszykowka": "basketball",
    "siatkowka": "volleyball",
    "hokej na lodzie": "ice_hockey",
    "hokej": "ice_hockey",
    "baseball": "baseball",
    "boks": "boxing",
    "dart": "darts",
    "darts": "darts",
    "futbol amerykanski": "american_football",
    "mma": "mma",
    "pilka reczna": "handball",
    "esport": "esports",
    "e-sport": "esports",
    "esporty": "esports",
    "snooker": "snooker",
    "rugby": "rugby",
    "futsal": "futsal",
    "zuzel": "speedway",
    "kolarstwo": "cycling",
    "skoki narciarskie": "ski_jumping",
    "golf": "golf",
    "krykiet": "cricket",
    "tenis stolowy": "table_tennis",
    "siatkowka plazowa": "beach_volleyball",
    "unihokej": "floorball",
    "pilka wodna": "water_polo",
    "badminton": "badminton",
    "sporty motorowe": "motorsport",
    "formula 1": "motorsport",
    "biathlon": "biathlon",
    "szachy": "chess",
}

# Sports where "match winner" markets are settled including overtime/extra time.
OVERTIME_SPORTS = frozenset(
    {"basketball", "ice_hockey", "american_football", "baseball", "handball", "futsal"}
)

ONE_X_TWO = {"1": "home", "X": "draw", "2": "away"}
DOUBLE_CHANCE = {"1X": "home_draw", "X2": "draw_away", "12": "home_away"}
TWO_WAY = {"1": "home", "2": "away"}
UNDER_OVER = {"-": "under", "+": "over"}
YES_NO = {"TAK": "yes", "NIE": "no"}
HTFT = {
    f"{a}/{b}": f"{a}/{b}".replace("1", "H").replace("X", "D").replace("2", "A")
    for a in "1X2"
    for b in "1X2"
}


def sport_key(raw: str) -> tuple[str, bool]:
    """(key, known). Unknown sports keep a slug and are flagged, never merged by guess."""
    folded = fold(raw)
    if folded in SPORTS:
        return SPORTS[folded], True
    return "other:" + folded.replace(" ", "_"), False


@dataclass(frozen=True)
class MarketSpec:
    market_key: str
    period: str  # FT (regular time), FT_OT, MATCH, H1, H2, S1..S5
    includes_overtime: bool | None
    selections: dict[str, str]  # printed label -> canonical selection key
    line_kind: str | None = None  # total, handicap_score, handicap_points
    complete: bool = True  # selections form an exhaustive, disjoint outcome set
    void_possible: bool = False  # e.g. draw-no-bet; binary net EV formula does not apply

    def identity(self, line: str | None) -> tuple:
        return (self.market_key, self.period, self.includes_overtime, self.line_kind, line)


def _winner(sport: str) -> MarketSpec:
    overtime = True if sport in OVERTIME_SPORTS else None
    return MarketSpec("match_winner", "MATCH", overtime, TWO_WAY)


def _split_1x2(period: str) -> list[MarketSpec]:
    return [
        MarketSpec("1x2", period, False, ONE_X_TWO),
        MarketSpec("double_chance", period, False, DOUBLE_CHANCE, complete=False),
    ]


def sts_market(header: str, labels: tuple[str, ...], sport: str) -> list[MarketSpec] | None:
    """Specs for one STS header row; None when the header/labels pair is not catalogued."""
    h = fold(header)
    labels = tuple(labels)
    six = ("1", "X", "2", "1X", "X2", "12")
    three = ("1", "X", "2")
    halves = {"1. polowa": "H1", "2. polowa": "H2"}
    sets = {f"{n}. set": f"S{n}" for n in range(1, 6)}
    if h == "mecz" and labels == six:
        return _split_1x2("FT")
    if h == "mecz" and labels == three:
        return [MarketSpec("1x2", "FT", False, ONE_X_TWO)]
    if h in halves and labels == six:
        return _split_1x2(halves[h])
    if h in halves and labels == three:
        return [MarketSpec("1x2", halves[h], False, ONE_X_TWO)]
    if h in {"zwyciezca meczu", "zwyciezca walki"} and labels == ("1", "2"):
        return [_winner(sport)]
    if h in sets and labels == ("1", "2"):
        return [MarketSpec("set_winner", sets[h], None, TWO_WAY)]
    if h == "zaklad bez remisu" and labels == ("1", "2"):
        return [MarketSpec("draw_no_bet", "FT", False, TWO_WAY, void_possible=True)]
    if h == "handicap 1x2" and labels == three:
        return [MarketSpec("handicap_1x2", "FT", False, ONE_X_TWO, "handicap_score")]
    if h == "1. polowa - handicap" and labels == ("1", "2"):
        # Printed without a sign; which side receives the line is not verified.
        return [MarketSpec("handicap_unsigned", "H1", None, TWO_WAY, "handicap_points")]
    if h == "1. polowa lub wynik koncowy" and labels == three:
        return [MarketSpec("ht_or_ft", "FT", False, ONE_X_TWO, complete=False)]
    if h == "1. polowa / wynik koncowy" and labels == tuple(HTFT):
        return [MarketSpec("ht_ft", "FT", False, HTFT)]
    if h == "dokladny wynik" and labels and all(":" in label for label in labels):
        scores = {label: "score_" + label.replace(":", "_") for label in labels}
        return [MarketSpec("correct_score", "MATCH", None, scores, complete=False)]
    if labels == ("TAK", "NIE"):
        yes_no = {
            "obie druzyny - strzela gola": ("btts", "FT"),
            "1. druzyna - strzeli gola": ("home_to_score", "FT"),
            "2. druzyna - strzeli gola": ("away_to_score", "FT"),
            "1. polowa - 1. druzyna - strzeli gola": ("home_to_score", "H1"),
            "1. polowa - 2. druzyna - strzeli gola": ("away_to_score", "H1"),
            "czerwona kartka": ("red_card", "FT"),
        }
        if h in yes_no:
            key, period = yes_no[h]
            return [MarketSpec(key, period, False, YES_NO)]
        return None
    if labels == ("-", "+"):
        overtime = "(z dogrywka)" in h
        base = h.replace("(z dogrywka)", "").strip()
        period, ot = ("FT_OT", True) if overtime else ("FT", False)
        totals = {
            "liczba goli": ("total", period, ot),
            "liczba punktow": ("total", period if overtime else "MATCH", ot or None),
            "1. druzyna - liczba goli": ("team_total_home", period, ot),
            "2. druzyna - liczba goli": ("team_total_away", period, ot),
            "1. druzyna - liczba punktow": ("team_total_home", period, ot),
            "2. druzyna - liczba punktow": ("team_total_away", period, ot),
            "1. polowa - liczba goli": ("total", "H1", False),
            "1. polowa - liczba punktow": ("total", "H1", None),
        }
        totals.update({f"{n}. set - liczba punktow": ("total", f"S{n}", None) for n in range(1, 6)})
        if base in totals:
            key, period, ot = totals[base]
            return [MarketSpec(key, period, ot, UNDER_OVER, "total")]
    return None


# Superbet vocabulary seen in public descriptions; NOT validated on an authentic PDF.
GENERIC_HEADERS = {
    "mecz": "1x2",
    "1x2": "1x2",
    "wynik meczu": "1x2",
    "wynik": "1x2",
    "final": "1x2",
    "podwojna szansa": "double_chance",
    "zwyciezca": "match_winner",
    "zwyciezca meczu": "match_winner",
    "zaklad bez remisu": "draw_no_bet",
    "gole": "total",
    "liczba goli": "total",
    "suma goli": "total",
    "gole powyzej/ponizej": "total",
    "obie druzyny strzela": "btts",
    "obie druzyny strzela gola": "btts",
}

GENERIC_LABELS = {
    "1": "home",
    "x": "draw",
    "2": "away",
    "1x": "home_draw",
    "x2": "draw_away",
    "12": "home_away",
    "-": "under",
    "+": "over",
    "ponizej": "under",
    "powyzej": "over",
    "pon.": "under",
    "pow.": "over",
    "tak": "yes",
    "nie": "no",
}


def generic_market(header: str, labels: tuple[str, ...], sport: str) -> list[MarketSpec] | None:
    """Unverified mapping used by the layout-agnostic parser (Superbet and user PDFs)."""
    keys = tuple(GENERIC_LABELS.get(fold(label)) for label in labels)
    if None in keys or len(set(keys)) != len(keys):
        return None
    by_label = dict(zip(labels, keys, strict=True))
    family = GENERIC_HEADERS.get(fold(header).rstrip(":"))
    if set(keys) <= {"home", "draw", "away", "home_draw", "draw_away", "home_away"}:
        main = {k: v for k, v in by_label.items() if v in {"home", "draw", "away"}}
        dc = {k: v for k, v in by_label.items() if v in {"home_draw", "draw_away", "home_away"}}
        specs = []
        if set(main.values()) == {"home", "draw", "away"} and family != "match_winner":
            specs.append(MarketSpec("1x2", "FT", False, main))
        elif set(main.values()) == {"home", "away"} and not dc and family != "1x2":
            overtime = True if sport in OVERTIME_SPORTS else None
            specs.append(MarketSpec("match_winner", "MATCH", overtime, main))
        if dc and family in {None, "1x2", "double_chance"}:
            specs.append(MarketSpec("double_chance", "FT", False, dc, complete=False))
        return specs or None
    if set(keys) == {"under", "over"} and family in {None, "total"}:
        return [MarketSpec("total", "FT", False, by_label, "total")]
    if set(keys) == {"yes", "no"} and family == "btts":
        return [MarketSpec("btts", "FT", False, by_label)]
    return None
