"""Model Performance Engine and Prediction Error Analysis (TASK 007 §P5).

Built only on SETTLED PROSPECTIVE forecasts (made before the match, settled from recorded
results): accuracy, log loss, Brier, ECE and calibration by probability range, league,
market and model version, with sample sizes and confidence intervals. Small samples are
marked as such — a pattern seen in 20 forecasts is a hypothesis, not a finding.
"""

import math
import random
from collections import defaultdict
from datetime import datetime

from sports_edge import collection, learning
from sports_edge.models.calibration import wilson_interval
from sports_edge.storage import Store

SMALL = 30  # fewer settled forecasts than this: "mała próba"
CORE = {"en.1", "de.1", "es.1", "it.1", "fr.1"}
BINS = [i / 10 for i in range(11)]


def items(store: Store) -> list[dict]:
    """One row per settled selection: served probability p, outcome y and its context."""
    out = []
    served = learning.served_check(store)
    for row in collection.settled_forecasts(store):
        if row["status"] != "settled":
            continue
        if not served(row["model_version_id"], row["predicted_at"]):
            continue  # a shadow forecast of a challenger: archived, never served
        competition = row["inputs"].get("competition")
        family = row["uncertainty"].get("family") or row["market_key"]
        groups = [
            (
                row["market_key"],
                row["line"],
                row["probabilities"],
                row["outcome"].get("selections") or {},
            )
        ]
        for key, value in (row["uncertainty"].get("derived") or {}).items():
            groups.append(
                (
                    key,
                    None,
                    value.get("calibrated") or {},
                    (row["outcome"].get("derived") or {}).get(key) or {},
                )
            )
        for market, line, probabilities, outcomes in groups:
            for selection, p in probabilities.items():
                y = outcomes.get(selection)
                if y is None or p is None:
                    continue
                out.append(
                    {
                        "event": row["prediction_id"],
                        "match": row["event_id"],  # independent unit: one match
                        "group": market_group(market),
                        "p": float(p),
                        "y": int(y),
                        "market": market,
                        "line": line,
                        "selection": selection,
                        "family": family if market == row["market_key"] else market,
                        "competition": competition,
                        "league": row["league"],
                        "scope": "core" if competition in CORE else "extended",
                        "version": row["model_version_id"],
                        "start_at": row["start_at"],
                        "predicted_at": row["predicted_at"],
                    }
                )
    return out


def _ece(rows: list[dict]) -> float | None:
    if not rows:
        return None
    total, err = len(rows), 0.0
    for lo, hi in zip(BINS, BINS[1:], strict=False):
        part = [r for r in rows if lo <= r["p"] < hi or (hi == 1.0 and r["p"] == 1.0)]
        if part:
            err += (
                len(part)
                / total
                * abs(sum(r["p"] for r in part) / len(part) - sum(r["y"] for r in part) / len(part))
            )
    return round(err, 4)


GROUPS = ("1X2", "Podwójna szansa", "Over/Under", "BTTS", "Gole drużyny")
ACCURACY = {
    "1X2": "trafność wskazanego wyniku (najwyższa z 3 szans)",
    "Podwójna szansa": "trafność najwyżej ocenionej z 3 opcji",
    "Over/Under": "trafność strony ≥ 50%",
    "BTTS": "trafność strony ≥ 50%",
    "Gole drużyny": "trafność strony ≥ 50%",
}


def market_group(market: str) -> str:
    if market == "1x2":
        return "1X2"
    if market == "double_chance":
        return "Podwójna szansa"
    if market == "total":
        return "Over/Under"
    if market == "btts":
        return "BTTS"
    return "Gole drużyny"


