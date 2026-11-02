"""Independent sports data engine and the first predictive experiment (Elo baseline).

Rules: only licensed data with a recorded source; nothing synthesised; every row carries
``available_at`` and the basis of that time (confirmed by the source or a conservative
assumption). Experiments split data chronologically into train / validation / holdout; the
holdout is evaluated once with hyper-parameters chosen on validation only.
"""

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import sqrt

from sports_edge.analysis.policy import Policy
from sports_edge.datasources import openfootball
from sports_edge.datasources.competitions import COMPETITIONS
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.matching import TeamIdentities, alias_map, match_participant
from sports_edge.models import calibration
from sports_edge.models.elo import (
    Elo,
    HistoricalMatch,
    fit_draw_margin,
    rating_differences,
    walk_forward,
)
from sports_edge.models.registry import register_version, save_prediction
from sports_edge.offers.text import NameNormalizer
from sports_edge.provenance import code_state, data_snapshot
from sports_edge.results_engine import effective_results, known_names, priority
from sports_edge.storage import Store

REQUIRED = ("date", "home", "away", "home_score", "away_score")
AVAILABILITY_LAG = timedelta(hours=36)
MIN_TEAM_GAMES = 10
MAX_DATA_GAP_DAYS = 21  # newest result in the league must be at most this old at kick-off
GRID = {"k": (15.0, 20.0, 30.0), "home_advantage": (40.0, 60.0, 80.0)}


def _parse_date(value: str) -> date:
    value = value.strip()
    if "." in value:
        day, month, year = value.split(".")[:3]
        return date(int(year), int(month), int(day))
    return date.fromisoformat(value[:10])


def import_results(
    store: Store,
    rows: list[dict],
    *,
    file_bytes: bytes,
    file_name: str,
    source_name: str,
    license_note: str,
    sport: str,
    competition: str,
    season: str | None,
    now: datetime,
    availability_basis: str,
    provider: str | None = None,
) -> dict:
    """Store completed matches with their availability time; refuse unlicensed data.

    ``provider`` marks the source in the results hierarchy (None: derived from the import,
    "manual_official": results the user copied from the official organiser).
    """
    if not license_note.strip():
        raise ValueError("Podaj źródło i podstawę prawną użycia danych")
    sha = hashlib.sha256(file_bytes).hexdigest()
    import_id = digest(["data_import", sha, "results"])
    if store.get("data_imports", import_id):
        return {"import_id": import_id, "duplicate": True, "inserted": 0, "errors": []}
    errors, accepted = [], []
    for index, row in enumerate(rows, start=1):
        if row["available_at"] > now:
            errors.append(f"pozycja {index}: wynik niedostępny w chwili importu")
        elif row["available_at"].astimezone(UTC).date().isoformat() < row["match_date"]:
            errors.append(f"pozycja {index}: wynik oznaczony jako dostępny przed dniem meczu")
        elif row["home"] == row["away"] or min(row["home_score"], row["away_score"]) < 0:
            errors.append(f"pozycja {index}: niepoprawne drużyny lub wynik")
        else:
            accepted.append(row)
    inserted = 0
    with store.transaction():
        store.insert(
            "data_imports",
            id=import_id,
            source_name=source_name,
            license_note=license_note,
            file_sha256=sha,
            file_name=file_name,
            kind="results",
            rows=len(accepted),
            ingested_at=iso(now),
        )
        for row in accepted:
            inserted += store.insert(
                "historical_results",
                # Rows of a named provider are observations (several sources, corrections);
                # unnamed CSV / openfootball rows keep the original natural key.
                id=digest(
                    ["hist", sport, competition, row["match_date"], row["home"], row["away"]]
                    if provider is None
                    else [
                        "hist",
                        provider,
                        sport,
                        competition,
                        row["match_date"],
                        row["home"],
                        row["away"],
                        row["home_score"],
                        row["away_score"],
                    ]
                ),
                import_id=import_id,
                sport=sport,
                competition=competition,
                season=season,
                match_date=row["match_date"],
                home=row["home"],
                away=row["away"],
                home_score=row["home_score"],
                away_score=row["away_score"],
                available_at=iso(row["available_at"]),
                available_at_basis=availability_basis,
                start_at=iso(row["start_at"]) if row.get("start_at") else None,
                details_json=canonical(row.get("details") or {}),
                provider=provider,
                observed_at=iso(now),
            )
    return {"import_id": import_id, "duplicate": False, "inserted": inserted, "errors": errors}


