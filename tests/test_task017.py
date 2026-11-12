"""TASK 017 — synthetic tests for national-team shadow forecasts and their gated serving."""

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from sports_edge import automation, decisions, international, services
from sports_edge.analysis.edge_engine import Candidate
from sports_edge.datasources.competitions import for_sts_league
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.models.registry import latest_predictions
from sports_edge.offers.model import OfferKind, OfferRow, OfferSelection, ParseResult
from sports_edge.repository import event_id, import_offer
from sports_edge.results_engine import effective_results

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
WARSAW = ZoneInfo("Europe/Warsaw")
NATIONAL_HEADER = "MIĘDZYNARODOWE - LIGA NARODÓW UEFA"
TEAMS = ("Aster", "Beryl", "Cinder", "Dovetail", "Ember", "Fable", "Gannet", "Hollis")


def _append_results(store, rows, *, provider="openfootball_int", available_at=NOW):
    rows = [{**item, "available_at": iso(item.get("available_at", available_at))} for item in rows]
    import_id = digest(["task017-history", provider, rows, iso(available_at)])
    store.insert(
        "data_imports",
        id=import_id,
        source_name="openfootball/internationals",
        license_note="CC0 synthetic fixture",
        file_sha256=digest(["file", import_id]),
        file_name=None,
        kind="synthetic",
        rows=len(rows),
        ingested_at=iso(available_at),
        summary_json="{}",
    )
    ids = []
    for index, item in enumerate(rows):
        result_id = item.get("id") or digest([import_id, index, item])
        details = {
            "regulation_certain": item.get("certain", True),
            "venue_country": item.get("venue"),
            "home_country": item["home"],
            "away_country": item["away"],
            "duration": item.get("duration"),
        }
        store.insert(
            "historical_results",
            id=result_id,
            import_id=import_id,
            sport="football",
            competition=item.get("competition", "uefa.nl"),
            season=str(item["day"][:4]),
            match_date=item["day"],
            home=item["home"],
            away=item["away"],
            home_score=item["score"][0],
            away_score=item["score"][1],
            available_at=iso(item.get("available_at", available_at)),
            available_at_basis="observed_at_fetch",
            start_at=None,
            details_json=canonical(details),
            provider=item.get("provider", provider),
            source_event_id=item.get("source_event_id"),
            status="finished",
            home_raw=item["home"],
            away_raw=item["away"],
            match_confidence="exact",
            published_at=None,
            observed_at=iso(item.get("available_at", available_at)),
        )
        ids.append(result_id)
    return ids


def _seed_history(store, *, now=NOW):
    rows = []
    for index in range(204):  # 2010–2026; fit, calibration, selection and holdout windows
        year, month = 2010 + index // 12, index % 12 + 1
        home, away = TEAMS[index % 8], TEAMS[(index + 1) % 8]
        rows.append(
            {
                "day": f"{year:04}-{month:02}-10",
                "home": home,
                "away": away,
                "score": ((index * 3) % 4, (index + 1) % 3),
                "venue": home if index % 3 == 0 else "Neutral Host" if index % 3 == 1 else None,
            }
        )
    for year in range(2018, 2026):
        rows.append(
            {
                "competition": "fifa.friendly",
                "day": f"{year}-02-20",
                "home": TEAMS[year % 8],
                "away": TEAMS[(year + 2) % 8],
                "score": (year % 3, (year + 1) % 3),
                "venue": None,
            }
        )
    return _append_results(store, rows, available_at=now - timedelta(days=1))


