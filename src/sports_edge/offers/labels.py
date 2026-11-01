"""Polish display names of markets, periods and selections (one source for all views).

A market is always shown with its settlement period and line: "1X2" alone is ambiguous in
the STS offer, which prints the same 1 / X / 2 columns for the match, both halves and
"1. połowa lub wynik końcowy" under different bet numbers.
"""

MARKETS = {
    "1x2": "1X2",
    "double_chance": "Podwójna szansa",
    "match_winner": "Zwycięzca",
    "draw_no_bet": "Zakład bez remisu",
    "total": "Suma poniżej/powyżej",
    "team_total_home": "Suma gospodarzy",
    "team_total_away": "Suma gości",
    "btts": "Obie drużyny strzelą",
    "home_to_score": "Gospodarze strzelą",
    "away_to_score": "Goście strzelą",
    "ht_or_ft": "1. połowa lub wynik końcowy",
    "ht_ft": "Połowa / koniec",
    "handicap_1x2": "Handicap 1X2",
    "handicap_unsigned": "Handicap (znak niezweryfikowany)",
    "red_card": "Czerwona kartka",
    "correct_score": "Dokładny wynik",
    "set_winner": "Zwycięzca seta",
    "unknown": "Nieskatalogowany",
}
PERIODS = {
    "FT": "czas regulaminowy",
    "FT_OT": "z dogrywką",
    "MATCH": "cały mecz",
    "H1": "1. połowa",
    "H2": "2. połowa",
    "S1": "1. set",
    "S2": "2. set",
    "S3": "3. set",
    "S4": "4. set",
    "S5": "5. set",
}
SELECTIONS = {
    "home": "1",
    "draw": "X",
    "away": "2",
    "home_draw": "1X",
    "draw_away": "X2",
    "home_away": "12",
    "under": "Poniżej",
    "over": "Powyżej",
    "yes": "Tak",
    "no": "Nie",
}


def market_label(market_key: str, period: str, line: str | None = None) -> str:
    """E.g. "1X2 — 1. połowa", "Handicap 1X2 — czas regulaminowy (0:1)"."""
    name = MARKETS.get(market_key, market_key)
    text = f"{name} — {PERIODS.get(period, period)}"
    return f"{text} ({line})" if line else text