def import_results_csv(
    store: Store,
    data: bytes,
    *,
    file_name: str,
    source_name: str,
    license_note: str,
    sport: str,
    competition: str,
    now: datetime,
    provider: str | None = None,
) -> dict:
    """CSV: date, home, away, home_score, away_score[, season, available_at, start_at]."""
    text = data.decode("utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = {c.strip().lower() for c in reader.fieldnames or []}
    missing = [c for c in REQUIRED if c not in columns]
    if missing:
        raise ValueError(f"Brak kolumn: {', '.join(missing)}")
    rows, errors, seasons = [], [], set()
    confirmed = "available_at" in columns
    for number, raw in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        try:
            played = _parse_date(row["date"])
            start = _aware(row["start_at"]) if row.get("start_at") else None
            if confirmed:
                available = _aware(row["available_at"])
            else:
                available = datetime.combine(played, time(0), UTC) + AVAILABILITY_LAG
            rows.append(
                {
                    "match_date": played.isoformat(),
                    "start_at": start,
                    "home": row["home"],
                    "away": row["away"],
                    "home_score": int(row["home_score"]),
                    "away_score": int(row["away_score"]),
                    "available_at": available,
                }
            )
            seasons.add(row.get("season") or None)
        except (ValueError, KeyError) as error:
            errors.append(f"wiersz {number}: {error}")
    out = import_results(
        store,
        rows,
        file_bytes=data,
        file_name=file_name,
        source_name=source_name,
        license_note=license_note,
        sport=sport,
        competition=competition,
        season=seasons.pop() if len(seasons) == 1 else None,
        now=now,
        availability_basis="source_timestamp" if confirmed else "assumed_lag_36h",
        provider=provider,
    )
    out["errors"] = errors + out["errors"]
    return out


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp needs a UTC offset")
    return parsed


def import_openfootball(
    store: Store, data: bytes, *, league_code: str, season: str, file_name: str, now: datetime
) -> dict:
    league = openfootball.LEAGUES[league_code]
    return import_results(
        store,
        openfootball.parse(data, league=league, season=season),
        file_bytes=data,
        file_name=file_name,
        source_name="openfootball/football.json",
        license_note=openfootball.LICENSE,
        sport="football",
        competition=league_code,
        season=season,
        now=now,
        availability_basis=openfootball.AVAILABILITY_BASIS,
    )


def history(
    store: Store,
    *,
    sport: str,
    until: datetime,
    competitions: tuple[str, ...] | None = None,
    identities: TeamIdentities | None = None,
) -> list[HistoricalMatch]:
    """Results whose availability time is not later than ``until`` (as-of view).

    One match per fixture even when several providers reported it: the source hierarchy and
    the newest correction decide (``results_engine.effective_results``).
    """
    return [
        _to_match(r)
        for r in effective_results(
            store, sport=sport, until=until, competitions=competitions, identities=identities
        )
    ]


def _to_match(row: dict) -> HistoricalMatch:
    return HistoricalMatch(
        available_at=datetime.fromisoformat(row["available_at"]),
        match_date=row["match_date"],
        home=row["home"],
        away=row["away"],
        home_score=row["home_score"],
        away_score=row["away_score"],
        start_at=datetime.fromisoformat(row["start_at"]) if row["start_at"] else None,
        competition=row["competition"],
        result_id=row["id"],
    )


def coverage(store: Store, *, until: datetime | None = None) -> list[dict]:
    """Per competition: matches, period, seasons, availability basis and providers."""
    rows = effective_results(
        store, sport="football", until=until or datetime.max.replace(tzinfo=UTC)
    )
    others = store.rows("SELECT DISTINCT sport FROM historical_results WHERE sport <> 'football'")
    for other in others:
        rows += effective_results(
            store, sport=other["sport"], until=until or datetime.max.replace(tzinfo=UTC)
        )
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["sport"], row["competition"])].append(row)
    return [
        {
            "sport": sport,
            "competition": competition,
            "matches": len(items),
            "first": min(r["match_date"] for r in items),
            "last": max(r["match_date"] for r in items),
            "seasons": len({r["season"] for r in items}),
            "availability_basis": ",".join(sorted({r["available_at_basis"] for r in items})),
            "providers": ",".join(sorted({r["provider_resolved"] for r in items}, key=priority)),
            "conflicts": sum(r["conflict"] for r in items),
        }
        for (sport, competition), items in sorted(groups.items())
    ]


