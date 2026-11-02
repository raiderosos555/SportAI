"""Advanced Football Prediction Engine in the application (TASK 008): the experiment on the
stored results, challenger registration, the user's decision and frozen shadow forecasts.

Nothing here changes a served model:

* an experiment registers a challenger only for the markets where every offline criterion
  passed (selection window of protocol v2, the holdout reported only);
* the challenger enters the prospective evaluation (shadow mode) only by the user's
  decision; its forecasts are frozen before kick-off and archived, never served;
* promotion would need a passed prospective comparison on enough settled matches and a
  separate decision of the user — it is not offered before that.
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sports_edge import advanced, learning
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.models.goals import GoalsMatch, fit, markets, score_matrix
from sports_edge.models.registry import model_overview, register_version, save_prediction
from sports_edge.probability_lab import LINES, dc_walk_forward_rates, served_view
from sports_edge.provenance import code_state, data_snapshot
from sports_edge.results_engine import effective_results
from sports_edge.storage import Store

HYPOTHESIS = "Advanced Football Prediction Engine"
FAMILIES = {False: "advanced_1x2", True: "advanced_goals"}
FAMILY_LABELS = {
    "advanced_1x2": "Model rozszerzony 1X2",
    "advanced_goals": "Model rozszerzony goli",
}
MIN_PROSPECTIVE = 300  # settled matches of a shadow challenger before promotion is possible
METHOD = "Advanced Football Prediction Engine — prognoza zamrożona przed meczem (tryb cienia)"
SHADOW_MARKETS = {  # market of the engine → (market_key, line, selections) of a prediction
    "total_1.5": ("total", "1.5", ("over", "under")),
    "total_2.5": ("total", "2.5", ("over", "under")),
    "btts": ("btts", None, ("yes", "no")),
}


def coverage(store: Store, split, competitions: tuple[str, ...]) -> dict:
    """Matches with statistics / as-of lineups per protocol window (decides xG and F)."""
    marks = ",".join("?" for _ in competitions)
    stats = Counter(
        split.window(r["event_date"])
        for r in store.rows(
            f"""SELECT DISTINCT entity_key, event_date FROM stat_observations
            WHERE kind='event_statistics' AND competition IN ({marks})""",
            competitions,
        )
        if r["event_date"]
    )
    lineups = Counter(
        split.window(r["start_at"][:10])
        for r in store.rows(
            """SELECT DISTINCT o.event_id, e.start_at FROM lineup_observations o
            JOIN events e ON e.id=o.event_id WHERE o.known_before_start=1"""
        )
        if r["start_at"]
    )
    return {"stats": dict(stats), "lineups": dict(lineups)}


def run(store: Store, paths, *, now: datetime, scope: str) -> dict:
    """The experiment of one scope on the ACTIVE Elo and Dixon–Coles versions; stored as an
    experiment (every variant, also the worse ones) and challengers registered."""
    from sports_edge.services import (
        MODEL_SCOPES,
        PROTOCOLS,
        active_versions,
        elo_walk_forward_forecasts,
        team_identities,
    )

    pair = active_versions(store)[scope]
    elo_row, goals_row = pair["elo"], pair["goals"]
    if elo_row is None or goals_row is None:
        raise ValueError("Brak aktywnych modeli Elo i Dixona–Colesa w tym zakresie")
    split = PROTOCOLS[scope]
    competitions = MODEL_SCOPES[scope]
    identities = team_identities(paths)
    rows = effective_results(
        store, sport="football", until=now, competitions=competitions, identities=identities
    )
    goals_report = json.loads(goals_row["validation_json"])
    rates = dc_walk_forward_rates(
        rows, half_life=goals_report["half_life_days"], from_date=split.validation_from
    )
    calibrators = goals_report["calibrators"]
    goals = {
        key: served_view(markets(score_matrix(*value), LINES), calibrators)
        for key, value in rates.items()
    }
    elo = elo_walk_forward_forecasts(
        store, now=now, identities=identities, competitions=competitions, version=elo_row
    )
    params = json.loads(elo_row["validation_json"])["hyperparameters"]
    report = advanced.run(
        rows,
        split=split,
        elo=elo,
        goals=goals,
        rates=rates,
        elo_params=params,
        coverage=coverage(store, split, competitions),
    )
    experiment_id = digest(["advanced", scope, elo_row["version_id"], iso(now)])
    report.update(
        scope=scope,
        experiment_id=experiment_id,
        ran_at=iso(now),
        data_through=max(r["match_date"] for r in rows),
        elo_version_id=elo_row["version_id"],
        goals_version_id=goals_row["version_id"],
        half_life_days=goals_report["half_life_days"],
    )
    report["challengers"] = register(store, paths, report, scope=scope, now=now)
    store.insert(
        "experiments",
        id=experiment_id,
        hypothesis=f"{HYPOTHESIS} — {scope}",
        registered_at=iso(now),
        kind="backtest",
        config_json=canonical(report),
        status="completed",
    )
    return report


def register(store: Store, paths, report: dict, *, scope: str, now: datetime) -> list[str]:
    """One experimental version per passed candidate variant (with the markets it passed)."""
    from sports_edge.services import MODEL_SCOPES, policy

    by_variant: dict[str, list[str]] = defaultdict(list)
    for market, decision in report["decision"].items():
        if decision.get("passed"):
            by_variant[decision["candidate"]].append(market)
    out = []
    for key, passed in sorted(by_variant.items()):
        variant = advanced.BY_KEY[key]
        family = FAMILIES[variant.goals]
        selection = {m: report["markets"][m]["windows"]["selection"][key] for m in passed}
        decisions = {
            m: {k: v for k, v in report["decision"][m].items() if k != "reliability"}
            for m in passed
        }
        version_id, _status, _reasons = register_version(
            store,
            family=family,
            sport="football",
            description=f"{HYPOTHESIS}: {variant.label}",
            version=f"{key}-{scope}-{iso(now)}",
            created_at=now,
            trained_through=None,
            training_data={
                "competitions": list(MODEL_SCOPES[scope]),
                "scope": scope,
                "variant": key,
                "label": variant.label,
                "markets": passed,
                "feature_version": report["feature_version"],
                "elo_version_id": report["elo_version_id"],
                "goals_version_id": report["goals_version_id"],
                "elo_parameters": report["elo_parameters"],
                "half_life_days": report["half_life_days"],
                "experiment_id": report["experiment_id"],
                "model": report["models"][key],
            },
            validation={
                "protocol": "v2",
                "competitions": list(MODEL_SCOPES[scope]),
                "decision": {
                    "n": min(row["n"] for row in selection.values()),
                    "ece": max(row["ece"] for row in selection.values()),
                },
                "selection": selection,
                "advanced": decisions,
                "leakage_checked": bool(report["leakage"]["ok"]),
                "markets_served": [],  # a challenger never serves
            },
            policy=policy(paths),
        )
        learning.record(
            store,
            kind="registered",
            actor="system",
            reason=f"Challenger z eksperymentu cech ({variant.label}); czeka na decyzję",
            now=now,
            version_id=version_id,
            scope=scope,
            family=family,
            key=["advanced_registered", version_id],
        )
        out.append(version_id)
    return out


def reports(store: Store, scope: str | None = None) -> list[dict]:
    """Stored experiments, oldest first."""
    rows = store.rows(
        "SELECT * FROM experiments WHERE hypothesis LIKE ? ORDER BY registered_at, rowid",
        (f"{HYPOTHESIS}%",),
    )
    found = [json.loads(r["config_json"]) for r in rows]
    return [r for r in found if scope is None or r.get("scope") == scope]


def versions(store: Store, scope: str | None = None) -> list[dict]:
    """Registered challengers with their role: waiting / shadow / rejected."""
    out = []
    for row in model_overview(store):
        if row["family"] not in FAMILY_LABELS:
            continue
        data = json.loads(row["training_data_json"])
        if scope is not None and data.get("scope") != scope:
            continue
        kinds = [
            e["kind"]
            for e in learning.events(store, scope=data.get("scope"), family=row["family"])
            if e["model_version_id"] == row["version_id"]
        ]
        role = (
            "odrzucony"
            if "rejected" in kinds
            else "tryb cienia"
            if "challenger_ready" in kinds
            else "czeka na decyzję"
        )
        out.append({**row, "training": data, "role": role})
    return out


def admit(store: Store, *, version_id: str, now: datetime) -> str:
    """The user's decision: the challenger enters the prospective evaluation (shadow mode)."""
    row = _version(store, version_id)
    data = json.loads(row["training_data_json"])
    return learning.record(
        store,
        kind="challenger_ready",
        actor="user",
        reason="Decyzja użytkownika: ocena prospektywna w trybie cienia (prognozy archiwizowane, "
        "niewykorzystywane w kuponach)",
        now=now,
        version_id=version_id,
        scope=data["scope"],
        family=row["family"],
        key=["advanced_admitted", version_id],
    )


