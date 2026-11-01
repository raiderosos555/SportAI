"""Coverage of the STS offer (TASK 006): how much of it the models can really analyse.

Events, markets and selections are counted separately and never mixed. Every event gets
exactly one reason why it has no forecast (the first failed condition, in a fixed order),
so the funnel adds up: offer → before start → sport with a model → league with independent
results → league with a model → matched team names → current results → valid forecast.
Match statistics are reported as information only: no model uses them yet.
"""

import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime

from sports_edge.analysis.coupon_builder import eligibility
from sports_edge.matching import alias_map, match_participant
from sports_edge.results_engine import (
    SOURCE_LABELS,
    effective_results,
    known_names,
    league_freshness,
)
from sports_edge.services import (
    RESEARCH_COMPETITIONS,
    analyse_day,
    league_code,
    normalizer,
    probability_policy,
    team_aliases,
    team_identities,
)
from sports_edge.settings import AppPaths, AppSettings
from sports_edge.storage import Store

STALE_REASON = "kurs niepotwierdzony (stara migawka)"
FUNNEL = (
    ("events", "Wydarzenia w ofercie STS"),
    ("before_start", "Przed rozpoczęciem"),
    ("modelled_sport", "Dyscyplina z modelem (piłka nożna)"),
    ("results_league", "Liga z niezależnymi wynikami"),
    ("modelled_league", "Liga z modelem prognoz"),
    ("matched", "Dopasowane nazwy obu drużyn"),
    ("fresh_results", "Aktualne wyniki ligi (≤ 21 dni)"),
    ("forecast", "Ważna prognoza przedmeczowa"),
)
REASONS = {
    "before_start": "mecz rozpoczęty, zakończony lub bez godziny",
    "modelled_sport": "dyscyplina bez modelu",
    "results_league": "liga bez legalnego źródła wyników",
    "modelled_league": "liga ma wyniki, ale za krótką historię na model",
    "matched": "nazwy drużyn niedopasowane do danych",
    "fresh_results": "nieaktualne wyniki ligi",
    "forecast": "brak prognozy (np. za mało meczów drużyny)",
}
MODELLED_SPORTS = {"football"}


def coverage_report(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    days: list[str],
    *,
    now: datetime,
    data_kinds: tuple[str, ...] = ("prospective", "archive"),
    modelled: tuple[str, ...] = RESEARCH_COMPETITIONS,
) -> dict:
    names = normalizer(paths)
    aliases = alias_map(store, team_aliases(paths))
    identities = team_identities(paths)
    max_odds = float(probability_policy(paths).builder["max_odds_per_leg"])
    known_by_code: dict[str, dict] = {}
    freshness = {}
    events: list[dict] = []
    for day in days:
        _report, candidates = analyse_day(
            store, paths, settings, day, now=now, data_kinds=data_kinds
        )
        by_event: dict[str, list] = defaultdict(list)
        for candidate in candidates:
            by_event[candidate.event_id].append(candidate)
        for event_id, rows in by_event.items():
            head = rows[0]
            code = league_code(head.league, head.sport)
            if code and code not in freshness:
                freshness[code] = league_freshness(store, (code,), now=now)[0]
            if code and code not in known_by_code:
                known_by_code[code] = known_names(
                    effective_results(
                        store,
                        sport=head.sport,
                        until=now,
                        competitions=(code,),
                        identities=identities,
                    ),
                    head.sport,
                    names,
                )
            event = store.get("events", event_id)
            sides = [event["home"] or "", event["away"] or ""]
            matched = bool(code) and all(
                match_participant(side, head.sport, known_by_code[code], names, aliases).status
                in {"exact", "alias"}
                for side in sides
            )
            checks = {
                "before_start": head.event_status == "before_start",
                "modelled_sport": head.sport in MODELLED_SPORTS,
                "results_league": code is not None,
                "modelled_league": code in modelled,
                "matched": matched,
                "fresh_results": bool(code and freshness[code]["fresh"]),
                "forecast": any(c.p_model is not None for c in rows),
            }
            reason = next((REASONS[k] for k, ok in checks.items() if not ok), None)
            if checks["forecast"]:
                reason = None  # a forecast that exists is never explained away
            modelled_rows = [c for c in rows if c.p_model is not None]
            why = [eligibility(c, max_odds) for c in modelled_rows]
            stale_only = sum(
                1
                for c, w in zip(modelled_rows, why, strict=True)
                if w == STALE_REASON
                and eligibility(replace(c, freshness="fresh"), max_odds) is None
            )
            events.append(
                {
                    "event_id": event_id,
                    "event": head.event,
                    "day": day,
                    "start_at": head.start_at,
                    "sport": head.sport,
                    "league": head.league,
                    "league_key": f"{head.league} ({head.sport})",
                    "competition": code,
                    "source": ", ".join(
                        SOURCE_LABELS.get(p, p)
                        for p in (freshness[code]["providers"] if code else {})
                    )
                    or "brak",
                    "model": _model_label(modelled_rows),
                    "checks": checks,
                    "reason": reason,
                    "markets": len({c.market_id for c in rows}),
                    "selections": len(rows),
                    "with_model": len(modelled_rows),
                    "with_conservative": sum(c.p_low is not None for c in modelled_rows),
                    "eligible": sum(w is None for w in why),
                    "stale_only": stale_only,
                    "blocked": dict(Counter(w for w in why if w)),
                }
            )
    return summarise(events)


