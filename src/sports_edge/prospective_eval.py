"""Prospective reliability, STS market benchmark and challenger evidence (TASK 011).

Everything here is computed from SETTLED PROSPECTIVE forecasts — saved before the match and
settled from recorded results — and changes nothing: no forecast, threshold, model status or
coupon. The only write is ``record_market_benchmarks`` (new append-only rows).

THE UNIT IS A MATCH. Markets of one match are one dependent observation ("home team scores"
and "home team over 0.5 goals" are the same event), so every sample size below counts
matches, not selections:

* RELIABILITY (coupon zone, chance >= 60%): selections are grouped by role (served version
  or challenger in shadow mode), scope of leagues, model family, market with its line and a
  10-point range of the forecast. Inside a group each match contributes ONE pair: the mean
  forecast and the mean outcome of its selections in that range. ``n`` is the number of
  matches; the 95% Wilson interval uses the matches as trials and the sum of the per-match
  means as successes. When the selections of a match are perfectly correlated the per-match
  mean is 0 or 1 and the interval is exact; when they are less correlated the true variance
  is smaller, so the interval is conservative (wider). A range with at least ``min_matches``
  matches whose upper bound is below the mean forecast is "zbyt optymistyczna" (the reverse
  "zbyt ostrożna"); fewer matches: "za mało meczów". A flag is information only.
* STS MARKET BENCHMARK: for a settled forecast the STS price of the same match and market is
  taken from the LAST STS document generated before the kick-off (``quote_sets.valid_at`` —
  the time printed in the document — strictly before ``events.start_at``; only rows with
  status ``ok``; a document from after the start is never used). The margin is removed with
  the POWER method (p_i = (1/o_i)^k, k such that the sum is 1): unlike the proportional
  method it does not hand the favourites a proportional share of the margin, which would
  make the market look worse exactly in the coupon zone (favourite–longshot bias), so it is
  the stricter benchmark for the model; the proportional result is kept as a sensitivity
  check. Log loss and Brier of the model and of the market on the SAME forecasts, the
  difference per match and its 95% bootstrap interval over matches. No common match is
  "nieznane", fewer than ``min_matches`` matches "za mało" — never a default "false".
  The price is a benchmark ex post only and never a model feature (leakage_prevention.md).
* CHAMPION vs CHALLENGER: the same comparison between two versions on common matches.

Double chance follows from the 1X2 forecast of the same version, so pooled log loss
comparisons ("all markets", champion vs challenger) count it once, through 1X2.
"""

import json
import math
import random
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sports_edge import collection, learning
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.models.calibration import wilson_interval
from sports_edge.storage import Store

POLICY_FILE = "prospective_policy.toml"
ELO = "elo_ordered_draw"
GOALS = "dixon_coles_goals"
FAMILY_LABELS = {ELO: "Elo", GOALS: "Dixon–Coles"}
ROLE_LABELS = {"active": "aktywna wersja", "shadow": "challenger (tryb cienia)"}
SCOPE_LABELS = {"all": "wszystkie ligi", "core": "5 czołowych lig", "extended": "7 lig"}
MARKET_LABELS = {
    "1x2": "1X2",
    "double_chance": "Podwójna szansa",
    "total": "Gole",
    "btts": "Obie strzelą",
    "home_to_score": "Gospodarz strzeli",
    "away_to_score": "Gość strzeli",
    "team_total_home": "Gole gospodarza",
    "team_total_away": "Gole gościa",
}
POOLED = "*"  # all markets of a family together (still one observation per match)
# Markets whose selections can enter a coupon (they have a calibrated interval, i.e. a
# conservative estimate). Other markets are evaluated too, but shown apart.
COUPON_MARKETS = {
    ELO: ("1x2", "double_chance"),
    GOALS: ("1x2", "double_chance", "total 1.5", "total 2.5", "total 3.5", "btts"),
}
FLAG_LABELS = {
    "optimistic": "zbyt optymistyczna",
    "cautious": "zbyt ostrożna",
    "ok": "zgodna z prognozą",
    "insufficient": "za mało meczów",
}
STATES = ("unknown", "insufficient", "model_better", "model_worse", "no_difference")
EPS = 1e-6
DC_PARTS = {
    "home_draw": ("home", "draw"),
    "draw_away": ("draw", "away"),
    "home_away": ("home", "away"),
}


@dataclass(frozen=True)
class EvalPolicy:
    version: str = "2026-11-06.e2"
    zone: float = 0.60
    bins: tuple[float, ...] = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
    min_matches: int = 30
    z: float = 1.96
    market_min_matches: int = 300
    devig: str = "power"
    resamples: int = 1000
    seed: int = 7
    decision_matches: int = 300
    expiry_days: int = 14
    age_groups: tuple[float, ...] = (3.0, 12.0)  # TASK 012: hours before the kick-off


def load_policy(config: Path | None = None) -> EvalPolicy:
    """The policy file of the project (``config/prospective_policy.toml``); the defaults
    above when it is missing (a home created before TASK 011)."""
    if config is None:
        from sports_edge.settings import AppPaths, project_root

        config = AppPaths(project_root()).config
    path = Path(config) / POLICY_FILE
    if not path.is_file():
        return EvalPolicy()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cal, market, chal = data["calibration"], data["market"], data["challengers"]
    return EvalPolicy(
        version=data["policy"]["version"],
        zone=float(cal["zone"]),
        bins=tuple(float(b) for b in cal["bins"]),
        min_matches=int(cal["min_matches"]),
        z=float(cal["z"]),
        market_min_matches=int(market["min_matches"]),
        devig=str(market["devig"]),
        resamples=int(market["resamples"]),
        seed=int(market["seed"]),
        decision_matches=int(chal["decision_matches"]),
        expiry_days=int(chal["expiry_days"]),
        age_groups=tuple(float(x) for x in market.get("age_groups", (3, 12))),
    )


# ---------------------------------------------------------------- forecasts


def scope_of(competition: str | None) -> str | None:
    from sports_edge.services import MODEL_SCOPES

    return next((name for name, codes in MODEL_SCOPES.items() if competition in codes), None)


def market_name(market_key: str, line: str | None) -> str:
    return f"{market_key} {line}" if line else market_key