# ---------------------------------------------------------------- experiment


@dataclass(frozen=True)
class Split:
    """Chronological windows of one experiment (ISO dates, each ``from`` inclusive).

    Protocol v1 (TASK 003–006): train < validation_from ≤ validation < holdout_from ≤ holdout;
    the holdout also decided statuses. Protocol v2 (TASK 007) adds two windows so that the
    final holdout never decides anything:

    train < validation_from ≤ validation (hyperparameters) < calibration_from ≤ calibration
    (calibration maps, draw margin, interval widths) < selection_from ≤ selection (model
    choice, predictive statuses, league gates) < holdout_from ≤ final holdout (reported once).
    """

    validation_from: str
    holdout_from: str
    calibration_from: str | None = None
    selection_from: str | None = None

    @property
    def protocol(self) -> str:
        return "v2" if self.selection_from else "v1"

    @property
    def tuning_until(self) -> str:
        """End of the hyperparameter window."""
        return self.calibration_from or self.holdout_from

    @property
    def decision_from(self) -> str:
        """First day of the window whose results decide statuses and the served model."""
        return self.selection_from or self.holdout_from

    def window(self, day: str) -> str:
        if day < self.validation_from:
            return "train"
        if self.protocol == "v1":
            return "validation" if day < self.holdout_from else "holdout"
        if day < self.calibration_from:
            return "validation"
        if day < self.selection_from:
            return "calibration"
        return "selection" if day < self.holdout_from else "holdout"

    def as_dict(self) -> dict:
        out = {"validation_from": self.validation_from, "holdout_from": self.holdout_from}
        if self.selection_from:
            out.update(
                calibration_from=self.calibration_from,
                selection_from=self.selection_from,
                protocol="v2",
            )
        return out


def _scoped(matches: list[HistoricalMatch]) -> list[HistoricalMatch]:
    """Separate rating pools per competition: team keys are prefixed with the league code."""
    return [
        HistoricalMatch(
            available_at=m.available_at,
            match_date=m.match_date,
            home=f"{m.competition}|{m.home}",
            away=f"{m.competition}|{m.away}",
            home_score=m.home_score,
            away_score=m.away_score,
            start_at=m.start_at,
            competition=m.competition,
            result_id=m.result_id,
        )
        for m in matches
    ]


def _forecasts(matches, *, k, home_advantage, margin_until: str) -> tuple[list[dict], float]:
    """Draw margin fitted on matches before ``margin_until``; then one as-of pass."""
    before = [m for m in matches if m.match_date < margin_until]
    margin = fit_draw_margin(rating_differences(before, Elo(k=k, home_advantage=home_advantage)))
    model = Elo(k=k, home_advantage=home_advantage, draw_margin=margin)
    return walk_forward(matches, model, three_way=True), margin


