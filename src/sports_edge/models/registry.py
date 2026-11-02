"""Model versions and append-only predictions with temporal guards.

* ``validation_status`` derives every piece of evidence itself; a caller cannot declare a
  model "validated" or "better than the market" by passing a flag.
* Prospective predictions must be made before the event starts and must carry the model
  version, information cutoff, data snapshot and code state.
"""

import json
from collections.abc import Sequence
from datetime import datetime

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.probability import validate_probabilities
from sports_edge.storage import Store

REQUIRED_INPUTS = ("info_cutoff", "data_snapshot", "code_state")


def beats_market(benchmark: dict | None, policy) -> tuple[bool, str]:
    """Model beats the market only on the same events, enough of them, lower log loss."""
    if not benchmark:
        return False, "Brak porównania z rynkiem na tych samych wydarzeniach"
    n = benchmark.get("n_events", 0)
    if not benchmark.get("same_events"):
        return False, "Porównanie z rynkiem nie dotyczy tych samych wydarzeń"
    if n < policy.min_validation_events:
        return False, f"Za mało wspólnych wydarzeń z rynkiem: {n} < {policy.min_validation_events}"
    model_ll, market_ll = benchmark.get("model_log_loss"), benchmark.get("market_log_loss")
    if model_ll is None or market_ll is None or model_ll >= market_ll:
        return False, f"Log loss modelu {model_ll} nie jest niższy niż rynku {market_ll}"
    return True, "Model ma niższy log loss niż rynek na tych samych wydarzeniach"


def validation_status(validation: dict, policy) -> tuple[str, list[str]]:
    """'validated' requires out-of-sample size and calibration, a market benchmark and as-of
    data. Protocol v2 (TASK 007) judges the selection window; its final holdout never
    decides. Protocol v1 versions judged their holdout and keep that stored status."""
    reasons = []
    if validation.get("protocol") == "v2":
        sample, where, of = validation.get("decision") or {}, "w oknie selekcji", "okna selekcji"
    else:
        sample, where, of = validation.get("holdout") or {}, "w holdoucie", "holdoutu"
    n = sample.get("n", 0)
    if n < policy.min_validation_events:
        reasons.append(f"Za mało prognoz {where}: {n} < {policy.min_validation_events}")
    ece = sample.get("ece")
    if ece is None or ece > policy.max_calibration_ece:
        reasons.append(f"Kalibracja {of} ECE={ece} powyżej progu {policy.max_calibration_ece}")
    if policy.require_beats_market_benchmark:
        ok, why = beats_market(validation.get("market_benchmark"), policy)
        if not ok:
            reasons.append(why)
    if validation.get("leakage_checked") is not True:
        reasons.append("Brak potwierdzonej kontroli wycieku danych (as-of)")
    return ("validated" if not reasons else "experimental"), reasons


def register_version(
    store: Store,
    *,
    family: str,
    sport: str,
    description: str,
    version: str,
    created_at: datetime,
    trained_through: datetime | None,
    training_data: dict,
    validation: dict,
    policy,
) -> tuple[str, str, list[str]]:
    model_id = digest(["model", family, sport])
    status, reasons = validation_status(validation, policy)
    derived, _ = beats_market(validation.get("market_benchmark"), policy)
    stored = {**validation, "beats_market": derived, "status_reasons": reasons}
    version_id = digest(["model_version", model_id, version])
    with store.transaction():
        store.insert("models", id=model_id, family=family, sport=sport, description=description)
        store.insert(
            "model_versions",
            id=version_id,
            model_id=model_id,
            version=version,
            created_at=iso(created_at),
            trained_through=iso(trained_through) if trained_through else None,
            training_data_json=canonical(training_data),
            validation_json=canonical(stored),
            status=status,
        )
    return version_id, status, reasons