def market_label(market: str) -> str:
    if market == POOLED:
        return "Wszystkie rynki razem"
    key, _, line = market.partition(" ")
    label = MARKET_LABELS.get(key, key)
    return f"{label} {line.replace('.', ',')}" if line else label


def _families(store: Store) -> dict[str, str]:
    return {
        r["id"]: r["family"]
        for r in store.rows(
            "SELECT v.id, m.family FROM model_versions v JOIN models m ON m.id=v.model_id"
        )
    }


def _elo_double_chance(probabilities: dict, outcomes: dict) -> tuple[dict, dict] | None:
    if not all(k in probabilities for k in ("home", "draw", "away")):
        return None
    known = all(outcomes.get(k) is not None for k in ("home", "draw", "away"))
    return (
        {sel: probabilities[a] + probabilities[b] for sel, (a, b) in DC_PARTS.items()},
        {
            sel: (int(bool(outcomes[a] or outcomes[b])) if known else None)
            for sel, (a, b) in DC_PARTS.items()
        },
    )


def market_forecasts(store: Store) -> list[dict]:
    """One row per settled prospective MARKET forecast of a version: the latest forecast
    made before the start of each (version, match, market, line). Double chance is a market
    of its own (shipped by Dixon–Coles, derived from the same 1X2 for Elo).

    ``role``: "active" — the version was served when the forecast was made; "shadow" — a
    challenger that was not served (shadow mode). Versions with neither role (e.g. a
    research experiment spanning several scopes) are left out."""
    served = learning.served_check(store)
    challengers = {
        e["model_version_id"] for e in learning.events(store) if e["kind"] == "challenger_ready"
    }
    families = _families(store)
    latest: dict[tuple, dict] = {}
    for row in collection.settled_forecasts(store):
        if row["status"] != "settled":
            continue
        predicted = datetime.fromisoformat(row["predicted_at"])
        if row["start_at"] is None or predicted >= datetime.fromisoformat(row["start_at"]):
            continue
        version = row["model_version_id"]
        role = (
            "active" if served(version, predicted) else "shadow" if version in challengers else None
        )
        if role is None:
            continue
        family = families.get(version)
        competition = row["inputs"].get("competition")
        outcomes = row["outcome"].get("selections") or {}
        groups = [(row["market_key"], row["line"], row["probabilities"], outcomes)]
        for key, value in (row["uncertainty"].get("derived") or {}).items():
            groups.append(
                (
                    key,
                    None,
                    value.get("calibrated") or {},
                    (row["outcome"].get("derived") or {}).get(key) or {},
                )
            )
        if row["market_key"] == "1x2" and "double_chance" not in {g[0] for g in groups}:
            found = _elo_double_chance(row["probabilities"], outcomes)
            if found:
                groups.append(("double_chance", None, *found))
        for market_key, line, probabilities, result in groups:
            key = (version, row["event_id"], market_key, line)
            current = latest.get(key)
            if current is not None and current["predicted_at"] >= row["predicted_at"]:
                continue
            latest[key] = {
                "prediction_id": row["prediction_id"],
                "version": version,
                "role": role,
                "family": family,
                "match": row["event_id"],
                "competition": competition,
                "scope": scope_of(competition),
                "league": row["league"],
                "market_key": market_key,
                "line": line,
                "market": market_name(market_key, line),
                "probabilities": {k: float(v) for k, v in probabilities.items() if v is not None},
                "outcomes": dict(result),
                "predicted_at": row["predicted_at"],
                "start_at": row["start_at"],
            }
    return list(latest.values())


def _role_forecasts(rows: list[dict], role: str) -> list[dict]:
    """Forecasts of one role, one per (family, match, market): the newest version wins."""
    best: dict[tuple, dict] = {}
    for row in rows:
        if row["role"] != role:
            continue
        key = (row["family"], row["match"], row["market"])
        if key not in best or best[key]["predicted_at"] < row["predicted_at"]:
            best[key] = row
    return list(best.values())


# ---------------------------------------------------------------- reliability


def _bin(p: float, bins: tuple[float, ...]) -> tuple[float, float] | None:
    for lo, hi in zip(bins, bins[1:], strict=False):
        if lo <= p < hi or (hi == bins[-1] and p == hi):
            return lo, hi
    return None


def _range_label(lo: float, hi: float) -> str:
    return f"{lo * 100:.0f}–{hi * 100:.0f}%"


def selections(rows: list[dict], policy: EvalPolicy) -> list[dict]:
    """Settled selections with a forecast in the reported ranges (>= the first bin)."""
    out = []
    for row in rows:
        for sel, p in row["probabilities"].items():
            y = row["outcomes"].get(sel)
            if y is None or p < policy.bins[0]:
                continue
            out.append(
                {
                    **{k: row[k] for k in ("role", "family", "scope", "market", "match")},
                    "selection": sel,
                    "p": p,
                    "y": int(y),
                }
            )
    return out


def _cell(part: list[dict], policy: EvalPolicy) -> dict:
    """Match-level reliability of a group of selections (see the module docstring)."""
    by_match: dict[str, list[dict]] = defaultdict(list)
    for item in part:
        by_match[item["match"]].append(item)
    means = [
        (sum(i["p"] for i in items) / len(items), sum(i["y"] for i in items) / len(items))
        for items in by_match.values()
    ]
    n = len(means)
    mean_p = sum(p for p, _ in means) / n
    freq = sum(y for _, y in means) / n
    low, high = wilson_interval(sum(y for _, y in means), n, z=policy.z)
    if n < policy.min_matches:
        flag = "insufficient"
    elif high < mean_p:
        flag = "optimistic"
    elif low > mean_p:
        flag = "cautious"
    else:
        flag = "ok"
    return {
        "matches": n,
        "selections": len(part),
        "mean_p": round(mean_p, 4),
        "freq": round(freq, 4),
        "ci95": [round(low, 4), round(high, 4)],
        "gap": round(freq - mean_p, 4),
        "flag": flag,
        "flag_label": FLAG_LABELS[flag],
    }


