"""Model versions and append-only predictions. Validation status follows the policy only."""

import json
from datetime import datetime

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.probability import validate_probabilities
from sports_edge.storage import Store


def validation_status(validation: dict, policy) -> tuple[str, list[str]]:
    """'validated' only with enough out-of-sample events, calibration and a market benchmark."""
    reasons = []
    if validation.get("n_out_of_sample", 0) < policy.min_validation_events:
        reasons.append(
            f"Za mało prognoz poza próbą: {validation.get('n_out_of_sample', 0)} "
            f"< {policy.min_validation_events}"
        )
    ece = validation.get("ece")
    if ece is None or ece > policy.max_calibration_ece:
        reasons.append(f"Kalibracja ECE={ece} powyżej progu {policy.max_calibration_ece}")
    if policy.require_beats_market_benchmark and validation.get("beats_market") is not True:
        reasons.append("Brak dowodu, że model jest lepszy od rynku na tych samych zdarzeniach")
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
            validation_json=canonical({**validation, "status_reasons": reasons}),
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
    """Predictions are immutable: a new forecast is a new row, history is never rewritten."""
    validate_probabilities(probabilities)
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


def latest_predictions(store: Store, *, as_of: datetime) -> dict[tuple, dict]:
    """Newest prospective prediction per (event, market, period, line) made before ``as_of``."""
    rows = store.rows(
        """SELECT p.*, v.status version_status, v.version, v.validation_json, m.family
        FROM predictions p JOIN model_versions v ON v.id=p.model_version_id
        JOIN models m ON m.id=v.model_id
        WHERE p.kind='prospective' AND p.created_at<=? ORDER BY p.created_at""",
        (iso(as_of),),
    )
    latest: dict[tuple, dict] = {}
    for row in rows:
        row["probabilities"] = json.loads(row["probabilities_json"])
        row["uncertainty"] = json.loads(row["uncertainty_json"])
        row["inputs"] = json.loads(row["inputs_json"])
        row["validation"] = json.loads(row["validation_json"])
        latest[(row["event_id"], row["market_key"], row["period"], row["line"])] = row
    return latest


def model_overview(store: Store) -> list[dict]:
    return store.rows(
        """SELECT m.family, m.sport, m.description, v.version, v.status, v.created_at,
        v.trained_through, v.validation_json FROM model_versions v JOIN models m
        ON m.id=v.model_id ORDER BY v.created_at DESC"""
    )