def reject(store: Store, *, version_id: str, now: datetime) -> str:
    row = _version(store, version_id)
    data = json.loads(row["training_data_json"])
    return learning.record(
        store,
        kind="rejected",
        actor="user",
        reason="Decyzja użytkownika: challenger odrzucony",
        now=now,
        version_id=version_id,
        scope=data["scope"],
        family=row["family"],
        key=["advanced_rejected", version_id],
    )


def _version(store: Store, version_id: str) -> dict:
    row = next((r for r in model_overview(store) if r["version_id"] == version_id), None)
    if row is None or row["family"] not in FAMILY_LABELS:
        raise ValueError("To nie jest challenger modelu rozszerzonego")
    return row


def shadow(store: Store) -> list[dict]:
    return [v for v in versions(store) if v["role"] == "tryb cienia"]


# ---------------------------------------------------------------- frozen shadow forecasts


def _champion_forecast(store: Store, version_id: str, event_id: str, now: datetime):
    rows = store.rows(
        """SELECT * FROM predictions WHERE model_version_id=? AND event_id=? AND market_key='1x2'
        AND kind='prospective' AND created_at<=? ORDER BY created_at DESC LIMIT 1""",
        (version_id, event_id, iso(now)),
    )
    if not rows:
        return None
    return {
        **rows[0],
        "probabilities": json.loads(rows[0]["probabilities_json"]),
        "inputs": json.loads(rows[0]["inputs_json"]),
    }


