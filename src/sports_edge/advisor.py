"""AI Coupon Advisor (TASK 007): "OCEŃ MÓJ KUPON AI".

"AI" means the application's own models (Elo, Dixon–Coles) and its validation rules. Every
statement comes from stored data: model probabilities, conservative bounds, validation
status, price freshness and data quality. Nothing is estimated from the odds alone — a
market without a validated model says so. Alternatives are only proposed; the user applies
them (never automatically).
"""

from datetime import datetime
from decimal import Decimal

from sports_edge import insights as ins
from sports_edge import my_coupon as mine
from sports_edge.analysis.coupon_builder import VALIDATED
from sports_edge.analysis.edge_engine import Candidate
from sports_edge.settlement import CostProfile

WIDE_INTERVAL = 0.08  # P − P_low above this: wide uncertainty (as in insights.reliability)
DISAGREEMENT = 0.10  # model vs de-margined STS price, in probability points


def supported(c: Candidate | None) -> bool:
    return c is not None and c.p_model is not None and c.probability_ok and c.p_low is not None


def leg_report(leg: dict, c: Candidate | None, *, now: datetime) -> dict:
    """Plain evaluation of one selection of the coupon."""
    base = {
        "event": leg["event"],
        "pick": leg["label"],
        "market": leg.get("market", ""),
        "selection_id": leg["selection_id"],
        "odds": c.odds if c is not None else Decimal(leg["added_odds"]),
    }
    if c is None:
        return {
            **base,
            "supported": False,
            "status": "brak w ofercie",
            "reliability": "insufficient",
            "risks": ["selekcji nie ma w bieżącej ofercie STS"],
            "reason": "Selekcja zniknęła z oferty — nie można jej ocenić",
        }
    risks = []
    if c.freshness != "fresh":
        risks.append("kurs niepotwierdzony w świeżym dokumencie STS")
    if c.event_status != "before_start":
        risks.append("wydarzenie nie jest przed rozpoczęciem")
    if c.data_status != "ok":
        risks.append(f"jakość odczytu oferty: {c.data_status}")
    if c.rules_status != "zweryfikowane":
        risks.append("zasady rozliczenia rynku niezweryfikowane")
    if c.status_note:
        risks.append("status modelu zawieszony: holdout był wcześniej użyty do decyzji")
    level, reasons = ins.reliability(c, now=now)
    if not supported(c):
        return {
            **base,
            "supported": False,
            "p_model": None,
            "p_low": None,
            "status": "brak modelu",
            "reliability": "insufficient",
            "data_quality": c.data_status,
            "freshness": c.freshness,
            "risks": risks or ["brak prognozy modelu dla tego rynku"],
            "reason": mine.NO_MODEL,
        }
    if c.p_model - c.p_low > WIDE_INTERVAL:
        risks.append(f"szeroki przedział niepewności (do {ins.pct(c.p_model - c.p_low)} w dół)")
    if c.predictive_status != VALIDATED:
        risks.append("model tego rynku bez walidacji predykcyjnej — wynik eksperymentalny")
    if c.p_market is not None and abs(c.p_model - c.p_market) > DISAGREEMENT:
        risks.append(
            f"model ({ins.pct(c.p_model)}) i kurs STS bez marży ({ins.pct(c.p_market)}) "
            "wyraźnie się różnią — możliwa informacja, której model nie zna (np. skład)"
        )
    for reason in reasons:
        if "mecz za kilka dni" in reason or "składy" in reason:
            risks.append(reason)
    return {
        **base,
        "supported": True,
        "p_model": c.p_model,
        "p_low": c.p_low,
        "p_high": c.p_high,
        "status": "zwalidowany" if c.predictive_status == VALIDATED else "eksperymentalny",
        "calibration": c.calibration,
        "model": c.model,
        "reliability": level,
        "data_quality": c.data_status,
        "freshness": c.freshness,
        "risks": risks,
        "reason": (
            f"Model {c.model} szacuje {ins.pct(c.p_model)} (ostrożnie {ins.pct(c.p_low)}). "
            f"{ins.sentence(c.calibration)}."
        ),
    }


def weakest(reports: list[dict]) -> int | None:
    """Index of the weakest link: a leg without a reliable estimate first, then the lowest
    conservative probability (ties: more risks)."""
    if not reports:
        return None
    missing = [i for i, r in enumerate(reports) if not r["supported"]]
    if missing:
        return missing[0]
    return min(range(len(reports)), key=lambda i: (reports[i]["p_low"], -len(reports[i]["risks"])))