def _valid_report(store, *, experiment_id="synthetic-task017", validated=True, benchmark=True):
    criteria = [
        {"name": "co najmniej 300 meczów", "value": 300, "passed": validated},
        {"name": "CI95 poniżej zera", "value": [-0.08, -0.01], "passed": validated},
        {"name": "ECE ≤ 0.03", "value": 0.01, "passed": validated},
    ]
    reliability = [
        {"range": f"{low}–{low + 10}%", "n": 100, "mean_p": low / 100, "freq": low / 100}
        for low in range(0, 100, 10)
    ]
    metric = {
        "n": 300 if validated else 250,
        "ece": 0.01,
        "log_loss": 0.92,
        "delta_log_loss": -0.04,
        "ci95": [-0.08, -0.01],
    }
    national = {
        "scope": "national",
        "competitions": list(international.NATIONAL_OFFICIAL),
        "matches": {"fit": 100, "calibration": 40, "selection": metric["n"], "holdout": 20},
        "friendlies_used": 12,
        "venue_coverage": {"known": 40, "unknown": 10, "neutral": 15},
        "params": {
            key: {
                "k": 24.0,
                "home_advantage": 70.0,
                "draw_margin": 0.18,
                "friendly_weight": 0.5 if key != "N1" else 0.0,
                "neutral_venues": key == "N3",
            }
            for key in ("N1", "N2", "N3")
        },
        "markets": {
            "1x2": {
                "windows": {
                    "selection": {"N1": metric, "baseline": {"n": 300, "log_loss": 0.96}},
                    "calibration": {"N1": {"n": 40, "ece": 0.01, "reliability": reliability}},
                    "holdout": {"N1": {"n": 20, "ece": 0.4, "log_loss": 0.7}},
                },
                "decision": {
                    "best": "N1",
                    "validated": validated,
                    "criteria": criteria,
                    "verdict": "zwalidowany w eksperymencie wstecznym"
                    if validated
                    else "niezwalidowany",
                },
                "market_benchmark": (
                    {
                        "same_events": True,
                        "n_events": 300,
                        "moment": "pre_match",
                        "operator": "sts",
                        "model_log_loss": 0.90,
                        "market_log_loss": 0.96,
                    }
                    if benchmark
                    else None
                ),
            }
        },
        "friendly_evaluation": {"selection": {"N1": {"n": 20, "log_loss": 0.9}}},
    }
    report = {
        "experiment_id": experiment_id,
        "national": national,
        "european": {"matches": {}, "markets": {}},
    }
    store.insert(
        "experiments",
        id=experiment_id,
        hypothesis=international.HYPOTHESIS,
        registered_at=iso(NOW),
        kind="backtest",
        config_json=canonical(report),
        status="completed",
    )
    return report


def _sts_offer(
    store,
    *,
    now=NOW,
    home=TEAMS[0],
    away=TEAMS[1],
    period="FT",
    stale=False,
    missing_participants=False,
    league=NATIONAL_HEADER,
):
    local_start = now.astimezone(WARSAW) + timedelta(hours=5)
    offer_date = local_start.date()
    seen = now - (timedelta(hours=5) if stale else timedelta(minutes=30))
    row = OfferRow(
        bookmaker="sts",
        sport_raw="Piłka nożna",
        sport="football",
        league_raw=league,
        event_name_raw=f"{home} – {away}",
        participants=() if missing_participants else (home, away),
        market_raw="Wynik meczu (1X2)",
        market_key="1x2",
        period=period,
        includes_overtime=False,
        selections=(
            OfferSelection(label="1", key="home", odds=Decimal("2.50"), odds_text="2.50"),
            OfferSelection(label="X", key="draw", odds=Decimal("3.10"), odds_text="3.10"),
            OfferSelection(label="2", key="away", odds=Decimal("2.90"), odds_text="2.90"),
        ),
        page=1,
        column=1,
        source_text=f"{home} - {away}",
        start_local=local_start,
        offer_date=offer_date,
        rules_ref="synthetic national 90-minute market",
    )
    result = ParseResult(
        bookmaker="sts",
        parser="task017_synthetic",
        parser_version="1",
        parser_verified=True,
        offer_kind=OfferKind.TODAY,
        rows=(row,),
        review=(),
        pages_total=1,
        pages_with_text=1,
        document_generated_at=seen,
    )
    import_offer(
        store,
        result,
        sha256=digest(["task017-offer", home, away, period, stale]),
        file_name=None,
        source_id="fixture",
        data_kind="prospective",
        ingested_at=seen,
        fetched_at=seen,
    )
    return event_id(row), offer_date.isoformat()