def save_prediction(
    store: Store,
    *,
    model_version_id: str,
    event_id: str | None,
    market_key: str,
    period: str,
    line: str | None,
    probabilities: dict[str, float],
    uncertainty: dict,
    inputs: dict,
    kind: str,
    created_at: datetime,
) -> str:
    """Immutable forecast. Prospective ones are refused at or after the event start."""
    validate_probabilities(probabilities)
    if kind == "prospective":
        if event_id is None:
            raise ValueError("Prognoza prospektywna wymaga wydarzenia")
        event = store.get("events", event_id)
        if event is None or not event["start_at"]:
            raise ValueError("Nieznany czas rozpoczęcia — prognoza przedmeczowa niemożliwa")
        if created_at >= datetime.fromisoformat(event["start_at"]):
            raise ValueError("Wydarzenie już się rozpoczęło — odmowa prognozy przedmeczowej")
        missing = [key for key in REQUIRED_INPUTS if not inputs.get(key)]
        if missing:
            raise ValueError(f"Brak wersjonowania prognozy: {', '.join(missing)}")
        if datetime.fromisoformat(inputs["info_cutoff"]) > created_at:
            raise ValueError("Dane wejściowe z przyszłości względem chwili prognozy")
    if store.get("model_versions", model_version_id) is None:
        raise ValueError("Nieznana wersja modelu")
    key = digest(
        ["prediction", model_version_id, event_id, market_key, period, line, iso(created_at)]
    )
    with store.transaction():
        store.insert(
            "predictions",
            id=key,
            model_version_id=model_version_id,
            event_id=event_id,
            market_key=market_key,
            period=period,
            line=line,
            created_at=iso(created_at),
            probabilities_json=canonical(probabilities),
            uncertainty_json=canonical(uncertainty),
            inputs_json=canonical(inputs),
            kind=kind,
        )
    return key


def latest_predictions(
    store: Store,
    *,
    as_of: datetime,
    prefer: dict[str, str] | None = None,
    versions: set[str] | None = None,
    offer_dates: Sequence[str] | None = None,
) -> dict[tuple, dict]:
    """Newest pre-match prediction per (event, market, period, line).

    Only prospective forecasts created no later than ``as_of`` and strictly before the event
    start qualify; anything made after kick-off is never a pre-match forecast. ``prefer``
    maps a market to the model family chosen for it on validation (e.g. 1X2 → Elo or
    Dixon–Coles); a newer forecast of another family never replaces the preferred one.
    ``versions`` keeps only the served versions (an experiment is not a release) and
    ``offer_dates`` only events of those STS offer days; both are filtered in SQL.
    """
    where = ["p.kind='prospective'", "p.created_at<=?", "e.start_at IS NOT NULL"]
    params: list = [iso(as_of)]
    if versions is not None:
        where.append(f"p.model_version_id IN ({','.join('?' for _ in versions)})")
        params += sorted(versions)
    if offer_dates is not None:
        where.append(f"e.offer_date IN ({','.join('?' for _ in offer_dates)})")
        params += list(offer_dates)
    rows = store.rows(
        f"""SELECT p.*, v.status version_status, v.version, m.family, e.start_at event_start
        FROM predictions p JOIN model_versions v ON v.id=p.model_version_id
        JOIN models m ON m.id=v.model_id JOIN events e ON e.id=p.event_id
        WHERE {" AND ".join(where)} ORDER BY p.created_at""",
        tuple(params),
    )
    validations: dict[str, dict] = {}  # one parse per version, not per forecast
    latest: dict[tuple, dict] = {}
    for row in rows:
        if datetime.fromisoformat(row["created_at"]) >= datetime.fromisoformat(row["event_start"]):
            continue
        version_id = row["model_version_id"]
        if version_id not in validations:
            validations[version_id] = json.loads(
                store.get("model_versions", version_id)["validation_json"]
            )
        row["probabilities"] = json.loads(row["probabilities_json"])
        row["uncertainty"] = json.loads(row["uncertainty_json"])
        row["inputs"] = json.loads(row["inputs_json"])
        row["validation"] = validations[version_id]
        key = (row["event_id"], row["market_key"], row["period"], row["line"])
        competition = row["inputs"].get("competition")
        wanted = (prefer or {}).get((row["market_key"], competition)) or (prefer or {}).get(
            row["market_key"]
        )
        current = latest.get(key)
        if wanted and current is not None and current["family"] == wanted != row["family"]:
            continue
        latest[key] = row
    return latest


def model_overview(store: Store) -> list[dict]:
    return store.rows(
        """SELECT v.id version_id, m.family, m.sport, m.description, v.version, v.status,
        v.created_at, v.trained_through, v.training_data_json, v.validation_json
        FROM model_versions v JOIN models m ON m.id=v.model_id ORDER BY v.created_at DESC"""
    )