def evaluate(
    cart: list[dict],
    rows: list[Candidate | None],
    *,
    stake: Decimal,
    cost: CostProfile,
    on_date,
    now: datetime,
) -> dict:
    summary = mine.summary(cart, rows, stake=stake, cost=cost, on_date=on_date)
    reports = [leg_report(leg, row, now=now) for leg, row in zip(cart, rows, strict=True)]
    weak = weakest(reports)
    uncertainties = []
    if summary["probability_note"]:
        uncertainties.append(summary["probability_note"])
    for issue in summary["issues"]:
        if issue.severity == "block" and issue.code not in {"same_event"}:
            uncertainties.append(issue.message)
    for r in reports:
        uncertainties += [f"{r['event']}: {risk}" for risk in r["risks"][:2]]
    why_weak = None
    if weak is not None:
        r = reports[weak]
        why_weak = (
            f"Brak wiarygodnego oszacowania ({r['reason']})"
            if not r["supported"]
            else f"Najniższa ostrożna szansa na kuponie: {ins.pct(r['p_low'])}"
            + (f"; {r['risks'][0]}" if r["risks"] else "")
        )
    return {
        "summary": summary,
        "legs": reports,
        "weakest": weak,
        "weakest_reason": why_weak,
        "uncertainties": uncertainties[:6],
        "verdict": verdict(summary, reports),
    }


def verdict(summary: dict, reports: list[dict]) -> str:
    if not reports:
        return "Kupon jest pusty."
    if summary["probability"] is None:
        return (
            "Nie można wiarygodnie oszacować szansy całego kuponu: "
            + ins.sentence(summary["probability_note"] or "brakuje danych")
            + "."
        )
    p, low = summary["probability"], summary["probability_low"]
    level = (
        "zwalidowany" if summary["validated"] else "eksperymentalny (część rynków bez walidacji)"
    )
    text = f"Szacowana szansa trafienia całego kuponu: {ins.pct(p)}"
    if low is not None:
        text += f" (ostrożnie {ins.pct(low)})"
    text += f". Status: {level}."
    if summary.get("break_even") is not None:
        text += (
            f" Aby wyjść na zero przy tym kursie, podobne kupony musiałyby trafiać w "
            f"{ins.pct(summary['break_even'])} przypadków."
        )
    return text


def alternatives(
    cart: list[dict],
    rows: list[Candidate | None],
    pool: list[Candidate],
    *,
    stake: Decimal,
    cost: CostProfile,
    on_date,
    now: datetime,
    target_odds: Decimal | None = None,
) -> list[dict]:
    """ZAPROPONUJ ALTERNATYWĘ: proposals the user may apply (never applied automatically)."""
    out: list[dict] = []
    present = [(leg, row) for leg, row in zip(cart, rows, strict=True) if row is not None]
    reports = [leg_report(leg, row, now=now) for leg, row in present]

    def option(title: str, why: str, picks: list[Candidate]) -> None:
        if not picks:
            return
        legs = [mine.leg_from(c, day=(c.start_at or "")[:10], now=now) for c in picks]
        found = mine.summary(legs, picks, stake=stake, cost=cost, on_date=on_date)
        out.append({"title": title, "why": why, "legs": legs, "rows": picks, "summary": found})

    weak = weakest(reports)
    if weak is not None and len(present) > 1:
        rest = [row for i, (_leg, row) in enumerate(present) if i != weak]
        option(
            "Usuń najsłabsze ogniwo",
            f"Bez: {present[weak][0]['event']} ({present[weak][0]['label']})",
            rest,
        )
    strong = sorted(
        (row for _leg, row in present if supported(row)), key=lambda c: c.p_low, reverse=True
    )
    if len(present) > 2 and len(strong) >= 2:
        option("Mniej selekcji", "Dwie selekcje z najwyższą ostrożną szansą", strong[:2])
    if weak is not None:
        event = present[weak][1].event_id
        better = [
            c
            for c in pool
            if c.event_id == event
            and supported(c)
            and c.selection_id != present[weak][1].selection_id
            and c.freshness == "fresh"
            and (not supported(present[weak][1]) or c.p_low > present[weak][1].p_low)
        ]
        better.sort(key=lambda c: (c.predictive_status == VALIDATED, c.p_low), reverse=True)
        if better:
            swap = [row for _leg, row in present]
            swap[weak] = better[0]
            option(
                "Lepiej zamodelowany rynek",
                f"{better[0].event}: {ins.candidate_pick(better[0])} zamiast "
                f"{present[weak][0]['label']}",
                swap,
            )
    if strong:
        option("SOLO", "Tylko najpewniejsza selekcja kuponu", strong[:1])
    if target_odds is not None and strong:
        best = None
        for c in strong:
            gap = abs(c.odds - target_odds)
            if best is None or gap < best[0]:
                best = (gap, c)
        if best is not None:
            option(
                "Bliżej kursu docelowego",
                f"Selekcja kuponu z kursem najbliższym {target_odds}",
                [best[1]],
            )
    return out