def _metrics(items: list[dict]) -> dict:
    if not items:
        return {"n": 0}
    forecasts = [i["forecast"] for i in items]
    outcomes = [i["match"].outcome for i in items]
    p, y = calibration.one_vs_rest(forecasts, outcomes)
    return {
        "n": len(items),
        "log_loss": round(calibration.log_loss(forecasts, outcomes), 5),
        "brier": round(calibration.brier(forecasts, outcomes), 5),
        "ece": round(calibration.ece(p, y), 5),
        "reliability": calibration.reliability(p, y),
    }


def _climatology(train: list[HistoricalMatch]) -> dict[str, dict[str, float]]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for m in train:
        counts[m.competition][m.outcome] += 1
    return {
        league: {k: counter[k] / sum(counter.values()) for k in ("home", "draw", "away")}
        for league, counter in counts.items()
    }


def market_benchmark(store: Store, items: list[dict], *, moment: str = "closing") -> dict | None:
    """Model vs de-margined odds of the SAME events (benchmark_odds); None if no such data."""
    ids = [i["match"].result_id for i in items]
    if not ids:
        return None
    rows = store.rows(
        f"""SELECT result_id, operator, selection, price FROM benchmark_odds
        WHERE moment=? AND market_key='1x2' AND result_id IN ({",".join("?" for _ in ids)})""",
        (moment, *ids),
    )
    if not rows:
        return None
    prices: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        prices[(r["result_id"], r["operator"])][r["selection"]] = float(r["price"])
    by_id = {i["match"].result_id: i for i in items}
    # One observation per event: several operators of the same match are averaged into one
    # de-margined consensus instead of being counted as independent events.
    per_event: dict[str, list[dict]] = defaultdict(list)
    operators = set()
    for (result_id, operator), odds in prices.items():
        if set(odds) != {"home", "draw", "away"}:
            continue
        overround = sum(1 / o for o in odds.values())
        per_event[result_id].append({k: (1 / o) / overround for k, o in odds.items()})
        operators.add(operator)
    model_f, market_f, outcomes = [], [], []
    for result_id, books in sorted(per_event.items()):
        market_f.append({k: sum(b[k] for b in books) / len(books) for k in books[0]})
        model_f.append(by_id[result_id]["forecast"])
        outcomes.append(by_id[result_id]["match"].outcome)
    if not outcomes:
        return None
    return {
        "same_events": True,
        "n_events": len(outcomes),
        "moment": moment,
        "operators": sorted(operators),
        "operator_quotes": sum(len(b) for b in per_event.values()),
        "model_log_loss": round(calibration.log_loss(model_f, outcomes), 5),
        "market_log_loss": round(calibration.log_loss(market_f, outcomes), 5),
        "model_brier": round(calibration.brier(model_f, outcomes), 5),
        "market_brier": round(calibration.brier(market_f, outcomes), 5),
        "note": "Kursy innego operatora jako benchmark badawczy — nie są ofertą STS.",
    }


