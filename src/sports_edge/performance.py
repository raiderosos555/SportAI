"""Effectiveness of the coupon builder (TASK 005): coupons and their selections.

Only coupons saved by the builder count here (they carry a profile and the proposal with the
predicted probability). Per profile: hit rate with a Wilson interval, the average predicted
probability against the observed hit rate, ROI after the STS stake coefficient and payout
tax, maximum drawdown and streaks. Per selection (each one counted once, even when several
profiles used it): hit rate by sport, league, market and probability range against the
mean forecast. A handful of settled coupons proves nothing — intervals show how little.
"""

import json
import math
from collections import Counter, defaultdict
from decimal import Decimal

from sports_edge.coupon_store import counted_sql
from sports_edge.models.calibration import wilson_interval
from sports_edge.offers.labels import market_label
from sports_edge.paper import latest_result, latest_settlement, resolve
from sports_edge.storage import Store

PROBABILITY_BINS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001))


def _mean(values) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


SETTLED = {"win", "loss", "push", "void", "half_win", "half_loss"}


def builder_coupons(
    store: Store, *, versions: bool = False, everything: bool = False
) -> list[dict]:
    """Saved proposals in start order, with their latest settlement (if any).

    TASK 010: only the coupons that count — the official coupon of a profile and day and the
    user's coupons (a coupon saved before lineage existed counts as well). Duplicates never
    count; ``versions`` returns the price updates and revisions instead (diagnostics only),
    ``everything`` every saved coupon with its relation (the "Wersje" view of Historia)."""
    condition = (
        "1=1"
        if everything
        else "g.relation IN ('price_update','revision')"
        if versions
        else counted_sql("c")
    )
    out = []
    for coupon in store.rows(
        f"""SELECT c.*, g.relation, g.official_coupon_id FROM coupons c
        LEFT JOIN coupon_lineage g ON g.coupon_id=c.id
        WHERE c.profile IS NOT NULL AND c.proposal_json IS NOT NULL
        AND {condition} ORDER BY c.first_start_at, c.created_at"""
    ):
        settlement = latest_settlement(store, coupon["id"])
        out.append(
            {
                **coupon,
                "proposal": json.loads(coupon["proposal_json"]),
                "outcome": settlement["outcome"] if settlement else None,
                "net_profit": Decimal(settlement["net_profit"]) if settlement else None,
                "payout": Decimal(settlement["payout"]) if settlement else None,
            }
        )
    return out


def _streaks(outcomes: list[str]) -> dict:
    longest = {"win": 0, "loss": 0}
    current, run = None, 0
    for outcome in outcomes:
        if outcome not in longest:
            current, run = None, 0  # a void/push interrupts the series
            continue
        run = run + 1 if outcome == current else 1
        current = outcome
        longest[outcome] = max(longest[outcome], run)
    return {
        "longest_win_streak": longest["win"],
        "longest_loss_streak": longest["loss"],
        "current_streak": (
            f"{run} × {'wygrana' if current == 'win' else 'przegrana'}" if current else "—"
        ),
    }


def coupon_performance(store: Store, *, versions: bool = False) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for coupon in builder_coupons(store, versions=versions):
        groups[coupon["profile"]].append(coupon)
    out = {}
    for profile, coupons in groups.items():
        settled = [c for c in coupons if c["outcome"] in SETTLED]
        decided = [c for c in settled if c["outcome"] in {"win", "loss"}]
        wins = sum(c["outcome"] == "win" for c in decided)
        stake = sum((Decimal(c["stake"]) for c in settled), Decimal(0))
        profit = sum((c["net_profit"] for c in settled), Decimal(0))
        peak = running = drawdown = Decimal(0)
        for c in settled:
            running += c["net_profit"]
            peak = max(peak, running)
            drawdown = max(drawdown, peak - running)
        predicted = [c["proposal"]["probability"] for c in decided]
        # a coupon of the user may have no model probability (no validated model for its
        # market): it counts in hit rate and ROI, the averages cover the known ones only
        calibrated = [p["calibrated"] for p in predicted if p.get("calibrated") is not None]
        conservative = [p["conservative"] for p in predicted if p.get("conservative") is not None]
        out[profile] = {
            "label": coupons[0]["proposal"].get("label", profile),
            "coupons": len(coupons),
            "pending": len(coupons) - len(settled),
            "settled": len(settled),
            "wins": wins,
            "hit_rate": wins / len(decided) if decided else None,
            "hit_rate_ci95": wilson_interval(wins, len(decided)) if decided else None,
            "avg_predicted": sum(calibrated) / len(calibrated) if calibrated else None,
            "avg_predicted_conservative": (
                sum(conservative) / len(conservative) if conservative else None
            ),
            "avg_legs": sum(len(c["proposal"]["legs"]) for c in coupons) / len(coupons),
            "avg_odds": sum(float(c["total_odds"]) for c in coupons) / len(coupons),
            "stake": stake,
            "profit": profit,
            "roi": profit / stake if stake else None,
            "max_drawdown": drawdown,
            **_streaks([c["outcome"] for c in settled]),
        }
    return out