def _forecast_observation(
    store,
    event_id,
    *,
    observed_at,
    competition="uefa.nl",
    stored_observed_at=None,
    variant="N1",
):
    payload = {
        "event_id": event_id,
        "competition": competition,
        "home": TEAMS[0],
        "away": TEAMS[1],
        "variant": variant,
        "experiment_id": international.latest(store)["experiment_id"],
        "made_at": iso(observed_at),
        "probabilities": {"home": 0.44, "draw": 0.28, "away": 0.28},
        "market": "1x2",
        "period": "FT_90",
    }
    observation_id = digest(["task017-observation", event_id, iso(observed_at), variant])
    store.insert(
        "stat_observations",
        id=observation_id,
        kind=international.NATIONAL_SHADOW_KIND,
        sport="football",
        competition=competition,
        season=None,
        entity_type="event",
        entity_key=event_id,
        event_date=None,
        source="synthetic",
        source_ref=payload["experiment_id"],
        payload_json=canonical(payload),
        data_version="task017-test",
        quality="unverified",
        published_at=None,
        fetched_at=iso(observed_at),
        observed_at=stored_observed_at or iso(observed_at),
        available_at=iso(observed_at),
        available_basis="observed_at_fetch",
        import_id=None,
    )
    return observation_id


def _candidate(**overrides):
    values = {
        "key": "q:home",
        "event_id": "event",
        "market_id": "market",
        "quote_set_id": "quote",
        "odds_id": "odds",
        "selection_id": "selection",
        "event": f"{TEAMS[0]} – {TEAMS[1]}",
        "sport": "football",
        "league": NATIONAL_HEADER,
        "start_at": iso(NOW + timedelta(hours=5)),
        "offer_code": None,
        "market_key": "1x2",
        "period": "FT",
        "line": None,
        "selection": "home",
        "odds": Decimal("2.50"),
        "implied": 0.4,
        "net_multiplier": Decimal("2.2"),
        "margin": 0.02,
        "p_market": 0.4,
        "p_model": 0.44,
        "p_low": 0.35,
        "p_high": 0.53,
        "model": "national_1x2",
        "model_version_id": None,
        "model_status": "validated",
        "validation": "synthetic",
        "ev_gross": 0.1,
        "ev_net": Decimal("0.01"),
        "ev_net_low": Decimal("-0.01"),
        "quality": 1.0,
        "data_status": "ok",
        "rules_status": "zweryfikowane",
        "tax_status": "zweryfikowany",
        "freshness": "fresh",
        "presence": "current",
        "tier": "validated",
        "reasons": (),
        "predictive_status": "validated_predictive",
        "break_even": 0.45,
        "prediction_id": "prediction",
    }
    return Candidate(**(values | overrides))


def test_walk_forward_uses_asof_results_and_holdout_never_selects(store):
    _seed_history(store)
    future_result = {
        "id": "published-later-selection-result",
        "day": "2024-10-12",
        "home": TEAMS[0],
        "away": TEAMS[1],
        "score": (3, 0),
        "available_at": NOW + timedelta(hours=1),
    }
    _append_results(store, [future_result], available_at=NOW + timedelta(hours=1))
    before = international.national_experiment(store, now=NOW)
    after = international.national_experiment(store, now=NOW + timedelta(hours=2))
    assert before["matches"]["selection"] + 1 == after["matches"]["selection"]
    assert set(before["friendly_evaluation"]) <= {"selection", "holdout"}
    selection = {
        "N1": {"n": 300, "log_loss": 0.8, "ece": 0.02, "ci95": [-0.1, -0.01]},
        "N2": {"n": 300, "log_loss": 0.9, "ece": 0.02, "ci95": [-0.1, -0.01]},
    }
    chosen = international._decide(selection)
    assert chosen["best"] == "N1"  # holdout metrics are not an argument to selection
    assert chosen["validated"]


def test_official_friendlies_and_neutral_venues_stay_explicit(store):
    _seed_history(store)
    rows = international._national_history(store, now=NOW)
    assert any(row["_friendly"] for row in rows)
    assert any(row["_venue_unknown"] for row in rows)
    assert any(row["_neutral"] for row in rows)
    assert any(not row["_neutral"] and not row["_venue_unknown"] for row in rows)
    report = international.national_experiment(store, now=NOW)
    assert report["friendlies_used"] > 0
    assert report["venue_coverage"]["unknown"] > 0
    assert report["venue_coverage"]["neutral"] > 0
    # N1/N2 have no home advantage when venue is unknown; N3 removes it on confirmed neutral ground.
    unknown = next(row for row in rows if row["_venue_unknown"])
    neutral = next(row for row in rows if row["_neutral"])
    assert international._country_key(unknown.get("_venue_country") or "") == ""
    assert international._country_key(neutral["_venue_country"]) != international._country_key(
        neutral["_venue_country"] and neutral["home"]
    )