def generate(store: Store, paths, *, now: datetime, events: list[dict] | None = None) -> dict:
    """Shadow forecasts of every admitted challenger for upcoming events: the same features
    and code as the experiment, as of now; archived, never served."""
    from sports_edge.services import _upcoming, active_versions, team_identities

    events = _upcoming(store, now) if events is None else events
    out = {"saved": 0, "kept": 0, "skipped": []}
    served = active_versions(store)
    for version in shadow(store):
        data = version["training"]
        scope = data["scope"]
        champions = served.get(scope) or {}
        elo_id = (champions.get("elo") or {}).get("version_id")
        goals_id = (champions.get("goals") or {}).get("version_id")
        if elo_id != data["elo_version_id"] or goals_id != data["goals_version_id"]:
            out["skipped"].append(
                f"{version['version'][:24]}: aktywne Elo/Dixon–Coles zmieniły się od eksperymentu "
                "— uruchom eksperyment ponownie"
            )
            continue
        rows = effective_results(
            store,
            sport="football",
            until=now,
            competitions=tuple(data["competitions"]),
            identities=team_identities(paths),
        )
        snapshot = data_snapshot(r["id"] for r in rows)
        info_cutoff = iso(max(datetime.fromisoformat(r["available_at"]) for r in rows))
        variant = advanced.BY_KEY[data["variant"]]
        fits = {}
        targets = []
        for event in events:
            champion = _champion_forecast(store, elo_id, event["id"], now)
            if champion is None:
                continue  # the champion does not forecast this event either
            teams = champion["inputs"].get("teams") or []
            code = champion["inputs"].get("competition")
            if len(teams) == 2 and code in data["competitions"]:
                targets.append((event, champion, teams, code))
        # one as-of pass: an upcoming match sees only the results of earlier days
        upcoming = [
            {
                "id": f"upcoming:{event['id']}",
                "competition": code,
                "match_date": datetime.fromisoformat(event["start_at"]).date().isoformat(),
                "home": teams[0],
                "away": teams[1],
            }
            for event, _champion, teams, code in targets
        ]
        every = advanced.team_features(
            rows + upcoming,
            k=data["elo_parameters"]["k"],
            home_advantage=data["elo_parameters"]["home_advantage"],
        )
        for event, champion, teams, code in targets:
            feats = every[f"upcoming:{event['id']}"]
            if not advanced.complete(feats):
                out["skipped"].append(f"{teams[0]} - {teams[1]}: brak kompletu cech as-of")
                continue
            rates = None
            if variant.kind == "poisson_offset":
                if code not in fits:
                    mine = sorted(
                        (r for r in rows if r["competition"] == code), key=lambda r: r["match_date"]
                    )
                    fits[code] = fit(
                        [
                            GoalsMatch(
                                r["match_date"],
                                r["home"],
                                r["away"],
                                r["home_score"],
                                r["away_score"],
                            )
                            for r in mine
                        ],
                        fit_date=(now.date() + timedelta(days=1)).isoformat(),
                        half_life_days=data["half_life_days"],
                    )
                model = fits[code]
                found = model.rates(*teams) if model is not None else None
                if found is None:
                    out["skipped"].append(f"{teams[0]} - {teams[1]}: drużyna poza modelem goli")
                    continue
                rates = (found[0], found[1], model.rho)
            elo = champion["probabilities"]
            predicted = advanced.predict_match(
                data["model"], data["variant"], feats, elo=elo, rates=rates
            )
            inputs = {
                "info_cutoff": info_cutoff,
                "data_snapshot": snapshot,
                "code_state": code_state(),
                "teams": teams,
                "competition": code,
                "features": feats,
                "elo_input": elo,
                "elo_prediction_id": champion["id"],
                "rates": list(rates) if rates else None,
                "feature_version": data["feature_version"],
                "data": "wyniki as-of + prognoza Elo aktywnej wersji",
            }
            out_rows = [("1x2", None, dict(zip(advanced.OUTCOMES, predicted["1x2"], strict=True)))]
            if variant.goals:
                for market, (key, line, sides) in SHADOW_MARKETS.items():
                    p = float(predicted[market])
                    out_rows.append((key, line, dict(zip(sides, (p, 1.0 - p), strict=True))))
            for market_key, line, probabilities in out_rows:
                if _already(
                    store,
                    version["version_id"],
                    event["id"],
                    market_key,
                    line,
                    canonical(inputs["features"]) + snapshot,
                ):
                    out["kept"] += 1
                    continue
                uncertainty = {
                    "method": METHOD,
                    "challenger": True,
                    "variant": data["variant"],
                    "family": market_key if market_key != "total" else f"total_{line}",
                    "passed_markets": data["markets"],
                    "fingerprint": canonical(inputs["features"]) + snapshot,
                }
                if market_key == "1x2":
                    dc = predicted["double_chance"]
                    uncertainty["derived"] = {
                        "double_chance": {
                            "calibrated": dict(
                                zip(("home_draw", "draw_away", "home_away"), dc, strict=True)
                            )
                        }
                    }
                save_prediction(
                    store,
                    model_version_id=version["version_id"],
                    event_id=event["id"],
                    market_key=market_key,
                    period="FT",
                    line=line,
                    probabilities=probabilities,
                    uncertainty=uncertainty,
                    inputs=inputs,
                    kind="prospective",
                    created_at=now,
                )
                out["saved"] += 1
    return out