def selection_outcomes(store: Store) -> list[dict]:
    """Every distinct SELECTION of a counted coupon with its outcome (None = not yet known).

    Counted once by ``selection_id`` (stable between STS documents) — the same pick saved
    from two PDFs, in several profiles or in a duplicate coupon is one observation."""
    seen: dict[str, dict] = {}
    for coupon in builder_coupons(store):
        for leg in coupon["proposal"]["legs"]:
            key = leg.get("selection_id") or _selection_of(store, leg["odds_id"])
            if key is None:
                continue
            if key in seen:
                seen[key]["profiles"].add(coupon["profile"])
                continue
            rows = store.rows(
                """SELECT s.key selection, m.market_key, m.period, m.overtime, m.line,
                e.id event_id, e.sport, e.league, e.start_at FROM odds o
                JOIN selections s ON s.id=o.selection_id JOIN markets m ON m.id=s.market_id
                JOIN events e ON e.id=m.event_id WHERE o.id=?""",
                (leg["odds_id"],),
            )
            if not rows:
                continue
            market = rows[0]
            result = latest_result(store, market["event_id"])
            outcome = resolve(market, market["selection"], result) if result else None
            seen[key] = {
                **leg,
                "selection_id": key,
                "sport": market["sport"],
                "league": market["league"],
                "market_family": market_label(
                    market["market_key"], market["period"], market["line"]
                ),
                # TASK 014: the match and the market of the offer row (a leg saved by older
                # code may lack them) — the unit of the leg table is the match
                "match": market["event_id"],
                "market_group": market_group(market["market_key"], market["selection"]),
                "match_start": market["start_at"],
                "outcome": outcome.value if outcome else None,
                "profiles": {coupon["profile"]},
            }
    return list(seen.values())


def leg_outcomes(store: Store, coupon_id: str) -> dict[str, str | None]:
    """Outcome of every leg of ONE coupon (None = not yet known), keyed by both the selection
    id and the odds id of the leg — for any coupon, also a version outside the statistics.
    (``selection_outcomes`` keeps one row per selection, so it cannot answer for a coupon.)"""
    out: dict[str, str | None] = {}
    for leg in store.rows(
        """SELECT l.odds_id, l.selection_id, s.key selection, m.market_key, m.period,
        m.overtime, m.line, e.id event_id FROM coupon_legs l
        JOIN selections s ON s.id=l.selection_id JOIN markets m ON m.id=s.market_id
        JOIN events e ON e.id=m.event_id WHERE l.coupon_id=?""",
        (coupon_id,),
    ):
        result = latest_result(store, leg["event_id"])
        outcome = resolve(leg, leg["selection"], result) if result else None
        out[leg["selection_id"]] = out[leg["odds_id"]] = outcome.value if outcome else None
    return out


def _selection_of(store: Store, odds_id: str) -> str | None:
    rows = store.rows("SELECT selection_id FROM odds WHERE id=?", (odds_id,))
    return rows[0]["selection_id"] if rows else None