def by_market(rows: list[dict]) -> dict[str, dict]:
    """Metrics of each market group separately (never one mixed "accuracy"), plus draws.

    The number of independent matches is reported next to the number of forecasts: several
    markets of one match are not independent evidence."""
    out = {}
    for group in GROUPS:
        part = [r for r in rows if r["group"] == group]
        if part:
            out[group] = {**metrics(part), "accuracy_basis": ACCURACY[group]}
    draws = [r for r in rows if r["group"] == "1X2" and r["selection"] == "draw"]
    if draws:
        hits = sum(r["y"] for r in draws)
        low, high = wilson_interval(hits, len(draws))
        out["Remisy"] = {
            **metrics(draws),
            "predicted": round(sum(r["p"] for r in draws) / len(draws), 4),
            "observed": round(hits / len(draws), 4),
            "observed_ci95": [round(low, 4), round(high, 4)],
            "accuracy_basis": "średnia prognozowana szansa remisu wobec częstości remisów",
        }
    return out


def metrics(rows: list[dict], *, seed: int = 7, resamples: int = 300) -> dict:
    """Metrics of selections of ONE kind of market; the log loss interval resamples whole
    matches (all forecasts of a match together). "accuracy": the forecast's favourite side
    per prediction — meaningful within one market group only."""
    if not rows:
        return {"n": 0, "events": 0, "matches": 0}
    eps = 1e-6

    def loss(part):
        return sum(
            -math.log(max(eps, r["p"])) if r["y"] else -math.log(max(eps, 1 - r["p"])) for r in part
        ) / len(part)

    by_event: dict[str, list[dict]] = defaultdict(list)
    by_match: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_event[r["event"]].append(r)
        by_match[r.get("match") or r["event"]].append(r)
    groups = list(by_event.values())
    matches = list(by_match.values())
    rng = random.Random(seed)
    draws = sorted(
        loss([r for g in rng.choices(matches, k=len(matches)) for r in g]) for _ in range(resamples)
    )
    favourites = [max(g, key=lambda r: r["p"]) for g in groups if len(g) > 1]
    hits = sum(r["y"] for r in favourites)
    return {
        "n": len(rows),
        "events": len(groups),
        "matches": len(matches),
        "log_loss": round(loss(rows), 4),
        "log_loss_ci95": [
            round(draws[int(0.025 * resamples)], 4),
            round(draws[int(0.975 * resamples) - 1], 4),
        ],
        "brier": round(sum((r["p"] - r["y"]) ** 2 for r in rows) / len(rows), 4),
        "ece": _ece(rows),
        "accuracy": round(hits / len(favourites), 4) if favourites else None,
        "accuracy_ci95": [round(v, 4) for v in wilson_interval(hits, len(favourites))]
        if favourites
        else None,
        "small_sample": len(matches) < SMALL,
    }


def coupon_split(coupons: list[dict]) -> list[dict]:
    """Coupons by kind (SOLO / AKO) and by profile: hit rate with a Wilson interval, the
    average predicted probability and ROI — a whole coupon, never single selections."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for coupon in coupons:
        groups[f"rodzaj: {'SOLO' if coupon['kind'] == 'solo' else 'AKO'}"].append(coupon)
        label = (coupon.get("proposal") or {}).get("label") or coupon.get("profile")
        groups[f"profil: {label}"].append(coupon)
    out = []
    for name, part in sorted(groups.items()):
        decided = [c for c in part if c["outcome"] in {"win", "loss"}]
        wins = sum(c["outcome"] == "win" for c in decided)
        stake = sum(float(c["stake"]) for c in decided)
        profit = sum(float(c["net_profit"] or 0) for c in decided)
        predicted = [
            ((c.get("proposal") or {}).get("probability") or {}).get("calibrated") for c in decided
        ]
        predicted = [p for p in predicted if p is not None]
        ci = wilson_interval(wins, len(decided)) if decided else None
        out.append(
            {
                "grupa": name,
                "kupony": len(part),
                "rozliczone": len(decided),
                "wygrane": wins,
                "trafność": round(wins / len(decided), 4) if decided else None,
                "trafność_ci95": [round(v, 4) for v in ci] if ci else None,
                "prognoza_średnio": round(sum(predicted) / len(predicted), 4)
                if predicted
                else None,
                "roi": round(profit / stake, 4) if stake else None,
                "mała_próba": len(decided) < SMALL,
            }
        )
    return out


def calibration(rows: list[dict]) -> list[dict]:
    out = []
    for lo, hi in zip(BINS, BINS[1:], strict=False):
        part = [r for r in rows if lo <= r["p"] < hi or (hi == 1.0 and r["p"] == 1.0)]
        if not part:
            continue
        hits = sum(r["y"] for r in part)
        low, high = wilson_interval(hits, len(part))
        mean = sum(r["p"] for r in part) / len(part)
        out.append(
            {
                "range": f"{lo:.0%}–{hi:.0%}",
                "n": len(part),
                "mean_p": round(mean, 4),
                "freq": round(hits / len(part), 4),
                "ci95": [round(low, 4), round(high, 4)],
                "flag": "zbyt pewny" if high < mean else "zbyt ostrożny" if low > mean else "",
                "small_sample": len(part) < SMALL,
            }
        )
    return out


def by(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append(r)
    return {name: metrics(part) for name, part in sorted(groups.items())}


def stability(rows: list[dict]) -> dict[str, dict]:
    """Metrics per ISO week of the match (is the model drifting?)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        year, week, _ = datetime.fromisoformat(r["start_at"]).isocalendar()
        groups[f"{year}-T{week:02d}"].append(r)
    return {name: metrics(part, resamples=100) for name, part in sorted(groups.items())}