def reliability(items: list[dict], policy: EvalPolicy) -> list[dict]:
    """Rows per (role, scope, family, market, range) plus the whole zone (>= ``zone``) of
    every group; ``scope`` "all" pools both scopes and ``market`` "*" all markets."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for item in items:
        for scope in ("all", item["scope"]):
            for market in (item["market"], POOLED):
                groups[(item["role"], scope, item["family"], market)].append(item)
    out = []
    for (role, scope, family, market), part in sorted(groups.items(), key=lambda kv: kv[0]):
        if scope is None:
            continue
        ranges: dict[tuple, list[dict]] = defaultdict(list)
        for item in part:
            found = _bin(item["p"], policy.bins)
            if found:
                ranges[found].append(item)
        cells = [(lo, hi, _range_label(lo, hi), ranges[(lo, hi)]) for lo, hi in sorted(ranges)]
        zone = [i for i in part if i["p"] >= policy.zone]
        if zone:
            cells.append((policy.zone, 1.0, f"strefa kuponów ≥ {policy.zone * 100:.0f}%", zone))
        for lo, hi, label, cell in cells:
            out.append(
                {
                    "role": role,
                    "scope": scope,
                    "family": family,
                    "market": market,
                    "range": label,
                    "low": lo,
                    "high": hi,
                    "zone_total": label.startswith("strefa"),
                    "in_zone": lo >= policy.zone,
                    "coupon_market": market == POOLED or market in COUPON_MARKETS.get(family, ()),
                    **_cell(cell, policy),
                }
            )
    return out


def calibration_report(
    store: Store,
    *,
    policy: EvalPolicy | None = None,
    scope: str | None = None,
    min_prob: float | None = None,
    rows: list[dict] | None = None,
) -> dict:
    """Reliability of the coupon zone (CLI ``sports-edge calibration`` and the panels)."""
    policy = policy or load_policy()
    rows = market_forecasts(store) if rows is None else rows
    items = []
    for role in ("active", "shadow"):
        items += selections(_role_forecasts(rows, role), policy)
    table = reliability(items, policy)
    if scope:
        table = [r for r in table if r["scope"] == scope]
    if min_prob is not None:
        table = [r for r in table if r["low"] >= min_prob - 1e-9 or r["zone_total"]]
    flagged = [r for r in table if r["flag"] in ("optimistic", "cautious") and not r["zone_total"]]
    return {
        "policy_version": policy.version,
        "unit": "mecz (rynki jednego meczu = jedna obserwacja zależna)",
        "interval": f"Wilson {policy.z} na meczach: sukcesy = suma średnich wyników meczów",
        "min_matches": policy.min_matches,
        "zone": policy.zone,
        "matches": len({r["match"] for r in rows}),
        "forecasts": len(rows),
        "rows": table,
        "flags": flagged,
    }


def leg_flag(
    table: list[dict],
    *,
    family: str | None,
    market_key: str | None,
    line: str | None,
    scope: str | None,
    p: float | None,
) -> dict | None:
    """The flagged range (served versions) that a coupon leg falls into, if any."""
    if not (family and market_key and scope and p is not None):
        return None
    market = market_name(market_key, line)
    for row in table:
        if (
            row["role"] == "active"
            and row["scope"] == scope
            and row["family"] == family
            and row["market"] == market
            and not row["zone_total"]
            and row["flag"] in ("optimistic", "cautious")
            and (row["low"] <= p < row["high"] or (row["high"] == 1.0 and p == 1.0))
        ):
            return row
    return None


def leg_note(row: dict | None) -> str | None:
    if row is None:
        return None
    side = "rzadziej" if row["flag"] == "optimistic" else "częściej"
    return (
        f"prospektywnie ten rynek w tym przedziale trafia {side} niż przewiduje model "
        f"(n={row['matches']} meczów)"
    )


def family_of_model(model: str | None) -> str | None:
    """Family of a candidate's ``model`` text ("<family> <version>")."""
    return model.split()[0] if model else None


def note_for_leg(table: list[dict], leg: dict) -> str | None:
    """Short note for a coupon leg (payload dict of a proposal or of Mój kupon)."""
    from sports_edge.services import league_code

    league = leg.get("league") or ""
    competition = league_code(league, "football") if league else None
    return leg_note(
        leg_flag(
            table,
            family=leg.get("family") or family_of_model(leg.get("model")),
            market_key=leg.get("market_key"),
            line=leg.get("line"),
            scope=scope_of(competition),
            p=leg.get("p_model"),
        )
    )


# ---------------------------------------------------------------- scoring and margin


def power_devig(prices: dict[str, float]) -> dict[str, float]:
    """Power method: p_i = (1/o_i)^k with k such that the probabilities sum to one."""
    raw = {k: 1.0 / float(v) for k, v in prices.items()}
    total = sum(raw.values())
    if abs(total - 1.0) < 1e-12:
        return raw
    lo, hi = (1.0, 64.0) if total > 1 else (1e-4, 1.0)
    for _ in range(200):
        mid = (lo + hi) / 2
        if sum(r**mid for r in raw.values()) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    found = {s: r**k for s, r in raw.items()}
    norm = sum(found.values())
    return {s: v / norm for s, v in found.items()}


def proportional_devig(prices: dict[str, float]) -> dict[str, float]:
    raw = {k: 1.0 / float(v) for k, v in prices.items()}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def devig(prices: dict[str, float], method: str = "power") -> dict[str, float]:
    return power_devig(prices) if method == "power" else proportional_devig(prices)


def score(probabilities: dict, outcomes: dict, market_key: str) -> tuple[float, float] | None:
    """(log loss, Brier) of one market forecast. Exclusive markets: −ln p(observed outcome)
    and the sum of squared errors over the outcomes; double chance (not exclusive): the mean
    of the binary scores of its selections. None when an outcome is unknown."""
    keys = list(probabilities)
    if not keys or any(outcomes.get(k) is None for k in keys):
        return None
    if market_key == "double_chance":
        losses = [
            -math.log(max(EPS, probabilities[k] if outcomes[k] else 1 - probabilities[k]))
            for k in keys
        ]
        briers = [(probabilities[k] - outcomes[k]) ** 2 for k in keys]
        return sum(losses) / len(keys), sum(briers) / len(keys)
    winners = [k for k in keys if outcomes[k] == 1]
    if len(winners) != 1:
        return None
    loss = -math.log(max(EPS, probabilities[winners[0]]))
    return loss, sum((probabilities[k] - outcomes[k]) ** 2 for k in keys)