def _already(store, version_id, event_id, market_key, line, fingerprint) -> bool:
    for row in store.rows(
        """SELECT uncertainty_json FROM predictions WHERE model_version_id=? AND event_id=?
        AND market_key=? AND line IS ? AND kind='prospective'""",
        (version_id, event_id, market_key, line),
    ):
        if json.loads(row["uncertainty_json"]).get("fingerprint") == fingerprint:
            return True
    return False


def event_view(store: Store, event_id: str) -> list[dict]:
    """Shadow forecasts of one event (newest per challenger and market) for the match page."""
    rows = store.rows(
        """SELECT p.*, v.version, m.family FROM predictions p
        JOIN model_versions v ON v.id=p.model_version_id JOIN models m ON m.id=v.model_id
        WHERE p.event_id=? AND m.family IN ('advanced_1x2','advanced_goals')
        ORDER BY p.created_at DESC""",
        (event_id,),
    )
    seen, out = set(), []
    for row in rows:
        key = (row["model_version_id"], row["market_key"], row["line"])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                **row,
                "probabilities": json.loads(row["probabilities_json"]),
                "uncertainty": json.loads(row["uncertainty_json"]),
            }
        )
    return out


def prospective(store: Store, version: dict) -> dict:
    """Champion vs shadow challenger on the SAME settled forecasts, per market group."""
    from sports_edge.retraining import paired

    data = version["training"]
    out = {"1x2": paired(store, data["elo_version_id"], version["version_id"], markets={"1x2"})}
    if advanced.BY_KEY[data["variant"]].goals:
        out["goals"] = paired(
            store, data["goals_version_id"], version["version_id"], markets={"total", "btts"}
        )
    settled = max((v.get("n", 0) for v in out.values()), default=0)
    out["promotion"] = {
        "possible": False,
        "settled": settled,
        "required": MIN_PROSPECTIVE,
        "reason": (
            f"ocena prospektywna: {settled} z wymaganych {MIN_PROSPECTIVE} rozliczonych prognoz"
            if settled < MIN_PROSPECTIVE
            else "próba wystarczająca — promocja wymaga osobnej, świadomej decyzji (wkrótce)"
        ),
    }
    return out