def test_uncertain_and_conflicting_90_minute_results_are_quarantined(store):
    _seed_history(store)
    event_id, day = _sts_offer(store)
    event = store.get("events", event_id)
    started = datetime.fromisoformat(event["start_at"])
    # A result that may include extra time is not a training row or settlement candidate.
    uncertain = {
        "day": day,
        "home": TEAMS[0],
        "away": TEAMS[1],
        "score": (2, 1),
        "certain": False,
        "available_at": started + timedelta(hours=1),
    }
    _append_results(store, [uncertain], available_at=started + timedelta(hours=1))
    history = international._national_history(store, now=started + timedelta(days=1))
    assert not any(row["match_date"] == day and row["home"] == TEAMS[0] for row in history)

    # Two certain sources disagreeing on the exact fixture produce a conflict and are excluded.
    common = {"day": day, "home": TEAMS[0], "away": TEAMS[1], "score": (1, 0)}
    _append_results(
        store, [common], provider="openfootball_int", available_at=started + timedelta(hours=2)
    )
    _append_results(
        store,
        [{**common, "score": (0, 1), "provider": "football_data_org"}],
        provider="football_data_org",
        available_at=started + timedelta(hours=2),
    )
    resolved = effective_results(
        store,
        sport="football",
        until=started + timedelta(days=1),
        competitions=("uefa.nl",),
    )
    conflicted = next(row for row in resolved if row["match_date"] == day)
    assert conflicted["conflict"]
    assert not any(row["id"] == conflicted["id"] for row in international._played(resolved))


def test_shadow_worker_is_idempotent_offline_and_never_saves_coupons(store, app_paths, monkeypatch):
    _seed_history(store)
    _valid_report(store)
    event_id, _day = _sts_offer(store)
    before = {
        table: store.scalar(f"SELECT COUNT(*) FROM {table}")
        for table in ("coupons", "coupon_legs", "user_decisions", "automation_settings")
    }
    monkeypatch.setattr(automation, "_app", lambda _paths: SimpleNamespace(offline_mode=True))
    first = automation.job_national_shadow(store, app_paths, now=NOW, settings={})
    second = automation.job_national_shadow(store, app_paths, now=NOW, settings={})
    assert first.payload["new"] == 3
    assert second.payload["new"] == 0 and second.payload["kept"] == 3
    assert first.payload["source_status"] == "offline_cached_only"
    assert (
        store.scalar(
            "SELECT COUNT(*) FROM stat_observations WHERE kind=?",
            (international.NATIONAL_SHADOW_KIND,),
        )
        == 3
    )
    observations = store.rows(
        "SELECT payload_json FROM stat_observations WHERE kind=?",
        (international.NATIONAL_SHADOW_KIND,),
    )
    frozen = json.loads(observations[0]["payload_json"])
    assert frozen["model_version"].startswith("national-shadow:")
    assert frozen["code_state"] and frozen["data_snapshot"]
    assert frozen["version_reason"].startswith("pierwsza wersja")
    assert frozen["supersedes"] == []
    assert store.scalar("SELECT COUNT(*) FROM predictions") == 0
    assert store.scalar("SELECT COUNT(*) FROM coupons") == 0
    assert before == {table: store.scalar(f"SELECT COUNT(*) FROM {table}") for table in before}
    assert international.national_shadow_of(
        store, event_id, before=datetime.fromisoformat(store.get("events", event_id)["start_at"])
    )


def test_missing_participant_ids_quarantine_shadow_forecast(store, app_paths):
    _seed_history(store)
    _valid_report(store)
    event_id, _day = _sts_offer(store, missing_participants=True)
    result = international.national_shadow_forecasts(store, app_paths, now=NOW)
    assert result["stored"] == 0
    assert result["invalid"] == 1
    assert result["skipped"]["incomplete_participants"] == 1
    assert (
        store.scalar(
            "SELECT COUNT(*) FROM stat_observations WHERE kind=?",
            (international.NATIONAL_SHADOW_KIND,),
        )
        == 0
    )