# ---------------------------------------------------------------- STS prices before the start


def pre_match_quotes(store: Store, event_ids: set[str]) -> dict[tuple, dict]:
    """(event, market_key, line) → the last STS quote set generated BEFORE the start:
    {"quote_set_id", "valid_at", "prices": {selection: price}} (status ``ok`` only)."""
    if not event_ids:
        return {}
    out: dict[tuple, dict] = {}
    ids = sorted(event_ids)
    for chunk in range(0, len(ids), 400):
        part = ids[chunk : chunk + 400]
        marks = ",".join("?" for _ in part)
        rows = store.rows(
            f"""SELECT q.id quote_set_id, COALESCE(q.valid_at, q.ingested_at) observed_at,
            e.id event_id, e.start_at, m.market_key, m.line, s.key, o.price
            FROM odds o JOIN quote_sets q ON q.id=o.quote_set_id
            JOIN selections s ON s.id=o.selection_id JOIN markets m ON m.id=q.market_id
            JOIN events e ON e.id=m.event_id JOIN imports i ON i.id=q.import_id
            WHERE e.id IN ({marks}) AND q.status='ok' AND m.period='FT'
            AND i.bookmaker_id='sts' AND i.data_kind IN ('prospective','archive')
            AND e.start_at IS NOT NULL""",
            tuple(part),
        )
        sets: dict[str, dict] = {}
        for r in rows:
            if datetime.fromisoformat(r["observed_at"]) >= datetime.fromisoformat(r["start_at"]):
                continue  # a document from after the kick-off is never a pre-match price
            entry = sets.setdefault(
                r["quote_set_id"],
                {
                    "quote_set_id": r["quote_set_id"],
                    "valid_at": r["observed_at"],
                    "start_at": r["start_at"],
                    "key": (r["event_id"], r["market_key"], r["line"]),
                    "prices": {},
                },
            )
            entry["prices"][r["key"]] = float(r["price"])
        for entry in sets.values():
            current = out.get(entry["key"])
            if current is None or (current["valid_at"], current["quote_set_id"]) < (
                entry["valid_at"],
                entry["quote_set_id"],
            ):
                out[entry["key"]] = entry
    return out


def _market_probabilities(row: dict, quotes: dict, method: str) -> tuple[dict, dict] | None:
    """(de-margined probabilities over the forecast's selections, quote) or None."""
    if row["market_key"] == "double_chance":
        quote = quotes.get((row["match"], "1x2", None))
        if quote is None or set(quote["prices"]) != {"home", "draw", "away"}:
            return None
        base = devig(quote["prices"], method)
        found = {sel: base[a] + base[b] for sel, (a, b) in DC_PARTS.items()}
    else:
        quote = quotes.get((row["match"], row["market_key"], row["line"]))
        if quote is None or set(quote["prices"]) != set(row["probabilities"]):
            return None
        found = devig(quote["prices"], method)
    if set(found) != set(row["probabilities"]):
        return None
    return found, quote


def _bootstrap(diffs: list[float], *, resamples: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    draws = sorted(sum(rng.choices(diffs, k=len(diffs))) / len(diffs) for _ in range(resamples))
    return [round(draws[int(0.025 * resamples)], 5), round(draws[int(0.975 * resamples) - 1], 5)]


def _state(n: int, ci: list[float] | None, minimum: int) -> str:
    if n == 0:
        return "unknown"
    if n < minimum:
        return "insufficient"
    if ci[1] < 0:
        return "model_better"
    if ci[0] > 0:
        return "model_worse"
    return "no_difference"


def matches_text(n: int) -> str:
    """Polish count: 1 mecz, 2 mecze, 5 meczów (12–14 → meczów)."""
    if n == 1:
        return "1 mecz"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} mecze"
    return f"{n} meczów"


def state_label(state: str, matches: int = 0, minimum: int = 300) -> str:
    return {
        "unknown": "nieznane (brak wspólnych meczów)",
        "insufficient": f"za mało ({matches_text(matches)} < {minimum})",
        "model_better": "model lepszy",
        "model_worse": "model gorszy",
        "no_difference": "brak istotnej różnicy",
    }[state]


def market_comparison(
    store: Store, *, policy: EvalPolicy | None = None, rows: list[dict] | None = None
) -> list[dict]:
    """Model (served versions) vs the de-margined STS price on the same settled forecasts,
    per scope ("all", "core", "extended"), family and market — plus "*" (all markets of a
    family). Groups expected in the coupon zone appear even without data ("unknown")."""
    policy = policy or load_policy()
    rows = _role_forecasts(market_forecasts(store) if rows is None else rows, "active")
    quotes = pre_match_quotes(store, {r["match"] for r in rows})
    instances = []
    for row in rows:
        found = _market_probabilities(row, quotes, policy.devig)
        model = score(row["probabilities"], row["outcomes"], row["market_key"])
        if found is None or model is None:
            continue
        market_p, quote = found
        market = score(market_p, row["outcomes"], row["market_key"])
        other = _market_probabilities(
            row, quotes, "proportional" if policy.devig == "power" else "power"
        )
        sensitivity = score(other[0], row["outcomes"], row["market_key"]) if other else None
        lead = (
            datetime.fromisoformat(row["start_at"]) - datetime.fromisoformat(quote["valid_at"])
        ).total_seconds() / 3600
        instances.append(
            {
                **{k: row[k] for k in ("match", "scope", "family", "market", "prediction_id")},
                "quote_set_id": quote["quote_set_id"],
                "model_log_loss": model[0],
                "model_brier": model[1],
                "market_log_loss": market[0],
                "market_brier": market[1],
                "market_log_loss_other": sensitivity[0] if sensitivity else None,
                "lead_hours": lead,
            }
        )
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for item in instances:
        pooled = () if item["market"] == "double_chance" else (POOLED,)
        for scope in ("all", item["scope"]):
            for market in (item["market"], *pooled):
                groups[(scope, item["family"], market)].append(item)
    for scope in ("all", "core", "extended"):
        for family, markets in COUPON_MARKETS.items():
            for market in (*markets, POOLED):
                groups.setdefault((scope, family, market), [])
    out = []
    for (scope, family, market), part in sorted(groups.items(), key=lambda kv: kv[0]):
        if scope is None:
            continue
        out.append(_comparison_row(scope, family, market, part, policy=policy, instances=part))
    return out