def _rate(part: list[dict]) -> tuple[float, float, int]:
    return (sum(r["p"] for r in part) / len(part), sum(r["y"] for r in part) / len(part), len(part))


def error_analysis(rows: list[dict], *, leg_leagues: list[str] | None = None) -> list[dict]:
    """Plain findings about where the forecasts err. Each says how big its sample is."""
    findings = []

    def add(topic, part, text):
        if part:
            p, f, n = _rate(part)
            findings.append(
                {
                    "topic": topic,
                    "n": n,
                    "predicted": round(p, 4),
                    "observed": round(f, 4),
                    "gap": round(f - p, 4),
                    "finding": text(p, f),
                    "small_sample": n < SMALL,
                }
            )

    def verdict(p, f):
        gap = f - p
        if abs(gap) < 0.03:
            return f"zgodnie z prognozą (prognoza {p:.0%}, wynik {f:.0%})"
        side = "częściej" if gap > 0 else "rzadziej"
        return f"zdarzało się {side}, niż prognozowano (prognoza {p:.0%}, wynik {f:.0%})"

    one_x_two = [r for r in rows if r["market"] == "1x2"]
    add("Przewaga gospodarzy", [r for r in one_x_two if r["selection"] == "home"], verdict)
    add("Remisy", [r for r in one_x_two if r["selection"] == "draw"], verdict)
    add("Faworyci (≥ 60%)", [r for r in rows if r["p"] >= 0.6], verdict)
    add("Przedział 80–90%", [r for r in rows if 0.8 <= r["p"] < 0.9], verdict)
    for line in ("1.5", "2.5"):
        add(
            f"Powyżej {line.replace('.', ',')} gola",
            [
                r
                for r in rows
                if r["market"] == "total" and r["line"] == line and r["selection"] == "over"
            ],
            verdict,
        )
    core = metrics([r for r in rows if r["scope"] == "core"])
    new = metrics([r for r in rows if r["scope"] == "extended"])
    if core["n"] and new["n"]:
        findings.append(
            {
                "topic": "Nowe ligi a 5 czołowych",
                "n": new["events"],
                "finding": f"log loss nowych lig {new['log_loss']} vs czołowych {core['log_loss']}"
                + (" — gorzej" if new["log_loss"] > core["log_loss"] else " — nie gorzej"),
                "small_sample": new["small_sample"] or core["small_sample"],
            }
        )
    leagues = by(rows, "competition")
    pooled = metrics(rows) if rows else {"log_loss": None}
    worst = [(k, v) for k, v in leagues.items() if v["events"] >= SMALL]
    if worst and pooled.get("log_loss") is not None:
        name, value = max(worst, key=lambda kv: kv[1]["log_loss"])
        findings.append(
            {
                "topic": "Najsłabsza liga",
                "n": value["events"],
                "finding": f"{name}: log loss {value['log_loss']} (całość {pooled['log_loss']})",
                "small_sample": False,
            }
        )
    small = [k for k, v in leagues.items() if v["events"] < SMALL]
    if small:
        findings.append(
            {
                "topic": "Małe próby",
                "n": len(small),
                "finding": "ligi z mniej niż 30 rozliczonymi prognozami: " + ", ".join(small),
                "small_sample": True,
            }
        )
    if leg_leagues:
        counts: dict[str, int] = defaultdict(int)
        for league in leg_leagues:
            counts[league] += 1
        top, n = max(counts.items(), key=lambda kv: kv[1])
        share = n / len(leg_leagues)
        findings.append(
            {
                "topic": "Koncentracja lig w kuponach",
                "n": len(leg_leagues),
                "finding": f"{top}: {share:.0%} selekcji kuponów"
                + (" — duża koncentracja ryzyka" if share > 0.5 else ""),
                "small_sample": len(leg_leagues) < SMALL,
            }
        )
    return findings