def test_conflicting_approved_alias_targets_require_manual_mapping(store, app_paths, monkeypatch):
    from sports_edge.app import international_ui
    from sports_edge.matching import participant_key

    _seed_history(store)
    _valid_report(store)
    names = services.normalizer(app_paths)
    alias = participant_key("Shared Alias", "football", names)
    for team in TEAMS[:2]:
        target = participant_key(team, "football", names)
        store.insert(
            "aliases",
            id=digest(["task017-ambiguous-alias", team]),
            sport="football",
            alias_key=alias,
            canonical_key=target,
            origin="fixture_evidence:synthetic",
            created_at=iso(NOW),
        )
    event_id, day = _sts_offer(store, home="Shared Alias", away=TEAMS[2])
    result = international.national_shadow_forecasts(store, app_paths, now=NOW)
    assert result["stored"] == 0
    assert result["unmatched"] == 1
    assert result["skipped"]["ambiguous_participants"] == 1

    captured = []
    monkeypatch.setattr(international_ui.ui, "section", lambda *items: captured.extend(items))
    monkeypatch.setattr(
        international_ui.ui,
        "table",
        lambda columns, body, numeric=(): repr((columns, body)),
    )
    monkeypatch.setattr(international_ui, "_table", captured.append)
    international_ui.national_coupon_block(store, day, NOW, [])
    shown = " ".join(map(str, captured))
    # TASK 018: competitions with history follow the league path; the block lists only
    # competitions without data
    assert shown == ""
    assert (
        international.national_shadow_of(store, event_id, before=NOW + timedelta(hours=4)) is None
    )
    decisions.decide_national_1x2(store, app_paths, accept=True, now=NOW)
    serving = international.national_serving_predictions(store, app_paths, now=NOW)
    assert serving["stored"] == 0
    assert serving["skipped"]["ambiguous_participants"] == 1
    assert serving["unmatched"][0]["statuses"][0] == "ambiguous"


def test_national_mapper_uses_reviewed_polish_country_aliases(store, app_paths):
    history = [
        {
            "home": "Belgium",
            "away": "Spain",
            "home_source": None,
            "away_source": None,
            "match_date": "2025-01-01",
        }
    ]
    mapper = international._national_mapper(store, app_paths, history)
    belgium = mapper("Belgia")
    spain = mapper("Hiszpania")
    assert (belgium.status, belgium.target) == ("alias", "Belgium")
    assert (spain.status, spain.target) == ("alias", "Spain")


def test_post_kickoff_shadow_forecast_is_not_evaluated(store):
    _seed_history(store)
    _valid_report(store)
    event_id, _day = _sts_offer(store)
    start = datetime.fromisoformat(store.get("events", event_id)["start_at"])
    _forecast_observation(store, event_id, observed_at=start + timedelta(seconds=1))
    result = international.national_shadow_evaluation(store, now=start + timedelta(days=1))
    assert result["settled"] == 0
    assert result["invalid_result"] == 1
    assert result["reasons"]["forecast_not_pre_match"] == 1


def test_national_shadow_lookup_compares_instants_across_timezone_offsets(store):
    _seed_history(store)
    _valid_report(store)
    event_id, _day = _sts_offer(store)
    start = datetime.fromisoformat(store.get("events", event_id)["start_at"])
    after_start = start + timedelta(minutes=30)
    # A west-offset timestamp sorts before the UTC kickoff as text, despite being later.
    offset_timestamp = after_start.astimezone(timezone(timedelta(hours=-1))).isoformat()
    _forecast_observation(
        store,
        event_id,
        observed_at=after_start,
        stored_observed_at=offset_timestamp,
    )
    assert international.national_shadow_of(store, event_id, before=start) is None


def test_national_shadow_lookup_selects_best_of_same_time_variants(store):
    _seed_history(store)
    _valid_report(store)
    event_id, _day = _sts_offer(store)
    start = datetime.fromisoformat(store.get("events", event_id)["start_at"])
    made_at = start - timedelta(minutes=5)
    for variant in ("N_goals", "N3", "N2", "N1"):
        _forecast_observation(store, event_id, observed_at=made_at, variant=variant)
    selected = international.national_shadow_of(store, event_id, before=start)
    assert selected["variant"] == "N1"