def run_elo_experiment(
    store: Store,
    *,
    competitions: tuple[str, ...],
    split: Split,
    now: datetime,
    policy: Policy,
    register: bool = True,
    identities: TeamIdentities | None = None,
) -> dict:
    """Chronological train / validation / holdout experiment for 1X2 (regular time)."""
    matches = _scoped(
        history(
            store, sport="football", until=now, competitions=competitions, identities=identities
        )
    )
    if not matches:
        raise ValueError("Brak danych historycznych dla wybranych rozgrywek")
    train = [m for m in matches if m.match_date < split.validation_from]
    validation = [m for m in matches if split.validation_from <= m.match_date < split.tuning_until]
    holdout = [m for m in matches if m.match_date >= split.holdout_from]
    if split.protocol == "v2":
        return _run_elo_v2(
            store,
            matches,
            train=train,
            validation=validation,
            competitions=competitions,
            split=split,
            now=now,
            policy=policy,
            register=register,
        )
    if len(train) < 1000 or len(validation) < policy.min_validation_events or not holdout:
        raise ValueError(
            f"Za mało danych: trening {len(train)}, walidacja {len(validation)}, "
            f"holdout {len(holdout)}"
        )
    candidates = []
    for k in GRID["k"]:
        for hfa in GRID["home_advantage"]:
            items, margin = _forecasts(
                train + validation, k=k, home_advantage=hfa, margin_until=split.validation_from
            )
            scored = [i for i in items if i["match"].match_date >= split.validation_from]
            candidates.append(
                {"k": k, "home_advantage": hfa, "draw_margin": margin, **_metrics(scored)}
            )
    best = min(candidates, key=lambda c: c["log_loss"])
    items, margin = _forecasts(
        matches, k=best["k"], home_advantage=best["home_advantage"], margin_until=split.holdout_from
    )
    holdout_items = [i for i in items if i["match"].match_date >= split.holdout_from]
    climate = _climatology([m for m in matches if m.match_date < split.holdout_from])
    base = [climate[i["match"].competition] for i in holdout_items]
    outcomes = [i["match"].outcome for i in holdout_items]
    by_season: dict[str, list] = defaultdict(list)
    for item in items:
        if item["match"].match_date >= split.validation_from:
            year = int(item["match"].match_date[:4])
            month = int(item["match"].match_date[5:7])
            start = year if month >= 7 else year - 1
            by_season[f"{start}-{str(start + 1)[2:]}"].append(item)
    by_league: dict[str, list] = defaultdict(list)
    for item in holdout_items:
        by_league[item["match"].competition].append(item)
    holdout_metrics = _metrics(holdout_items)
    report = {
        "family": "elo_ordered_draw",
        "market": "1x2 (czas regulaminowy)",
        "competitions": list(competitions),
        "split": {"validation_from": split.validation_from, "holdout_from": split.holdout_from},
        "n": {"train": len(train), "validation": len(validation), "holdout": len(holdout)},
        "hyperparameters": {
            "k": best["k"],
            "home_advantage": best["home_advantage"],
            "draw_margin_holdout": margin,
        },
        "selection": [{k: v for k, v in c.items() if k != "reliability"} for c in candidates],
        "holdout": holdout_metrics,
        "baseline_climatology": {
            "log_loss": round(calibration.log_loss(base, outcomes), 5),
            "brier": round(calibration.brier(base, outcomes), 5),
        },
        "walk_forward_by_season": {
            season: {k: v for k, v in _metrics(group).items() if k != "reliability"}
            for season, group in sorted(by_season.items())
        },
        "holdout_by_league": {
            league: {k: v for k, v in _metrics(group).items() if k != "reliability"}
            for league, group in sorted(by_league.items())
        },
        "market_benchmark": market_benchmark(store, holdout_items),
        "availability_basis": sorted(
            {
                r["available_at_basis"]
                for r in store.rows(
                    f"SELECT DISTINCT available_at_basis FROM historical_results "
                    f"WHERE competition IN ({','.join('?' for _ in competitions)})",
                    competitions,
                )
            }
        ),
        "leakage_checked": True,
        "ordering_rule": "prognozy dnia D wyłącznie z wyników dni < D; podział czasowy",
        "data_snapshot": data_snapshot(m.result_id for m in matches),
        "code_state": code_state(),
        "data_until": iso(max(m.available_at for m in matches)),
    }
    report["improves_on_baseline"] = (
        holdout_metrics["log_loss"] < report["baseline_climatology"]["log_loss"]
    )
    if register:
        version = f"elo-1x2-{'-'.join(competitions)}-{now:%Y%m%d%H%M}"
        version_id, status, reasons = register_version(
            store,
            family="elo_ordered_draw",
            sport="football",
            description="Elo z marginesem remisu, 1X2 czas regulaminowy",
            version=version,
            created_at=now,
            trained_through=max(m.available_at for m in matches),
            training_data={
                "competitions": list(competitions),
                "data_snapshot": report["data_snapshot"],
                "code_state": report["code_state"],
                "hyperparameters": report["hyperparameters"],
                "split": report["split"],
            },
            validation=report,
            policy=policy,
        )
        report.update(version_id=version_id, status=status, status_reasons=reasons)
        with store.transaction():
            store.insert(
                "experiments",
                id=digest(["experiment", version_id]),
                hypothesis="Elo (1X2) poprawia log loss względem klimatologii poza próbą; "
                "przewaga nad rynkiem wymaga kursów tych samych meczów",
                registered_at=iso(now),
                kind="backtest",
                config_json=canonical(
                    {"version_id": version_id, "split": report["split"], "grid": GRID}
                ),
                status="concluded" if report["market_benchmark"] else "inconclusive_no_market",
            )
    return report