def probability_bin(p: float) -> str:
    for low, high in PROBABILITY_BINS:
        if low <= p < high:
            return f"{low:.0%}–{min(high, 1.0):.0%}"
    return "< 50%"


def selection_summary(rows: list[dict], by: str) -> list[dict]:
    """Hit rate against the mean forecast, grouped by sport, league, market or range. A
    selection without a forecast (MÓJ KUPON on a market without a validated model) is not a
    calibration observation and is left out."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["outcome"] not in {"win", "loss"} or row.get("p_model") is None:
            continue
        key = probability_bin(row["p_model"]) if by == "range" else row[by]
        groups[key].append(row)
    out = []
    for key, items in sorted(groups.items()):
        hits = sum(r["outcome"] == "win" for r in items)
        low, high = wilson_interval(hits, len(items))
        mean_p = sum(r["p_model"] for r in items) / len(items)
        out.append(
            {
                "group": key,
                "n": len(items),
                "hits": hits,
                "hit_rate": hits / len(items),
                "ci95": (low, high),
                "mean_p_model": mean_p,
                "mean_p_conservative": _mean(r.get("p_conservative") for r in items),
                # the forecast is inconsistent with the observations when outside the interval
                "flag": "zbyt optymistyczna"
                if high < mean_p
                else "zbyt ostrożna"
                if low > mean_p
                else "zgodna z wynikami",
            }
        )
    return out


# ---------------------------------------------------------------- TASK 014: expected vs actual

TAIL = 0.05  # one tail of the "worse / better than the model" mark
VERDICTS = {
    "worse": "gorzej niż przewiduje model",
    "better": "lepiej niż przewiduje model",
    "consistent": "zgodne z modelem",
}
MARKET_GROUPS = {
    "double_chance": "Podwójna szansa",
    "1x2": "1X2",
    "btts": "Obie strzelą",
    "team_total_home": "Gole drużyny",
    "team_total_away": "Gole drużyny",
    "home_to_score": "Gole drużyny",
    "away_to_score": "Gole drużyny",
}
ALL = "Łącznie"


def market_group(market_key: str | None, selection: str | None) -> str:
    """The market of a leg in the expected-vs-actual table (goals split into over/under)."""
    if market_key == "total":
        return "Gole: powyżej" if selection == "over" else "Gole: poniżej"
    return MARKET_GROUPS.get(market_key or "", "Inne rynki")


def poisson_binomial(probabilities) -> list[float]:
    """Exact distribution of the number of successes of independent trials with the given
    chances: ``out[k]`` = P(exactly k). Dynamic programming, O(n²) — exact for any n."""
    out = [1.0]
    for p in probabilities:
        p = min(max(float(p), 0.0), 1.0)
        nxt = [0.0] * (len(out) + 1)
        for k, value in enumerate(out):
            nxt[k] += value * (1 - p)
            nxt[k + 1] += value * p
        out = nxt
    return out


def tails(probabilities, hits: float) -> tuple[float, float]:
    """(P(X ≤ hits), P(X ≥ hits)) of the Poisson-binomial X. A fractional ``hits`` (legs
    counted on matches: a match with two legs, one won, counts 0.5) is rounded towards the
    middle in both tails — P(X ≤ ⌈hits⌉) and P(X ≥ ⌊hits⌋) — so no mark comes from the
    rounding (conservative)."""
    dist = poisson_binomial(probabilities)
    low, high = math.ceil(hits - 1e-9), math.floor(hits + 1e-9)
    return (
        min(1.0, sum(dist[: max(0, low) + 1])),
        min(1.0, sum(dist[max(0, high) :])),
    )


def verdict(p_le: float, p_ge: float) -> str:
    """'worse' when a result this bad or worse has a chance below 5% under the model;
    'better' when a result this good or better has a chance below 5%, i.e. the result lies
    above the 95th percentile (P(X < actual) > 95%); otherwise 'consistent'."""
    if p_le < TAIL:
        return "worse"
    if p_ge < TAIL:
        return "better"
    return "consistent"


def _share(value: float) -> str:
    return "mniej niż 1%" if value < 0.01 else f"{value:.0%}"


def sentence(found: dict) -> str:
    """One sentence for people, e.g. 'zgodne z modelem (taki lub gorszy wynik zdarza się
    w 62% przypadków)'."""
    if not found.get("n"):
        return "brak danych (brak rozliczeń z szansą modelu)"
    worse_side = found["hits"] <= found["expected"]
    share = found["p_le"] if worse_side else found["p_ge"]
    how = "gorszy" if worse_side else "lepszy"
    only = "tylko " if found["verdict"] != "consistent" else ""
    return (
        f"{VERDICTS[found['verdict']]} (taki lub {how} wynik zdarza się {only}w "
        f"{_share(share)} przypadków)"
    )