def _comparison_row(scope, family, market, part, *, policy: EvalPolicy, instances) -> dict:
    by_match: dict[str, list[dict]] = defaultdict(list)
    for item in part:
        by_match[item["match"]].append(item)

    def mean(key, items):
        values = [i[key] for i in items if i[key] is not None]
        return sum(values) / len(values) if values else None

    diffs = [
        mean("model_log_loss", items) - mean("market_log_loss", items)
        for items in by_match.values()
    ]
    n = len(diffs)
    ci = _bootstrap(diffs, resamples=policy.resamples, seed=policy.seed) if n else None
    state = _state(n, ci, policy.market_min_matches)
    snapshot = digest(
        sorted([i["prediction_id"], i["quote_set_id"], i["market"]] for i in instances)
    )
    leads = sorted(i["lead_hours"] for i in part)
    by_age = {}
    for label, low, high in age_bands(policy.age_groups):
        group = [i for i in part if low < i["lead_hours"] <= high]
        matches_in = defaultdict(list)
        for item in group:
            matches_in[item["match"]].append(item)
        cut = [
            mean("model_log_loss", items) - mean("market_log_loss", items)
            for items in matches_in.values()
        ]
        band_ci = _bootstrap(cut, resamples=policy.resamples, seed=policy.seed) if cut else None
        band_state = _state(len(cut), band_ci, policy.market_min_matches)
        by_age[label] = {
            "matches": len(cut),
            "forecasts": len(group),
            "model_log_loss": _round(mean("model_log_loss", group)) if group else None,
            "market_log_loss": _round(mean("market_log_loss", group)) if group else None,
            "difference": _round(sum(cut) / len(cut)) if cut else None,
            "ci95": band_ci,
            "state": band_state,
            "state_label": state_label(band_state, len(cut), policy.market_min_matches),
        }
    per_match_lead = sorted(min(i["lead_hours"] for i in items) for items in by_match.values())
    return {
        "scope": scope,
        "family": family,
        "market": market,
        "matches": n,
        "forecasts": len(part),
        "state": state,
        "state_label": state_label(state, n, policy.market_min_matches),
        "model_log_loss": _round(mean("model_log_loss", part)),
        "market_log_loss": _round(mean("market_log_loss", part)),
        "market_log_loss_other_devig": _round(mean("market_log_loss_other", part)),
        "model_brier": _round(mean("model_brier", part)),
        "market_brier": _round(mean("market_brier", part)),
        "difference": _round(sum(diffs) / n) if n else None,  # model − market (< 0: model better)
        "ci95": ci,
        "median_lead_hours": round(leads[len(leads) // 2], 2) if leads else None,
        # TASK 012 §4.2: age of the STS document used, one value per match (its youngest
        # quote), and the comparison per age band (sensitivity; the verdict stays pooled)
        "document_age_hours": age_summary(per_match_lead, policy.age_groups),
        "by_document_age": by_age,
        "devig": policy.devig,
        "min_matches": policy.market_min_matches,
        "data_snapshot": snapshot,
    }


def age_bands(edges: tuple[float, ...]) -> list[tuple[str, float, float]]:
    """("≤ 3 h", 0, 3), ("3–12 h", 3, 12), ("> 12 h", 12, inf) for edges (3, 12)."""
    out, low = [], 0.0
    for edge in edges:
        label = f"≤ {edge:g} h" if low == 0 else f"{low:g}–{edge:g} h"
        out.append((label, low, edge))
        low = edge
    out.append((f"> {low:g} h", low, math.inf))
    return out


def age_summary(hours: list[float], edges: tuple[float, ...] = (3.0, 12.0)) -> dict:
    """Median, quartiles and the share per age band of the document ages (one per match)."""
    if not hours:
        return {"matches": 0, "median": None}
    values = sorted(hours)

    def quantile(q: float) -> float:
        position = q * (len(values) - 1)
        low = int(position)
        high = min(low + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (position - low)

    return {
        "matches": len(values),
        "median": round(quantile(0.5), 2),
        "p25": round(quantile(0.25), 2),
        "p75": round(quantile(0.75), 2),
        "bands": {
            label: round(sum(low < h <= high for h in values) / len(values), 4)
            for label, low, high in age_bands(edges)
        },
    }


def _round(value: float | None, digits: int = 5) -> float | None:
    return None if value is None else round(value, digits)


def record_market_benchmarks(
    store: Store, *, now: datetime, policy: EvalPolicy | None = None
) -> dict:
    """Append the comparison to ``market_benchmarks`` (idempotent: the same data and policy
    give the same id, so a repeated run adds nothing). ``validation_json`` is never touched."""
    policy = policy or load_policy()
    added = 0
    rows = market_comparison(store, policy=policy)
    for row in rows:
        key = digest(
            [
                "market_benchmark",
                row["scope"],
                row["family"],
                row["market"],
                policy.version,
                row["data_snapshot"],
            ]
        )
        if store.get("market_benchmarks", key) is not None:
            continue
        store.insert(
            "market_benchmarks",
            id=key,
            computed_at=iso(now),
            scope=row["scope"],
            family=row["family"],
            market=row["market"],
            matches=row["matches"],
            forecasts=row["forecasts"],
            state=row["state"],
            policy_version=policy.version,
            data_snapshot=row["data_snapshot"],
            payload_json=canonical(row),
        )
        added += 1
    return {"groups": len(rows), "recorded": added}


def latest_benchmarks(store: Store) -> dict[tuple, dict]:
    """(scope, family, market) → the newest stored benchmark row with its payload."""
    out: dict[tuple, dict] = {}
    for row in store.rows("SELECT * FROM market_benchmarks ORDER BY computed_at, id"):
        out[(row["scope"], row["family"], row["market"])] = {
            **row,
            "payload": json.loads(row["payload_json"]),
        }
    return out


# ---------------------------------------------------------------- TASK 012: weekly report

VERDICT_STATES = ("model_better", "model_worse", "no_difference")


# ---------------------------------------------------------------- TASK 013: odds age

ODDS_AGE_MIN_DAYS = 3  # days with match-day downloads before the numbers count as measured
ODDS_AGE_DAYS = 7


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _age_summary(ages: list[float]) -> dict:
    return {
        "matches": len(ages),
        "median_hours": round(_median(ages), 2) if ages else None,
        "within_3h": round(sum(a <= 3 for a in ages) / len(ages), 4) if ages else None,
    }


def odds_age_report(
    store: Store,
    *,
    now: datetime,
    days: int = ODDS_AGE_DAYS,
    since: str | None = None,
    settings: dict | None = None,
    min_days: int = ODDS_AGE_MIN_DAYS,
) -> dict:
    """TASK 013 §7: how old the STS price was at the kick-off, measured on REAL downloads.

    Matches of the 12 supported leagues that started in [first day 00:00 Warsaw, ``now``)
    (the last ``days`` days, or from ``since``); a match is one row even when STS listed it
    on several offer days. Age = kick-off − the time printed in the LAST STS document with a
    price of that match before the start (``valid_at``, else the import time). Per day: STS
    downloads of the automation (all and in the match-day mode), STS documents imported,
    the match-day plan with and without the daily cap (``cap_cut``: the cap removed slots).
    Fewer than ``min_days`` days with match-day downloads: "za mało danych" with the count of
    days — the numbers are still shown, never guessed or filled in."""
    from zoneinfo import ZoneInfo

    from sports_edge import automation
    from sports_edge.services import RESEARCH_COMPETITIONS, league_code

    warsaw = ZoneInfo("Europe/Warsaw")
    today = now.astimezone(warsaw).date()
    first = date.fromisoformat(since) if since else today - timedelta(days=days)
    start = datetime.combine(first, time.min, tzinfo=warsaw).astimezone(UTC)
    cfg = automation.matchday_config(settings or {})
    cap = int(cfg.get("daily_cap") or 0)
    events = [
        e
        for e in store.rows(
            "SELECT id, league, sport, home, away, start_at FROM events WHERE bookmaker_id='sts' "
            "AND start_at IS NOT NULL AND start_at>=? AND start_at<?",
            (iso(start - timedelta(days=1)), iso(now + timedelta(days=1))),
        )
        if league_code(e["league"], e["sport"]) in RESEARCH_COMPETITIONS
        and start <= datetime.fromisoformat(e["start_at"]) < now
    ]
    observed: dict[str, list[datetime]] = defaultdict(list)
    ids = sorted({e["id"] for e in events})
    for chunk in range(0, len(ids), 400):
        part = ids[chunk : chunk + 400]
        marks = ",".join("?" for _ in part)
        for r in store.rows(
            f"""SELECT DISTINCT m.event_id, COALESCE(q.valid_at, q.ingested_at) observed_at
            FROM quote_sets q JOIN markets m ON m.id=q.market_id JOIN imports i ON i.id=q.import_id
            WHERE m.event_id IN ({marks}) AND q.status='ok' AND i.bookmaker_id='sts'""",
            tuple(part),
        ):
            observed[r["event_id"]].append(datetime.fromisoformat(r["observed_at"]))
    matches: dict[tuple, dict] = {}
    for e in events:
        kickoff = datetime.fromisoformat(e["start_at"])
        before = [t for t in observed.get(e["id"], ()) if t < kickoff]
        key = (league_code(e["league"], e["sport"]), e["home"], e["away"], e["start_at"])
        entry = matches.setdefault(
            key, {"day": kickoff.astimezone(warsaw).date().isoformat(), "last": None}
        )
        if before and (entry["last"] is None or max(before) > entry["last"]):
            entry["last"] = max(before)
        entry["start"] = kickoff
    runs: dict[str, dict] = defaultdict(lambda: {"downloads": 0, "matchday_downloads": 0})
    for r in store.rows(
        "SELECT finished_at, payload_json FROM job_runs WHERE job=? AND finished_at>=? "
        "AND finished_at<? AND status IN ('ok','partial')",
        (automation._job_name("sts_offer"), iso(start), iso(now)),
    ):
        day = datetime.fromisoformat(r["finished_at"]).astimezone(warsaw).date().isoformat()
        runs[day]["downloads"] += 1
        runs[day]["matchday_downloads"] += bool(
            json.loads(r["payload_json"] or "{}").get("matchday")
        )
    documents: dict[str, int] = defaultdict(int)
    for r in store.rows(
        "SELECT ingested_at FROM imports WHERE bookmaker_id='sts' AND ingested_at>=? "
        "AND ingested_at<? AND data_kind IN ('prospective','archive')",
        (iso(start), iso(now)),
    ):
        documents[
            datetime.fromisoformat(r["ingested_at"]).astimezone(warsaw).date().isoformat()
        ] += 1
    uncapped = {
        "jobs": {"sts_offer": {"matchday": {**cfg, "daily_cap": 10**6}}},
    }
    per_day, ages_by_mode = [], {"matchday": [], "other": []}
    day = first
    while day <= today:
        label = day.isoformat()
        mine = [m for m in matches.values() if m["day"] == label]
        ages = [
            (m["start"] - m["last"]).total_seconds() / 3600 for m in mine if m["last"] is not None
        ]
        noon = datetime.combine(day, time(12), tzinfo=warsaw)
        planned = len(automation.matchday_plan(store, uncapped, now=noon)) if mine else 0
        found = runs.get(label, {"downloads": 0, "matchday_downloads": 0})
        mode = "matchday" if found["matchday_downloads"] else "other"
        ages_by_mode[mode] += ages
        per_day.append(
            {
                "day": label,
                **_age_summary(ages),
                "without_price": len(mine) - len(ages),
                "downloads": found["downloads"],
                "matchday_downloads": found["matchday_downloads"],
                "documents": documents.get(label, 0),
                "slots_uncapped": planned,
                "slots_capped": min(planned, cap),
                "cap_cut": planned > cap,
            }
        )
        day += timedelta(days=1)
    every = [a for v in ages_by_mode.values() for a in v]
    matchday_days = sum(1 for d in per_day if d["matchday_downloads"])
    measured = matchday_days >= min_days
    state = {
        "state": "measured" if measured else "insufficient",
        "label": "zmierzone"
        if measured
        else (
            f"za mało danych ({matchday_days} "
            f"{'dzień' if matchday_days == 1 else 'dni'} z pobraniami w dniu meczu; "
            f"potrzeba {min_days})"
        ),
    }
    content = sorted(
        [str(k), m["last"].isoformat() if m["last"] else None] for k, m in matches.items()
    )
    return {
        "period": {"from": first.isoformat(), "until": iso(now)},
        "days": len(per_day),
        "matchday_days": matchday_days,
        **state,
        "cap": cap,
        **_age_summary(every),
        "by_mode": {
            "matchday": _age_summary(ages_by_mode["matchday"]),
            "other": _age_summary(ages_by_mode["other"]),
        },
        "downloads": sum(d["downloads"] for d in per_day),
        "matchday_downloads": sum(d["matchday_downloads"] for d in per_day),
        "cap_cut_days": [d["day"] for d in per_day if d["cap_cut"]],
        "per_day": per_day,
        "digest": digest(["odds_age", content, [(d["day"], d["downloads"]) for d in per_day]]),
        "basis": "wiek = start meczu − czas z ostatniego dokumentu STS z kursem meczu przed "
        "startem; mecze 12 lig; dzień czasu polskiego",
    }


def today_odds_age(store: Store, *, now: datetime, settings: dict | None = None) -> dict:
    """TASK 015 §4.3 (one Dashboard line): how old the newest STS document with a price of
    today's matches (12 leagues, Warsaw day) is now, and the match-day downloads of today.
    No match today or no document: said so — never a zero age."""
    from zoneinfo import ZoneInfo

    from sports_edge import automation
    from sports_edge.services import RESEARCH_COMPETITIONS, league_code

    warsaw = ZoneInfo("Europe/Warsaw")
    today = now.astimezone(warsaw).date()
    start = datetime.combine(today, time.min, tzinfo=warsaw).astimezone(UTC)
    end = start + timedelta(days=1)
    events = [
        e
        for e in store.rows(
            "SELECT id, league, sport, home, away, start_at FROM events WHERE bookmaker_id='sts' "
            "AND start_at IS NOT NULL AND start_at>=? AND start_at<?",
            (iso(start - timedelta(hours=2)), iso(end + timedelta(hours=2))),
        )
        if league_code(e["league"], e["sport"]) in RESEARCH_COMPETITIONS
        and start <= datetime.fromisoformat(e["start_at"]) < end
    ]
    cfg = automation.matchday_config(settings or {})
    runs = len(automation.matchday_runs_today(store, now=now))
    # one match is one row even when STS listed it on several offer days (as odds_age_report)
    unique = {
        (league_code(e["league"], e["sport"]), e["home"], e["away"], e["start_at"]) for e in events
    }
    out = {
        "day": today.isoformat(),
        "matches": len(unique),
        "upcoming": sum(datetime.fromisoformat(k[3]) > now for k in unique),
        "document_at": None,
        "age_hours": None,
        "matchday_downloads": runs,
        "cap": int(cfg.get("daily_cap") or 0),
    }
    if not events:
        out["text"] = "Kursy na dziś: brak meczów 12 lig w ofercie STS na dziś."
        return out
    ids = sorted({e["id"] for e in events})
    newest = None
    for chunk in range(0, len(ids), 400):
        part = ids[chunk : chunk + 400]
        found = store.scalar(
            f"""SELECT MAX(COALESCE(q.valid_at, q.ingested_at)) FROM quote_sets q
            JOIN markets m ON m.id=q.market_id JOIN imports i ON i.id=q.import_id
            WHERE m.event_id IN ({",".join("?" for _ in part)}) AND q.status='ok'
            AND i.bookmaker_id='sts'""",
            tuple(part),
        )
        if found and (newest is None or found > newest):
            newest = found
    downloads = f"pobrania w dniu meczu dziś: {runs}/{out['cap']}"
    if newest is None:
        out["text"] = f"Kursy na dziś ({out['matches']} meczów 12 lig): brak danych · {downloads}."
        return out
    moment = datetime.fromisoformat(newest)
    age = max(0.0, (now - moment).total_seconds() / 3600)
    out.update(document_at=newest, age_hours=round(age, 2))
    hours = f"{age:.1f}".replace(".", ",")
    out["text"] = (
        f"Kursy na dziś ({out['matches']} meczów 12 lig, przed startem {out['upcoming']}): "
        f"ostatni dokument STS z {moment.astimezone(warsaw):%H:%M} — {hours} h temu · "
        f"{downloads}."
    )
    return out


def weekly_report(
    store: Store,
    *,
    now: datetime,
    policy: EvalPolicy | None = None,
    rows: list[dict] | None = None,
    settings: dict | None = None,
) -> dict:
    """The weekly report (TASK 012 §3.2): the coupon-zone calibration on matches and the STS
    market comparison, appended to ``calibration_reports`` as a history entry (one per data
    state and policy: an unchanged state adds nothing). An ALERT is created once per fact —
    the first flag "zbyt optymistyczna" / "zbyt ostrożna" of a market and range of the served
    versions, and the first verdict of the market comparison (model better / worse / no
    difference, i.e. at least the minimum of common matches). The alert changes nothing in the
    models, thresholds or coupons."""
    from sports_edge import alerts

    policy = policy or load_policy()
    rows = market_forecasts(store) if rows is None else rows
    calibration = calibration_report(store, policy=policy, rows=rows)
    comparison = market_comparison(store, policy=policy, rows=rows)
    age = odds_age_report(store, now=now, settings=settings)
    # alerts: markets that can enter a coupon, served versions (every flag stays in the entry)
    flags = [
        r
        for r in calibration["flags"]
        if r["role"] == "active" and r["market"] != POOLED and r["coupon_market"]
    ]
    verdicts = [r for r in comparison if r["state"] in VERDICT_STATES]
    snapshot = digest(sorted([r["prediction_id"], r["market"], r["role"]] for r in rows))
    zone = [
        {k: r[k] for k in ("role", "scope", "family", "market", "matches", "mean_p", "freq")}
        | {"ci95": r["ci95"], "flag": r["flag"]}
        for r in calibration["rows"]
        if r["zone_total"] and r["role"] == "active"
    ]
    payload = {
        "policy_version": policy.version,
        "matches": calibration["matches"],
        "forecasts": calibration["forecasts"],
        "zone": zone,
        "flags": [
            {k: r[k] for k in ("role", "scope", "family", "market", "range", "matches")}
            | {"mean_p": r["mean_p"], "freq": r["freq"], "flag": r["flag_label"], "ci95": r["ci95"]}
            for r in calibration["flags"]
        ],
        "market": [
            {k: r[k] for k in ("scope", "family", "market", "matches", "state_label")}
            | {"difference": r["difference"], "ci95": r["ci95"]}
            for r in comparison
            if r["matches"]
        ],
        "verdicts": len(verdicts),
        # TASK 013 §7: the age of the STS price at the kick-off (real downloads, last 7 days)
        "odds_age": {k: v for k, v in age.items() if k != "digest"},
        "note": "raport informacyjny — nie zmienia modeli, progów profili ani kuponów",
    }
    report_id = digest(["calibration_report", "weekly", policy.version, snapshot])
    if age["matches"] or age["downloads"]:  # TASK 013: new prices or downloads = a new state
        report_id = digest(
            ["calibration_report", "weekly", policy.version, snapshot, age["digest"]]
        )
    recorded = False
    if store.get("calibration_reports", report_id) is None:
        store.insert(
            "calibration_reports",
            id=report_id,
            created_at=iso(now),
            kind="weekly",
            policy_version=policy.version,
            data_snapshot=snapshot,
            matches=int(calibration["matches"]),
            flags=len(flags),
            verdicts=len(verdicts),
            payload_json=canonical(payload),
        )
        recorded = True
    added = 0
    # one alert per FACT: a market and range of a model with its flag ("all leagues" and the
    # scope flagged by the same matches are one fact; the message names every scope)
    facts: dict[tuple, list[dict]] = defaultdict(list)
    for r in flags:
        facts[(r["family"], r["market"], r["range"], r["flag"])].append(r)
    for (family, market, band, flag), found in sorted(facts.items()):
        first = max(found, key=lambda r: r["matches"])
        scopes = ", ".join(SCOPE_LABELS.get(r["scope"], r["scope"]) for r in found)
        added += alerts.create(
            store,
            kind="calibration_flag",
            message=(
                f"Strefa kuponów — {FAMILY_LABELS.get(family, family)}, "
                f"{market_label(market)}, {band} ({scopes}): {first['flag_label']} "
                f"(n={matches_text(first['matches'])}; średnia szansa "
                f"{_percent(first['mean_p'])}, trafność {_percent(first['freq'])}). Alert "
                "informacyjny — modele i progi bez zmian."
            ),
            source="raport tygodniowy kalibracji",
            now=now,
            identity=["calibration_flag", family, market, band, flag],
            payload={"scopes": [r["scope"] for r in found], "matches": first["matches"]},
        )
    for r in verdicts:
        added += alerts.create(
            store,
            kind="market_verdict",
            message=(
                f"Porównanie z rynkiem STS — {FAMILY_LABELS.get(r['family'], r['family'])}, "
                f"{market_label(r['market'])} ({SCOPE_LABELS.get(r['scope'])}): "
                f"{r['state_label']} (n={matches_text(r['matches'])}). Alert informacyjny — "
                "statusy rynków zmienia wyłącznie użytkownik."
            ),
            source="raport tygodniowy kalibracji",
            now=now,
            identity=["market_verdict", r["scope"], r["family"], r["market"], r["state"]],
            payload={k: r[k] for k in ("scope", "family", "market", "matches", "state")},
        )
    return {
        "recorded": recorded,
        "report_id": report_id,
        "matches": calibration["matches"],
        "flags": len(flags),
        "verdicts": len(verdicts),
        "alerts": added,
        "odds_age": {k: v for k, v in age.items() if k not in ("digest", "per_day")},
    }


def _percent(value: float) -> str:
    return f"{value * 100:.1f}".replace(".", ",") + "%"


def latest_weekly_report(store: Store) -> dict | None:
    rows = store.rows(
        "SELECT * FROM calibration_reports WHERE kind='weekly' ORDER BY created_at DESC, id LIMIT 1"
    )
    return {**rows[0], "payload": json.loads(rows[0]["payload_json"])} if rows else None


# ---------------------------------------------------------------- champion vs challenger


def paired_matches(
    store: Store,
    champion: str,
    challenger: str,
    *,
    policy: EvalPolicy | None = None,
    rows: list[dict] | None = None,
) -> dict:
    """Log loss of two versions on the SAME settled forecasts (match, market, line); the
    difference (challenger − champion) is averaged per match and its interval resamples
    matches. ``enough``: at least ``decision_matches`` common matches."""
    policy = policy or load_policy()
    rows = market_forecasts(store) if rows is None else rows
    by_key: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        # double chance is a function of the same 1X2 forecast: counted once, through 1X2
        if row["version"] in (champion, challenger) and row["market_key"] != "double_chance":
            by_key[(row["match"], row["market"])][row["version"]] = row
    per_match: dict[str, list[float]] = defaultdict(list)
    for (match, _market), pair in by_key.items():
        if champion not in pair or challenger not in pair:
            continue
        a = score(
            pair[champion]["probabilities"],
            pair[champion]["outcomes"],
            pair[champion]["market_key"],
        )
        b = score(
            pair[challenger]["probabilities"],
            pair[challenger]["outcomes"],
            pair[challenger]["market_key"],
        )
        if a is None or b is None:
            continue
        per_match[match].append(b[0] - a[0])
    diffs = [sum(v) / len(v) for v in per_match.values()]
    if not diffs:
        return {"matches": 0, "forecasts": 0, "enough": False, "needed": policy.decision_matches}
    return {
        "matches": len(diffs),
        "forecasts": sum(len(v) for v in per_match.values()),
        "difference": round(sum(diffs) / len(diffs), 5),
        "ci95": _bootstrap(diffs, resamples=policy.resamples, seed=policy.seed),
        "enough": len(diffs) >= policy.decision_matches,
        "needed": policy.decision_matches,
    }