def _run_elo_v2(
    store: Store,
    matches,
    *,
    train,
    validation,
    competitions: tuple[str, ...],
    split: Split,
    now: datetime,
    policy: Policy,
    register: bool,
) -> dict:
    """Protocol v2: hyperparameters on validation, draw margin and interval widths fitted on
    data before the selection window, statuses on the selection window; the final holdout is
    reported only."""
    selection = [m for m in matches if split.window(m.match_date) == "selection"]
    if len(train) < 300 or len(validation) < 300 or len(selection) < 300:
        raise ValueError(
            f"Za mało danych (protokół v2): trening {len(train)}, walidacja {len(validation)}, "
            f"selekcja {len(selection)}"
        )
    candidates = []
    for k in GRID["k"]:
        for hfa in GRID["home_advantage"]:
            items, margin = _forecasts(
                train + validation, k=k, home_advantage=hfa, margin_until=split.validation_from
            )
            scored = [i for i in items if i["match"].match_date >= split.validation_from]
            candidates.append(
                {"k": k, "home_advantage": hfa, "draw_margin": margin, **_metrics(scored)}
            )
    best = min(candidates, key=lambda c: c["log_loss"])
    items, margin = _forecasts(
        matches,
        k=best["k"],
        home_advantage=best["home_advantage"],
        margin_until=split.selection_from,
    )
    windows: dict[str, list] = defaultdict(list)
    for item in items:
        windows[split.window(item["match"].match_date)].append(item)
    climate = _climatology([m for m in matches if m.match_date < split.selection_from])

    def baseline(group):
        known = [i for i in group if i["match"].competition in climate]
        if not known:
            return {"log_loss": None, "brier": None}
        base = [climate[i["match"].competition] for i in known]
        outcomes = [i["match"].outcome for i in known]
        return {
            "log_loss": round(calibration.log_loss(base, outcomes), 5),
            "brier": round(calibration.brier(base, outcomes), 5),
        }

    def by_league(group):
        leagues: dict[str, list] = defaultdict(list)
        for item in group:
            leagues[item["match"].competition].append(item)
        return {
            league: {k: v for k, v in _metrics(found).items() if k != "reliability"}
            for league, found in sorted(leagues.items())
        }

    by_season: dict[str, list] = defaultdict(list)
    for item in items:
        if item["match"].match_date >= split.validation_from:
            year, month = int(item["match"].match_date[:4]), int(item["match"].match_date[5:7])
            start = year if month >= 7 else year - 1
            by_season[f"{start}-{str(start + 1)[2:]}"].append(item)
    decision = _metrics(windows["selection"])
    report = {
        "family": "elo_ordered_draw",
        "market": "1x2 (czas regulaminowy)",
        "protocol": "v2",
        "competitions": list(competitions),
        "split": split.as_dict(),
        "n": {
            "train": len(train),
            **{name: len(windows[name]) for name in ("validation", "calibration", "selection")},
            "holdout": len(windows["holdout"]),
        },
        "hyperparameters": {
            "k": best["k"],
            "home_advantage": best["home_advantage"],
            "draw_margin": margin,
            "draw_margin_until": split.selection_from,
        },
        "selection": [{k: v for k, v in c.items() if k != "reliability"} for c in candidates],
        # interval widths of prospective forecasts come from this window (never the holdout)
        "calibration": _metrics(windows["calibration"]),
        "decision": decision,
        "baseline_climatology": baseline(windows["selection"]),
        "decision_by_league": by_league(windows["selection"]),
        "holdout": _metrics(windows["holdout"]),
        "holdout_baseline": baseline(windows["holdout"]),
        "holdout_by_league": by_league(windows["holdout"]),
        "holdout_decision_free": True,
        "walk_forward_by_season": {
            season: {k: v for k, v in _metrics(group).items() if k != "reliability"}
            for season, group in sorted(by_season.items())
        },
        "market_benchmark": market_benchmark(store, windows["selection"]),
        "holdout_market_benchmark": market_benchmark(store, windows["holdout"]),
        "availability_basis": sorted(
            {
                r["available_at_basis"]
                for r in store.rows(
                    f"SELECT DISTINCT available_at_basis FROM historical_results "
                    f"WHERE competition IN ({','.join('?' for _ in competitions)})",
                    competitions,
                )
            }
        ),
        "leakage_checked": True,
        "ordering_rule": "prognozy dnia D wyłącznie z wyników dni < D; podział czasowy v2",
        "data_snapshot": data_snapshot(m.result_id for m in matches),
        "code_state": code_state(),
        "data_until": iso(max(m.available_at for m in matches)),
    }
    base = report["baseline_climatology"]["log_loss"]
    report["improves_on_baseline"] = base is not None and decision.get("log_loss", 9) < base
    if register:
        version = f"elo-1x2-v2-{'-'.join(competitions)}-{now:%Y%m%d%H%M}"
        version_id, status, reasons = register_version(
            store,
            family="elo_ordered_draw",
            sport="football",
            description="Elo z marginesem remisu, 1X2 czas regulaminowy",
            version=version,
            created_at=now,
            trained_through=max(m.available_at for m in matches),
            training_data={
                "competitions": list(competitions),
                "data_snapshot": report["data_snapshot"],
                "code_state": report["code_state"],
                "hyperparameters": report["hyperparameters"],
                "split": report["split"],
            },
            validation=report,
            policy=policy,
        )
        report.update(version_id=version_id, status=status, status_reasons=reasons)
    return report