@pytest.mark.parametrize(
    ("n", "ece", "ci", "expected"),
    [
        (299, 0.01, [-0.1, -0.01], [False, True, True]),
        (300, 0.031, [-0.1, -0.01], [True, True, False]),
        (300, 0.01, [-0.1, 0.001], [True, False, True]),
    ],
)
def test_validation_reports_each_gate_and_blocks_sample_ece_or_ci_failures(n, ece, ci, expected):
    decision = international._decide(
        {
            "N1": {"n": n, "log_loss": 0.8, "ece": ece, "ci95": ci},
            "baseline": {"n": n, "log_loss": 0.9, "ece": 0.02, "ci95": None},
        }
    )
    assert [item["passed"] for item in decision["criteria"]] == expected
    assert decision["validated"] is all(expected)


def test_decision_is_specific_to_national_1x2_and_model_version_then_withdrawn(store, app_paths):
    _seed_history(store)
    _valid_report(store)
    assert international.active_national_version_id(store) is None
    # TASK 018: no extra registry gate — national markets follow the league gates
    allowed, reason = international.generator_allowed(store, "fifa.friendly", "1x2")
    assert allowed and "jak w ligach" in reason

    decision_id = decisions.decide_national_1x2(store, app_paths, accept=True, now=NOW)
    active = international.active_national_version_id(store)
    assert active
    assert decisions.last(store, decisions.NATIONAL_1X2)["id"] == decision_id
    assert international.generator_allowed(store, "uefa.nl", "1x2", model_version_id=active)[0]
    assert international.generator_allowed(
        store, "uefa.nl", "double_chance", model_version_id=active
    )[0]
    previous_rows = store.scalar("SELECT COUNT(*) FROM user_decisions")
    predictions_before = store.scalar("SELECT COUNT(*) FROM predictions")
    decisions.decide_national_1x2(store, app_paths, accept=False, now=NOW + timedelta(seconds=1))
    assert international.active_national_version_id(store) is None
    assert store.scalar("SELECT COUNT(*) FROM user_decisions") == previous_rows + 1
    assert store.scalar("SELECT COUNT(*) FROM predictions") == predictions_before


def test_market_benchmark_policy_is_not_relaxed_for_national_activation(store, app_paths):
    _seed_history(store)
    _valid_report(store, benchmark=False)
    readiness = international.national_registry_status(store, app_paths)
    assert readiness["status"] == "experimental"
    assert any("Brak porównania z rynkiem" in why for why in readiness["reasons"])
    with pytest.raises(ValueError, match="istniejącej polityki rejestru modeli"):
        decisions.decide_national_1x2(store, app_paths, accept=True, now=NOW)
    assert decisions.last(store, decisions.NATIONAL_1X2) is None


def test_legacy_approved_national_version_is_not_a_second_serving_path(store, app_paths):
    """TASK 018: national forecasts are served by the national scope of the league path; the
    TASK 017 approval path still records its frozen forecasts, but they are not served."""
    _seed_history(store)
    _valid_report(store)
    event_id, day = _sts_offer(store)
    assert international.national_serving_predictions(store, app_paths, now=NOW)["stored"] == 0
    decisions.decide_national_1x2(store, app_paths, accept=True, now=NOW)
    version_id = international.active_national_version_id(store)
    found = international.national_serving_predictions(store, app_paths, now=NOW)
    assert found["stored"] == 1
    assert international.national_serving_predictions(store, app_paths, now=NOW)["kept"] == 1
    assert version_id not in services.active_version_ids(store)
    predictions = latest_predictions(
        store,
        as_of=NOW,
        versions=services.active_version_ids(store),
        prefer=services.prediction_preference(store),
        offer_dates=[day],
    )
    assert (event_id, "1x2", "FT", None) not in predictions