def _model_label(rows) -> str:
    families = sorted({_family(c.model or "") for c in rows if c.model})
    return " + ".join(families) or "brak"


def _family(model: str) -> str:
    if model.startswith("elo"):
        return "Elo"
    if "dixon" in model or model.startswith("dc"):
        return "Dixon–Coles"
    return model


def summarise(events: list[dict]) -> dict:
    funnel = []
    remaining = list(events)
    funnel.append({"key": "events", "label": FUNNEL[0][1], "events": len(remaining)})
    for key, label in FUNNEL[1:]:
        remaining = [e for e in remaining if e["checks"][key]]
        funnel.append({"key": key, "label": label, "events": len(remaining)})
    totals = {
        key: sum(e[key] for e in events)
        for key in (
            "markets",
            "selections",
            "with_model",
            "with_conservative",
            "eligible",
            "stale_only",
        )
    }
    totals["events_with_forecast"] = sum(e["checks"]["forecast"] for e in events)
    return {
        "events": events,
        "funnel": funnel,
        "totals": totals,
        "by_sport": _group(events, "sport"),
        # one STS league header can exist in several sports (e.g. "WŁOCHY - SERIE A")
        "by_league": _group(events, "league_key", extra=("league", "competition", "sport")),
        "by_date": _group(events, "day"),
        "by_source": _group(events, "source"),
        "by_model": _group(events, "model"),
        "by_reason": _reasons(events),
    }


def _group(events: list[dict], key: str, extra: tuple[str, ...] = ()) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        groups[event[key]].append(event)
    out = []
    for value, items in groups.items():
        row = {
            "group": value,
            "events": len(items),
            "before_start": sum(e["checks"]["before_start"] for e in items),
            "with_forecast": sum(e["checks"]["forecast"] for e in items),
            "selections": sum(e["selections"] for e in items),
            "with_model": sum(e["with_model"] for e in items),
            "eligible": sum(e["eligible"] for e in items),
            "stale_only": sum(e["stale_only"] for e in items),
            "main_reason": Counter(e["reason"] for e in items if e["reason"]).most_common(1)[0][0]
            if any(e["reason"] for e in items)
            else "—",
        }
        for field in extra:
            row[field] = items[0][field]
        out.append(row)
    return sorted(out, key=lambda r: (-r["events"], str(r["group"])))


def _reasons(events: list[dict]) -> list[dict]:
    counts = Counter(e["reason"] for e in events if e["reason"])
    return [{"reason": reason, "events": n} for reason, n in counts.most_common()]


def to_json(report: dict) -> str:
    """Compact JSON of a coverage report (without per-event rows) for analyses and docs."""
    return json.dumps(
        {k: v for k, v in report.items() if k != "events"}, ensure_ascii=False, default=str
    )