def judge(probabilities: list[float], hits: float, conservative=None) -> dict:
    """Expected against actual hits of one group (``probabilities``: the model chance of
    every observation; ``conservative``: the cautious chances, None when one is unknown)."""
    n = len(probabilities)
    if not n:
        return {"n": 0, "hits": hits, "expected": None, "verdict": None, "sentence": sentence({})}
    p_le, p_ge = tails(probabilities, hits)
    found = {
        "n": n,
        "hits": hits,
        "expected": sum(probabilities),
        "expected_conservative": (
            sum(conservative) if conservative is not None and len(conservative) == n else None
        ),
        "p_le": p_le,
        "p_ge": p_ge,
        "verdict": verdict(p_le, p_ge),
    }
    found["verdict_label"] = VERDICTS[found["verdict"]]
    found["sentence"] = sentence(found)
    return found


def expected_vs_actual(store: Store, *, since: str | None = None) -> dict:
    """TASK 014 §2.1: settled coupons that count in the statistics (the official coupon of a
    profile and day and the user's coupons — no duplicates, no versions), per profile and
    together: expected hits from the model chance and from the cautious chance, actual hits
    and the chance of a result this bad or worse / this good or better (Poisson-binomial on
    the model chances). A coupon without a model chance is listed apart ("bez szansy
    modelu") and never enters the numbers. ``since`` (ISO): only coupons whose first match
    started then or later. Read only."""
    groups: dict[str, list[dict]] = defaultdict(list)
    labels: dict[str, str] = {}
    apart: dict[str, dict] = {}
    other = 0
    for coupon in builder_coupons(store):
        if since and (coupon["first_start_at"] or "") < since:
            continue
        if coupon["outcome"] not in {"win", "loss"}:
            other += coupon["outcome"] in SETTLED
            continue
        labels.setdefault(coupon["profile"], coupon["proposal"].get("label") or coupon["profile"])
        p = (coupon["proposal"].get("probability") or {}).get("calibrated")
        if p is None:
            entry = apart.setdefault(coupon["profile"], {"settled": 0, "wins": 0})
            entry["settled"] += 1
            entry["wins"] += coupon["outcome"] == "win"
            continue
        groups[coupon["profile"]].append(coupon)

    def row(label: str, items: list[dict]) -> dict:
        model = [c["proposal"]["probability"]["calibrated"] for c in items]
        cautious = [c["proposal"]["probability"].get("conservative") for c in items]
        hits = sum(c["outcome"] == "win" for c in items)
        known = None if any(v is None for v in cautious) else cautious
        return {"label": label, **judge(model, hits, known)}

    everything = [c for items in groups.values() for c in items]
    return {
        "rows": [row(labels[name], items) for name, items in groups.items()],
        "total": row(ALL, everything),
        "without_model": [
            {"label": labels[name], **entry} for name, entry in sorted(apart.items())
        ],
        "other_settlements": other,
        "since": since,
        "basis": "kupony liczone w statystykach (oficjalne kupony dnia i kupony użytkownika); "
        "rozkład Poissona-dwumianowy na szansach modelu, kupony traktowane jako niezależne",
    }


SHORT_VERDICTS = {
    "worse": "gorzej niż przewiduje model",
    "better": "lepiej niż przewiduje model",
    "consistent": "zgodnie z modelem",
}