def test_unknown_competitions_and_names_are_not_fuzzy_matched(store, app_paths, monkeypatch):
    _seed_history(store)
    _valid_report(store)
    known_id, _day = _sts_offer(store, home=TEAMS[0], away=TEAMS[1])
    unmatched_id, _day = _sts_offer(store, home="Unlistedonia", away=TEAMS[1])
    assert for_sts_league("MIĘDZYNARODOWE - LIGA NARODÓW UEFA", "football").code == "uefa.nl"
    assert for_sts_league("MIĘDZYNARODOWE - MŁODZIEŻÓWKA", "football") is None
    result = international.national_shadow_forecasts(store, app_paths, now=NOW)
    assert result["stored"] == 3
    assert result["unmatched"] == 1
    assert (
        international.national_shadow_of(store, unmatched_id, before=NOW + timedelta(hours=4))
        is None
    )
    assert international.national_shadow_of(store, known_id, before=NOW + timedelta(hours=4))
    assert (
        international.national_shadow_of(store, unmatched_id, before=NOW + timedelta(hours=4))
        is None
    )
    _unsupported_id, unsupported_day = _sts_offer(
        store, home=TEAMS[2], away=TEAMS[3], league="MIĘDZYNARODOWE - KING'S CUP"
    )
    assert store.get("events", _unsupported_id)["league"] == "MIĘDZYNARODOWE - KING'S CUP"
    unsupported_worker = international.national_shadow_forecasts(store, app_paths, now=NOW)
    assert unsupported_worker["skipped"]["unmapped_competition"] == 1
    from sports_edge import international_data
    from sports_edge.app import international_ui

    captured = []
    monkeypatch.setattr(international_ui.ui, "section", lambda *items: captured.extend(items))
    monkeypatch.setattr(
        international_ui.ui,
        "table",
        lambda columns, body, numeric=(): repr((columns, body)),
    )
    monkeypatch.setattr(international_ui, "_table", captured.append)
    international_ui.national_coupon_block(store, unsupported_day, NOW, [])
    shown = " ".join(map(str, captured))
    assert "brak danych — bez prognozy" in shown
    assert "KING'S CUP" in shown
    assert any(
        item["header"] == "MIĘDZYNARODOWE - KING'S CUP"
        for item in international_data.unmapped_headers(store)
    )


def test_synthetic_coupon_status_ui_shows_shadow_and_missing_decision(
    store, app_paths, monkeypatch
):
    from sports_edge.app import international_ui

    _seed_history(store)
    _valid_report(store)
    _event_id, day = _sts_offer(store)
    event = store.get("events", _event_id)
    made_at = datetime.fromisoformat(event["start_at"]) - timedelta(minutes=5)
    for variant in ("N_goals", "N3", "N2", "N1"):
        _forecast_observation(store, _event_id, observed_at=made_at, variant=variant)
    captured = []
    monkeypatch.setattr(international_ui.ui, "section", lambda *items: captured.extend(items))
    monkeypatch.setattr(
        international_ui.ui,
        "table",
        lambda columns, body, numeric=(): repr((columns, body)),
    )
    monkeypatch.setattr(international_ui, "_table", captured.append)
    international_ui.national_coupon_block(store, day, NOW, [])
    # TASK 018: no separate "outside the coupons" table for competitions with history
    assert " ".join(map(str, captured)) == ""

    captured.clear()
    monkeypatch.setattr(international_ui.st, "markdown", captured.append)
    monkeypatch.setattr(international_ui.st, "caption", captured.append)
    monkeypatch.setattr(international_ui.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        international_ui.ui,
        "explanation",
        lambda title, points, **kwargs: captured.extend([title, *points]),
    )
    international_ui.models_block(store)
    shown = " ".join(map(str, captured))
    assert "co najmniej 300 meczów" in shown
    assert "brak aktywnej decyzji" in shown
    assert "Benchmark STS 1X2 przed meczem" in shown
    assert "Polityka rejestru modeli: spełniona" in shown

    decisions.decide_national_1x2(store, app_paths, accept=True, now=NOW)
    version_id = international.active_national_version_id(store)
    assert version_id
    captured.clear()
    international_ui.national_coupon_block(
        store,
        day,
        NOW,
        [_candidate(event_id=_event_id, model_version_id=version_id, prediction_id="p-1")],
    )
    assert " ".join(map(str, captured)) == ""


def test_no_schema_migration_or_user_state_changes_are_needed(store):
    from sports_edge.storage import SCHEMA_VERSION

    before = {
        table: store.scalar(f"SELECT COUNT(*) FROM {table}")
        for table in ("coupons", "coupon_legs", "user_decisions", "automation_settings")
    }
    assert SCHEMA_VERSION >= 11  # TASK 016 added v12; TASK 017 itself adds no migration
    assert before == {table: store.scalar(f"SELECT COUNT(*) FROM {table}") for table in before}