def elo_parameters(validation: dict) -> tuple[float, list[dict], float]:
    """(draw margin, reliability bins, ECE) behind prospective Elo forecasts and intervals.

    v2 versions use the calibration window; v1 versions used the holdout (kept as stored).
    """
    params = validation["hyperparameters"]
    if validation.get("protocol") == "v2":
        source = validation["calibration"]
        return params["draw_margin"], source.get("reliability", []), source.get("ece") or 0.0
    source = validation["holdout"]
    return params["draw_margin_holdout"], source.get("reliability", []), source.get("ece") or 0.0


# ---------------------------------------------------------------- prospective forecasts


def predict_offer(
    store: Store,
    *,
    version_id: str,
    events: list[dict],
    normalizer: NameNormalizer,
    now: datetime,
    team_aliases: dict[tuple[str, str], str] | None = None,
    identities: TeamIdentities | None = None,
) -> dict:
    """1X2 forecasts for upcoming STS football events in covered leagues (pre-match only)."""
    version = store.get("model_versions", version_id)
    if version is None:
        raise ValueError("Nieznana wersja modelu")
    validation = json.loads(version["validation_json"])
    params = validation["hyperparameters"]
    competitions = tuple(validation["competitions"])
    league_for = {
        sts: code
        for code, league in COMPETITIONS.items()
        if code in competitions and league.sport == "football"
        for sts in league.sts_leagues
    }
    rows = effective_results(
        store, sport="football", until=now, competitions=competitions, identities=identities
    )
    matches = [_to_match(r) for r in rows]
    providers = sorted({r["provider_resolved"] for r in rows}, key=priority)
    scoped = _scoped(matches)
    draw_margin, reliability, ece = elo_parameters(validation)
    model = Elo(k=params["k"], home_advantage=params["home_advantage"], draw_margin=draw_margin)
    walk_forward(scoped, model, three_way=True)
    aliases = alias_map(store, team_aliases)
    known = {
        code: known_names([r for r in rows if r["competition"] == code], "football", normalizer)
        for code in competitions
    }
    bins = {(row["low"], row["high"]): row for row in reliability}
    info_cutoff = max(m.available_at for m in matches)
    snapshot = data_snapshot(m.result_id for m in matches)
    state = code_state()
    last_result = {
        code: max((m.match_date for m in matches if m.competition == code), default=None)
        for code in competitions
    }
    saved, kept, considered, skipped, unmatched = 0, 0, 0, [], []
    for event in events:
        code = league_for.get(event.get("league", ""))
        if event.get("sport") != "football" or code is None:
            continue
        considered += 1
        label = f"{event.get('home')} - {event.get('away')}"
        if not event.get("start_at") or datetime.fromisoformat(event["start_at"]) <= now:
            skipped.append(f"{label}: wydarzenie rozpoczęte lub bez godziny — brak prognozy")
            continue
        pair = []
        for side in ("home", "away"):
            found = match_participant(event[side], "football", known[code], normalizer, aliases)
            if found.status in {"exact", "alias"}:
                pair.append(found.target)
            else:
                unmatched.append(
                    {
                        "sts": event[side],
                        "league": code,
                        "status": found.status,
                        "suggestion": found.target,
                        "score": found.score,
                    }
                )
        if len(pair) != 2:
            skipped.append(f"{label}: brak zweryfikowanego dopasowania nazw")
            continue
        event_day = datetime.fromisoformat(event["start_at"]).date()
        last = last_result[code]
        if last is None or (event_day - date.fromisoformat(last)).days > MAX_DATA_GAP_DAYS:
            skipped.append(
                f"{label}: nieaktualne dane niezależne (ostatni wynik w źródle: {last}) — "
                "prognoza oparta na przestarzałych rankingach byłaby niewiarygodna"
            )
            continue
        home, away = (f"{code}|{team}" for team in pair)
        games = (model.games.get(home, 0), model.games.get(away, 0))
        if min(games) < MIN_TEAM_GAMES:
            skipped.append(f"{label}: za mało meczów w historii ({min(games)})")
            continue
        if _already_forecast(store, version_id, event["id"], snapshot):
            kept += 1  # same model and the same data: the stored forecast stays valid
            continue
        forecast = model.probabilities(home, away, three_way=True)
        interval = {}
        for state_key, value in forecast.items():
            row = next((r for (lo, hi), r in bins.items() if lo <= value < hi), None)
            n = row["n"] if row and row["n"] else 1
            half = max(ece, 1.96 * sqrt(value * (1 - value) / n))
            interval[state_key] = [max(0.0, value - half), min(1.0, value + half)]
        save_prediction(
            store,
            model_version_id=version_id,
            event_id=event["id"],
            market_key="1x2",
            period="FT",
            line=None,
            probabilities=forecast,
            uncertainty={"method": "holdout calibration-bin heuristic", "interval": interval},
            inputs={
                "info_cutoff": iso(info_cutoff),
                "data_snapshot": snapshot,
                "code_state": state,
                "teams": pair,
                "competition": code,
                "games": list(games),
                "data": "wyniki as-of: " + ", ".join(providers),
                "last_result": last,
            },
            kind="prospective",
            created_at=now,
        )
        saved += 1
    return {
        "considered": considered,
        "saved": saved,
        "kept": kept,
        "skipped": skipped,
        "unmatched": unmatched,
        "last_result": last_result,
    }


def _already_forecast(store: Store, version_id: str, event_id: str, snapshot: str) -> bool:
    for row in store.rows(
        """SELECT inputs_json FROM predictions WHERE model_version_id=? AND event_id=?
        AND market_key='1x2' AND period='FT' AND kind='prospective'""",
        (version_id, event_id),
    ):
        if json.loads(row["inputs_json"]).get("data_snapshot") == snapshot:
            return True
    return False