def coupon_review(coupon: dict) -> list[str]:
    """ANALIZA WYNIKU KUPONU: what happened, from the stored forecast and the result only —
    no invented causes (no data about injuries or tactics is claimed)."""
    proposal = coupon["proposal"]
    lines = []
    lost = [leg for leg in proposal["legs"] if leg.get("outcome") == "loss"]
    won = [leg for leg in proposal["legs"] if leg.get("outcome") == "win"]
    p = (proposal.get("probability") or {}).get("calibrated")
    if coupon.get("outcome") == "win":
        lines.append(f"Kupon trafiony: {len(won)} z {len(proposal['legs'])} selekcji.")
    elif coupon.get("outcome") == "loss":
        lines.append(
            f"Kupon nietrafiony: nie weszło {len(lost)} z {len(proposal['legs'])} selekcji."
        )
    for leg in lost:
        q = leg.get("p_model")
        size = (
            "mało prawdopodobny wynik według modelu"
            if q is not None and q >= 0.75
            else "wynik zgodny z ryzykiem pokazanym przez model"
            if q is not None
            else "selekcja bez wiarygodnego oszacowania"
        )
        shown = f"{q:.0%}".replace(".", ",") if q is not None else "brak"
        lines.append(f"{leg['event']}: prognoza {shown} — {size}.")
    if p is not None:
        lines.append(
            f"Szansa całego kuponu wynosiła {p:.0%} — "
            + (
                "przegrana mieściła się w ryzyku"
                if p < 0.8
                else "przegrana była mało prawdopodobna"
            )
            + ". Jeden kupon nie świadczy o jakości modelu; oceniamy setki rozliczonych prognoz."
        )
    lines.append(forecast_source(proposal["legs"]))
    lines.append("Aplikacja nie zna przyczyn (składy, kontuzje, sędziowanie) — nie są zgadywane.")
    return lines


def forecast_source(legs: list[dict]) -> str:
    """TASK 013 §4.2: which forecast the leg chances of a coupon review come from — the
    forecast stored with every leg, or (legs saved before the id was stored) the chance
    written in the saved proposal, marked as a fallback."""
    with_id = sum(bool(leg.get("prediction_id")) for leg in legs)
    if legs and with_id == len(legs):
        return "Źródło szans: prognoza zapisana przy każdej nodze kuponu."
    if with_id:
        return (
            f"Źródło szans: prognoza nogi dla {with_id} z {len(legs)} selekcji; pozostałe to "
            "FALLBACK — szansa z zapisu propozycji (stara noga bez identyfikatora prognozy)."
        )
    return (
        "Źródło szans: FALLBACK — szansa z zapisu propozycji (stare nogi bez identyfikatora "
        "prognozy)."
    )
