"""Independent sports data engine: user-supplied, licensed historical results only.

Nothing is downloaded automatically and no statistic is synthesised. When a result file has
no publication timestamp, a result is treated as known 36 hours after midnight UTC of the
match date (conservative as-of rule, documented in docs/task_002_report.md).
"""

import csv
import hashlib
import io
import json
from datetime import UTC, date, datetime, time, timedelta
from math import sqrt

from sports_edge.analysis.policy import Policy
from sports_edge.ingestion.types import digest, iso
from sports_edge.models import calibration
from sports_edge.models.elo import Elo, HistoricalMatch, fit_draw_margin, rating_differences
from sports_edge.models.registry import register_version, save_prediction
from sports_edge.offers.text import NameNormalizer
from sports_edge.storage import Store

REQUIRED = ("date", "home", "away", "home_score", "away_score")
AVAILABILITY_LAG = timedelta(hours=36)
MIN_MATCHES = 200
MIN_TEAM_GAMES = 10


def _parse_date(value: str) -> date:
    value = value.strip()
    if "." in value:
        day, month, year = value.split(".")[:3]
        return date(int(year), int(month), int(day))
    return date.fromisoformat(value[:10])


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
) -> dict:
    if not license_note.strip():
        raise ValueError("Podaj źródło i podstawę prawną użycia danych")
    text = data.decode("utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = {c.strip().lower() for c in reader.fieldnames or []}
    missing = [c for c in REQUIRED if c not in columns]
    if missing:
        raise ValueError(f"Brak kolumn: {', '.join(missing)}")
    sha = hashlib.sha256(data).hexdigest()
    import_id = digest(["data_import", sha, "results"])
    if store.get("data_imports", import_id):
        return {"import_id": import_id, "duplicate": True, "inserted": 0, "errors": []}
    rows, errors = [], []
    for number, raw in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        try:
            played = _parse_date(row["date"])
            home_score, away_score = int(row["home_score"]), int(row["away_score"])
            if home_score < 0 or away_score < 0 or not row["home"] or row["home"] == row["away"]:
                raise ValueError("invalid score or teams")
            if row.get("available_at"):
                available = datetime.fromisoformat(row["available_at"].replace("Z", "+00:00"))
                if available.tzinfo is None:
                    raise ValueError("available_at needs a UTC offset")
            else:
                available = datetime.combine(played, time(0), UTC) + AVAILABILITY_LAG
            if available > now:
                raise ValueError("result not yet available at import time")
        except (ValueError, KeyError) as error:
            errors.append(f"wiersz {number}: {error}")
            continue
        rows.append((played, row, home_score, away_score, available))
    with store.transaction():
        store.insert(
            "data_imports",
            id=import_id,
            source_name=source_name,
            license_note=license_note,
            file_sha256=sha,
            file_name=file_name,
            kind="results",
            rows=len(rows),
            ingested_at=iso(now),
        )
        inserted = 0
        for played, row, home_score, away_score, available in rows:
            inserted += store.insert(
                "historical_results",
                id=digest(
                    ["hist", sport, competition, played.isoformat(), row["home"], row["away"]]
                ),
                import_id=import_id,
                sport=sport,
                competition=competition,
                season=row.get("season") or None,
                match_date=played.isoformat(),
                home=row["home"],
                away=row["away"],
                home_score=home_score,
                away_score=away_score,
                available_at=iso(available),
            )
    return {"import_id": import_id, "duplicate": False, "inserted": inserted, "errors": errors}


def history(store: Store, *, sport: str, until: datetime, competition: str | None = None):
    sql = "SELECT * FROM historical_results WHERE sport=? AND available_at<=?"
    params: list = [sport, iso(until)]
    if competition:
        sql += " AND competition=?"
        params.append(competition)
    return [
        HistoricalMatch(
            available_at=datetime.fromisoformat(r["available_at"]),
            match_date=r["match_date"],
            home=r["home"],
            away=r["away"],
            home_score=r["home_score"],
            away_score=r["away_score"],
        )
        for r in store.rows(sql + " ORDER BY available_at", tuple(params))
    ]


def coverage(store: Store) -> list[dict]:
    return store.rows(
        """SELECT sport, competition, COUNT(*) matches, MIN(match_date) first,
        MAX(match_date) last, COUNT(DISTINCT home) home_teams FROM historical_results
        GROUP BY sport, competition ORDER BY matches DESC"""
    )


def evaluate_elo(matches: list[HistoricalMatch], *, train_fraction: float = 0.6) -> dict:
    """Draw margin fitted on the first part; forecasts scored only on the later part."""
    if len(matches) < MIN_MATCHES:
        raise ValueError(f"Za mało meczów: {len(matches)} < {MIN_MATCHES}")
    ordered = sorted(matches, key=lambda m: (m.available_at, m.home, m.away))
    cut = int(len(ordered) * train_fraction)
    margin = fit_draw_margin(rating_differences(ordered[:cut], Elo()))
    model = Elo(draw_margin=margin)
    forecasts, outcomes = [], []
    climatology = {k: 0 for k in ("home", "draw", "away")}
    for match in ordered[:cut]:
        climatology[match.outcome] += 1
    base = {k: v / cut for k, v in climatology.items()}
    for index, match in enumerate(ordered):
        forecast = model.probabilities(match.home, match.away, three_way=True)
        if (
            index >= cut
            and min(model.games.get(match.home, 0), model.games.get(match.away, 0))
            >= MIN_TEAM_GAMES
        ):
            forecasts.append(forecast)
            outcomes.append(match.outcome)
        model.update(match)
    if not forecasts:
        raise ValueError("Brak prognoz poza próbą po filtrze doświadczenia drużyn")
    p, y = calibration.one_vs_rest(forecasts, outcomes)
    return {
        "n_out_of_sample": len(forecasts),
        "train_matches": cut,
        "train_until": iso(ordered[cut - 1].available_at),
        "draw_margin": margin,
        "log_loss": round(calibration.log_loss(forecasts, outcomes), 5),
        "brier": round(calibration.brier(forecasts, outcomes), 5),
        "ece": round(calibration.ece(p, y), 5),
        "climatology_log_loss": round(calibration.log_loss([base] * len(outcomes), outcomes), 5),
        "reliability": calibration.reliability(p, y),
        "beats_market": None,  # no licensed historical odds on the same events yet
        "leakage_checked": True,  # forecasts use ratings updated only with earlier results
    }


def train_baseline(
    store: Store, *, sport: str, competition: str, now: datetime, policy: Policy
) -> dict:
    matches = history(store, sport=sport, competition=competition, until=now)
    validation = evaluate_elo(matches)
    version = f"elo-1x2-{now:%Y%m%d%H%M}"
    version_id, status, reasons = register_version(
        store,
        family="elo_ordered_draw",
        sport=sport,
        description=f"Elo z marginesem remisu, {competition}",
        version=version,
        created_at=now,
        trained_through=matches[-1].available_at,
        training_data={"competition": competition, "matches": len(matches)},
        validation=validation,
        policy=policy,
    )
    return {"version_id": version_id, "status": status, "reasons": reasons, **validation}


def predict_events(
    store: Store,
    *,
    version_id: str,
    sport: str,
    competition: str,
    events: list[dict],
    normalizer: NameNormalizer,
    now: datetime,
) -> dict:
    """Prospective 1X2 forecasts for offer events whose teams have enough history."""
    version = store.get("model_versions", version_id)
    if version is None:
        raise ValueError("Unknown model version")
    validation = json.loads(version["validation_json"])
    model = Elo(draw_margin=validation["draw_margin"])
    names: dict[str, str] = {}
    for match in history(store, sport=sport, competition=competition, until=now):
        model.update(match)
        names.setdefault(normalizer.key(match.home), match.home)
        names.setdefault(normalizer.key(match.away), match.away)
    bins = {(row["low"], row["high"]): row for row in validation["reliability"]}
    saved, skipped = 0, []
    for event in events:
        if event["sport"] != sport or not event.get("home") or not event.get("away"):
            continue
        home = names.get(normalizer.key(event["home"]))
        away = names.get(normalizer.key(event["away"]))
        if (
            not home
            or not away
            or min(model.games.get(home, 0), model.games.get(away, 0)) < MIN_TEAM_GAMES
        ):
            skipped.append(f"{event['home']} - {event['away']}: brak wystarczającej historii")
            continue
        forecast = model.probabilities(home, away, three_way=True)
        interval = {}
        for state, value in forecast.items():
            row = next((r for (lo, hi), r in bins.items() if lo <= value < hi), None)
            n = row["n"] if row and row["n"] else 1
            half = max(validation["ece"], 1.96 * sqrt(value * (1 - value) / n))
            interval[state] = [max(0.0, value - half), min(1.0, value + half)]
        save_prediction(
            store,
            model_version_id=version_id,
            event_id=event["id"],
            market_key="1x2",
            period="FT",
            line=None,
            probabilities=forecast,
            uncertainty={"method": "calibration-bin heuristic", "interval": interval},
            inputs={
                "history_until": iso(now),
                "games": [model.games[home], model.games[away]],
                "data": "historical_results (as-of)",
            },
            kind="prospective",
            created_at=now,
        )
        saved += 1
    return {"saved": saved, "skipped": skipped}