def recent_line(store: Store, *, now, days: int = 30) -> str:
    """TASK 014 §2.3: one line for the Dashboard, e.g. 'Ostatnie 30 dni: 7 z 14 trafionych,
    model oczekiwał 6,9 — zgodnie z modelem'."""
    from datetime import UTC, timedelta

    since = (now - timedelta(days=days)).astimezone(UTC).isoformat()  # stored starts are UTC
    found = expected_vs_actual(store, since=since)
    total = found["total"]
    apart = sum(item["settled"] for item in found["without_model"])
    if not total["n"]:
        return f"Ostatnie {days} dni: brak rozliczonych kuponów z szansą modelu."
    expected = f"{total['expected']:.1f}".replace(".", ",")
    text = (
        f"Ostatnie {days} dni: {total['hits']} z {total['n']} trafionych, model oczekiwał "
        f"{expected} — {SHORT_VERDICTS[total['verdict']]}"
    )
    if apart:
        text += f" (bez szansy modelu: {apart}, poza obliczeniem)"
    return text + "."


def legs_expected_vs_actual(store: Store, rows: list[dict] | None = None) -> dict:
    """TASK 014 §2.2: the legs of the counted coupons (each selection once) per market and
    together, COUNTED ON MATCHES like TASK 011: the legs of one match in a group are one
    observation with their mean chance and mean outcome. A leg without a model chance is
    left out (only counted)."""
    rows = selection_outcomes(store) if rows is None else rows
    decided = [r for r in rows if r["outcome"] in {"win", "loss"}]
    usable = [r for r in decided if r.get("p_model") is not None]

    def group(label: str, items: list[dict]) -> dict:
        by_match: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            by_match[item.get("match") or item.get("event_id") or item["selection_id"]].append(item)
        means = [
            (
                sum(i["p_model"] for i in part) / len(part),
                sum(i["outcome"] == "win" for i in part) / len(part),
            )
            for part in by_match.values()
        ]
        found = judge([p for p, _ in means], sum(y for _, y in means))
        return {"label": label, "legs": len(items), "matches": len(means), **found}

    by_market: dict[str, list[dict]] = defaultdict(list)
    for item in usable:
        by_market[item.get("market_group") or "Inne rynki"].append(item)
    return {
        "rows": [group(label, items) for label, items in sorted(by_market.items())],
        "total": group(ALL, usable),
        "without_model": len(decided) - len(usable),
        "unit": "mecz (nogi jednego meczu w grupie = jedna obserwacja)",
    }


def shared_losses(store: Store) -> dict:
    """TASK 014 §4.1: on settled coupons — on how many days ONE lost leg was in two or more
    counted coupons (one lost selection loses all of them), and how many legs each lost
    coupon lost. A day is the Warsaw date of the lost leg's match."""
    from sports_edge.coupon_store import local_day

    outcomes = {r["selection_id"]: r for r in selection_outcomes(store)}
    coupons = [c for c in builder_coupons(store) if c["outcome"] in {"win", "loss"}]
    holders: dict[str, set[str]] = defaultdict(set)
    lost_legs: dict[str, int] = {}
    for coupon in coupons:
        lost = 0
        for leg in coupon["proposal"].get("legs", []):
            key = leg.get("selection_id") or _selection_of(store, leg["odds_id"])
            found = outcomes.get(key)
            if found is None:
                continue
            holders[key].add(coupon["id"])
            lost += found["outcome"] == "loss"
        if coupon["outcome"] == "loss":
            lost_legs[coupon["id"]] = lost
    events = [
        {"day": local_day(outcomes[key]["match_start"]), "coupons": len(ids)}
        for key, ids in holders.items()
        if len(ids) >= 2 and outcomes[key]["outcome"] == "loss" and outcomes[key].get("match_start")
    ]
    days = {local_day(c["first_start_at"]) for c in coupons if c.get("first_start_at")}
    return {
        "settled_days": len(days),
        "days_one_leg_2plus": len({e["day"] for e in events}),
        "lost_selections_2plus": len(events),
        "coupons_lost_by_shared_leg": sum(e["coupons"] for e in events),
        "lost_coupons": len(lost_legs),
        "lost_coupons_by_lost_legs": dict(sorted(Counter(lost_legs.values()).items())),
    }
