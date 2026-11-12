"""TASK 018 — national teams follow the league path (synthetic data only).

The national scope is trained by the same protocol v2 code as the leagues, its forecasts are
stored in ``predictions``, the same per-market gate decides and the coupon builder treats
its selections exactly like league selections."""

import json
import math
import random
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from test_task005 import cand
from test_task017 import NOW, _append_results, _sts_offer

from sports_edge import international, learning, services
from sports_edge.analysis.coupon_builder import build_coupons
from sports_edge.bookmakers import load_profiles
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.models import calibration
from sports_edge.models.elo import Elo, HistoricalMatch, walk_forward
from sports_edge.models.goals import GoalsMatch, fit
from sports_edge.pools import (
    NATIONAL_CODES,
    NATIONAL_POOL,
    event_venue,
    neutral_of,
    pool_of,
)
from sports_edge.probability_lab import predictive_status
from sports_edge.settings import AppPaths, load_settings
from sports_edge.storage import Store

ROOT = Path(__file__).resolve().parents[1]
NATIONS = [
    "Aldoria",
    "Borvia",
    "Calderra",
    "Dunmark",
    "Estoval",
    "Fenwick",
    "Gorlan",
    "Halvaria",
    "Istrava",
    "Jorvik",
    "Kestrel",
    "Lunmoor",
    "Marovia",
    "Nordel",
    "Orvana",
    "Pellia",
    "Quorin",
    "Rastova",
    "Selwyn",
    "Tarvos",
    "Ulmora",
    "Valdren",
    "Wexmoor",
    "Ystral",
]
NATIONAL_HEADER = "MIĘDZYNARODOWE - LIGA NARODÓW UEFA"


def _history(seed: int = 18) -> list[dict]:
    """SYNTHETIC national results 2010-01 … 2026-08: three match days a month, seven matches
    each; 70% at the home team's country, 20% neutral, 10% unknown venue."""
    rng = random.Random(seed)
    strength = {name: rng.gauss(0, 0.55) for name in NATIONS}
    rows = []
    day = date(2010, 1, 3)
    while day <= date(2026, 8, 31):
        for match_day in (3, 10, 17):
            when = day.replace(day=match_day)
            teams = rng.sample(NATIONS, 14)
            for i in range(7):
                home, away = teams[2 * i], teams[2 * i + 1]
                roll = rng.random()
                venue = home if roll < 0.7 else "Neutralia" if roll < 0.9 else None
                advantage = 0.25 if venue == home else 0.0
                lam = 1.3 * math.exp(strength[home] - strength[away] + advantage)
                mu = 1.1 * math.exp(strength[away] - strength[home])
                score = (
                    sum(rng.random() < lam / 8 for _ in range(8)),
                    sum(rng.random() < mu / 8 for _ in range(8)),
                )
                rows.append(
                    {
                        "competition": "fifa.friendly" if i == 6 else "uefa.nl",
                        "day": when.isoformat(),
                        "home": home,
                        "away": away,
                        "score": score,
                        "venue": venue,
                        "available_at": datetime.combine(when, datetime.min.time(), UTC)
                        + timedelta(hours=30),
                    }
                )
        day = (day.replace(day=1) + timedelta(days=32)).replace(day=3)
    return rows


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    """One store with the synthetic national history and the national scope trained by
    protocol v2 (the same ``run_protocol_v2`` as the leagues)."""
    home = tmp_path_factory.mktemp("task018")
    shutil.copytree(ROOT / "config", home / "config")
    paths = AppPaths(home)
    from sports_edge.repository import ensure_reference_data

    store = Store(":memory:")
    ensure_reference_data(store)
    _append_results(store, _history(), available_at=NOW - timedelta(days=60))
    run = services.run_protocol_v2(store, paths, now=NOW - timedelta(days=1), scope="national")
    yield store, paths, run
    store.close()


def _observation(store, *, kind, payload, when, competition="uefa.nl", key="synthetic"):
    store.insert(
        "stat_observations",
        id=digest(["task018", kind, key, payload, iso(when)]),
        kind=kind,
        sport="football",
        competition=competition,
        season=None,
        entity_type="competition",
        entity_key=key,
        event_date=None,
        source="openfootball_int",
        source_ref=None,
        payload_json=canonical(payload),
        data_version="synthetic",
        quality="complete",
        published_at=None,
        fetched_at=iso(when),
        observed_at=iso(when),
        available_at=iso(when),
        available_basis="observed_at_fetch",
        import_id=None,
    )


# ---------------------------------------------------------------- scope and forecasts


def test_national_scope_is_registered_by_the_league_protocol(trained):
    store, _paths, run = trained
    assert services.MODEL_SCOPES["national"] == NATIONAL_CODES
    served = services.active_versions(store)["national"]
    assert served["elo"]["version_id"] == run["elo"]["version_id"]
    assert served["goals"]["version_id"] == run["goals"]["version_id"]
    for family, key in ((services.ELO_FAMILY, "elo"), (services.GOALS_FAMILY, "goals")):
        row = store.get("model_versions", run[key]["version_id"])
        validation = json.loads(row["validation_json"])
        assert validation["protocol"] == "v2"
        assert tuple(validation["competitions"]) == NATIONAL_CODES
        # the first version of a scope is served, exactly like the first league version
        assert learning.champion_id(store, "national", family) == run[key]["version_id"]
    # one rating pool and one per-"league" gate for all national competitions
    assert set(run["goals"]["by_league"]) == {NATIONAL_POOL}
    assert run["elo"]["n"]["selection"] >= 300


def test_national_forecasts_are_stored_in_predictions_and_reach_the_builder(trained):
    store, paths, _run = trained
    event_id, day = _sts_offer(store, home=NATIONS[0], away=NATIONS[1])
    found = services.generate_predictions(store, paths, now=NOW)
    assert found["scopes"]["national"]["saved"] >= 1
    elo_id = services.active_versions(store)["national"]["elo"]["version_id"]
    row = store.rows(
        "SELECT * FROM predictions WHERE event_id=? AND model_version_id=?", (event_id, elo_id)
    )
    assert len(row) == 1 and row[0]["market_key"] == "1x2" and row[0]["kind"] == "prospective"
    inputs = json.loads(row[0]["inputs_json"])
    assert inputs["competition"] == "uefa.nl"
    assert inputs["venue_status"].startswith("nieznane")  # no fixture list: no home advantage
    settings = load_settings(paths)
    candidates, _ = services.day_candidates_at(store, paths, settings, day, now=NOW)
    mine = [c for c in candidates if c.event_id == event_id and c.market_key == "1x2"]
    assert mine and all(c.p_model is not None and c.prediction_id for c in mine)
    report = json.loads(services.active_versions(store)["national"]["goals"]["validation_json"])
    expected = report["elo_predictive"]["1x2"]["status"]
    assert {c.predictive_status for c in mine} == {expected}
    # the same forecast again: kept, not duplicated (league idempotency)
    again = services.generate_predictions(store, paths, now=NOW)
    assert again["scopes"]["national"]["saved"] == 0
    assert again["scopes"]["national"]["kept"] >= 1


def test_competitions_without_history_get_no_forecast(trained):
    store, paths, _run = trained
    unsupported, _day = _sts_offer(
        store, home=NATIONS[2], away=NATIONS[3], league="MIĘDZYNARODOWE - KING'S CUP"
    )
    concacaf, _day = _sts_offer(
        store, home=NATIONS[4], away=NATIONS[5], league="MIĘDZYNARODOWE - LIGA NARODÓW CONCACAF"
    )
    services.generate_predictions(store, paths, now=NOW)
    for event_id in (unsupported, concacaf):
        assert store.scalar("SELECT COUNT(*) FROM predictions WHERE event_id=?", (event_id,)) == 0


def test_old_shadow_observations_are_left_untouched(trained):
    store, paths, _run = trained
    _observation(
        store,
        kind=international.NATIONAL_SHADOW_KIND,
        payload={"probabilities": {"home": 0.9, "draw": 0.05, "away": 0.05}},
        when=NOW - timedelta(days=3),
        key="old-shadow",
    )
    before = store.rows("SELECT * FROM stat_observations ORDER BY id")
    _sts_offer(store, home=NATIONS[6], away=NATIONS[7])
    services.generate_predictions(store, paths, now=NOW)
    settings = load_settings(paths)
    services.day_candidates_at(
        store, paths, settings, (NOW + timedelta(hours=5)).date().isoformat(), now=NOW
    )
    assert store.rows("SELECT * FROM stat_observations ORDER BY id") == before


# ---------------------------------------------------------------- gate and builder


def test_per_market_gate_uses_the_league_thresholds(trained):
    _store, paths, run = trained
    rules = services.probability_policy(paths).validation
    goals = run["goals"]
    for family in ("1x2", "double_chance"):
        again = predictive_status(goals["elo_families"][family], rules)
        assert again["status"] == goals["elo_predictive"][family]["status"]
    base = {"baseline_log_loss": 0.60, "by_season": {}, "ranges": []}
    above = predictive_status(
        {**base, "calibrated": {"n": 400, "ece": rules["max_ece"] + 0.01, "log_loss": 0.5}},
        rules,
    )
    below = predictive_status(
        {**base, "calibrated": {"n": 400, "ece": rules["max_ece"] - 0.01, "log_loss": 0.5}},
        rules,
    )
    assert above["status"] == "experimental" and any("ECE" in r for r in above["reasons"])
    assert below["status"] == "validated_predictive"


def _builder(candidates, paths):
    settings = load_settings(paths)
    return build_coupons(
        candidates,
        services.coupon_rules(paths, settings),
        cost=load_profiles(paths.config)["sts"].cost,
        on_date=date(2026, 10, 31),
        max_profiles_per_selection=0,
    )


def _legs(status: str, league: str):
    return [
        cand(f"ev{i}", odds, p, low, league=league, predictive_status=status)
        for i, (odds, p, low) in enumerate(
            [("1.70", 0.78, 0.74), ("1.75", 0.77, 0.73), ("1.30", 0.86, 0.82), ("1.45", 0.82, 0.78)]
        )
    ]


@pytest.mark.parametrize("status", ["experimental", "validated_predictive"])
def test_builder_treats_national_selections_exactly_like_league_ones(trained, status):
    _store, paths, _run = trained
    league = _builder(_legs(status, "LIGA SYNTETYCZNA"), paths)
    national = _builder(_legs(status, NATIONAL_HEADER), paths)
    shape = {
        name: (None if p is None else [leg.event_id for leg in p.legs])
        for name, p in league.proposals.items()
    }
    assert shape == {
        name: (None if p is None else [leg.event_id for leg in p.legs])
        for name, p in national.proposals.items()
    }
    profiles = services.coupon_rules(paths, load_settings(paths)).profiles
    for name, proposal in national.proposals.items():
        if status == "experimental" and profiles[name]["require_predictive_validation"]:
            assert proposal is None  # experimental legs only where leagues allow them
    if status == "experimental":
        assert national.proposals["higher_payout"] is not None
    else:
        assert sum(p is not None for p in national.proposals.values()) >= 2


def test_national_candidates_are_not_gated_by_the_task017_registry(trained):
    store, _paths, _run = trained
    allowed, reason = international.generator_allowed(store, "uefa.nl", "1x2")
    assert allowed and "jak w ligach" in reason
    assert international.generator_allowed(store, "fifa.friendly", "total")[0]
    # European cups keep their own lock (TASK 009)
    assert not international.generator_allowed(store, "uefa.cl", "1x2")[0]


# ---------------------------------------------------------------- calibration and venue


def test_calibration_reduces_ece_of_exaggerated_forecasts_and_keeps_their_order():
    rng = random.Random(5)
    truth = [rng.uniform(0.2, 0.8) for _ in range(3000)]
    outcomes = [int(rng.random() < p) for p in truth]
    exaggerated = [min(0.99, max(0.01, 0.5 + 1.8 * (p - 0.5))) for p in truth]
    blocks = calibration.isotonic_fit(exaggerated[:1500], outcomes[:1500])
    test_p, test_y = exaggerated[1500:], outcomes[1500:]
    fixed = [calibration.isotonic_apply(blocks, p)[0] for p in test_p]
    assert calibration.ece(fixed, test_y) < calibration.ece(test_p, test_y)
    order = sorted(range(len(test_p)), key=lambda i: test_p[i])
    assert all(fixed[a] <= fixed[b] for a, b in zip(order, order[1:], strict=False))


def test_home_advantage_only_on_a_known_home_venue():
    base = {"competition": "uefa.nl", "home": "Aldoria"}
    assert not neutral_of({**base, "details_json": json.dumps({"venue_country": "Aldoria"})})
    assert neutral_of({**base, "details_json": json.dumps({"venue_country": "Neutralia"})})
    assert neutral_of({**base, "details_json": json.dumps({"venue_country": None})})
    assert not neutral_of({"competition": "es.1", "home": "Club", "details_json": "{}"})
    model = Elo(k=20, home_advantage=80)
    model.ratings = {"A": 1500.0, "B": 1500.0}
    home = model.probabilities("A", "B", three_way=False)["home"]
    neutral = model.probabilities("A", "B", three_way=False, neutral=True)["home"]
    assert home > neutral == pytest.approx(0.5)


def _fixture_list(store, items, *, when):
    from sports_edge.international_data import FIXTURE_KIND

    _observation(store, kind=FIXTURE_KIND, payload=items, when=when, key=iso(when))


def test_event_venue_comes_only_from_an_unambiguous_fixture(store):
    event = {"offer_date": "2026-11-06", "start_at": iso(NOW + timedelta(hours=5))}
    assert event_venue(store, "uefa.nl", event, "Aldoria", "Borvia", now=NOW)[0] is True
    fixture = {
        "date": "2026-11-06",
        "home": "Aldoria",
        "away": "Borvia",
        "start_at": None,
        "venue_country": "Aldoria",
    }
    _fixture_list(store, [fixture], when=NOW - timedelta(days=2))
    neutral, venue, status = event_venue(store, "uefa.nl", event, "Aldoria", "Borvia", now=NOW)
    assert (neutral, venue) == (False, "Aldoria") and "gospodarz" in status
    away_ground = {**fixture, "home": "Calderra", "away": "Dunmark", "venue_country": "Neutralia"}
    _fixture_list(store, [fixture, away_ground], when=NOW - timedelta(days=1))
    assert event_venue(store, "uefa.nl", event, "Calderra", "Dunmark", now=NOW)[0] is True
    # club leagues: always a home team, the fixture list is never read
    assert event_venue(store, "es.1", event, "X", "Y", now=NOW) == (False, None, "liga")


# ---------------------------------------------------------------- league regression


def test_league_models_are_numerically_unchanged():
    """Club matches have no neutral flag and their own pool: the same keys, the same Elo
    and the same Dixon–Coles fit as before TASK 018."""
    assert pool_of("es.1") == "es.1" and pool_of("uefa.nl") == pool_of("fifa.friendly")
    rng = random.Random(3)
    matches = []
    for k in range(400):
        home, away = rng.sample(["A", "B", "C", "D", "E", "F"], 2)
        played = date(2024, 1, 1) + timedelta(days=k)
        matches.append(
            HistoricalMatch(
                datetime.combine(played, datetime.min.time(), UTC) + timedelta(hours=30),
                played.isoformat(),
                home,
                away,
                rng.randint(0, 3),
                rng.randint(0, 3),
                competition="es.1",
            )
        )
    model = Elo(k=20, home_advantage=60, draw_margin=0.2)
    items = walk_forward(matches, model, three_way=True)
    # replay with the pre-TASK 018 formula: (rh + home advantage − ra) / 400, every match
    ratings: dict[str, float] = {}
    for item in items:
        m = item["match"]
        d = (ratings.get(m.home, 1500.0) + 60 - ratings.get(m.away, 1500.0)) / 400
        assert item["forecast"]["home"] == 1 / (1 + 10 ** (-d + 0.2))
        change = 20 * ({"home": 1.0, "draw": 0.5, "away": 0.0}[m.outcome] - 1 / (1 + 10**-d))
        ratings[m.home] = ratings.get(m.home, 1500.0) + change
        ratings[m.away] = ratings.get(m.away, 1500.0) - change
    reference = Elo(k=20, home_advantage=60, draw_margin=0.2)
    reference.ratings = {"A": 1530.0, "B": 1490.0}
    assert reference.diff("A", "B") == (1530.0 + 60 - 1490.0) / 400
    goals = [GoalsMatch(m.match_date, m.home, m.away, m.home_score, m.away_score) for m in matches]
    flagged = [GoalsMatch(*g.__dict__.values()) for g in goals]
    a = fit(goals, fit_date="2025-02-01")
    b = fit(flagged, fit_date="2025-02-01")
    assert a.home_advantage == b.home_advantage and a.rates("A", "B") == b.rates("A", "B")


def test_league_candidates_and_coupons_do_not_change_with_the_national_scope(store, app_paths):
    """The same league data before and after national history, versions and forecasts are
    added: identical candidates, proposals and coupon identities."""
    from test_models_data import load, synthetic

    from sports_edge.coupon_store import coupon_identity
    from sports_edge.datasources.competitions import COMPETITIONS

    # SYNTHETIC LaLiga: one match a day 2019-01 … 2026-10 (fresh at the STS event)
    load(store, synthetic(2860, start=date(2019, 1, 1)), competition="es.1")
    services.run_protocol_v2(store, app_paths, now=NOW - timedelta(days=2), scope="core")
    header = COMPETITIONS["es.1"].sts_leagues[0]
    pairs = [("Team A CF", "Team B CF"), ("Team C CF", "Team D CF"), ("Team E CF", "Team F CF")]
    for home, away in pairs:
        _event, day = _sts_offer(store, home=home, away=away, league=header)
    now = NOW
    settings = load_settings(app_paths)

    def snapshot():
        services.generate_predictions(store, app_paths, now=now)
        services.generate_goals_predictions(store, app_paths, now=now)
        candidates, _ = services.day_candidates_at(store, app_paths, settings, day, now=now)
        built = _builder(candidates, app_paths)
        return (
            [(c.key, c.p_model, c.p_low, c.predictive_status, c.prediction_id) for c in candidates],
            {
                name: None
                if p is None
                else (
                    [leg.key for leg in p.legs],
                    p.p_conservative,
                    coupon_identity(
                        source="generator",
                        profile=name,
                        day=day,
                        policy_version="synthetic",
                        selection_ids=[leg.selection_id for leg in p.legs],
                    ),
                )
                for name, p in built.proposals.items()
            },
        )

    before = snapshot()
    assert any(item[1] is not None for item in before[0])  # league forecasts exist
    _append_results(store, _history(), available_at=now - timedelta(days=60))
    services.run_protocol_v2(store, app_paths, now=now - timedelta(hours=1), scope="national")
    assert services.active_versions(store)["national"]["elo"] is not None
    after = snapshot()
    assert after == before


def test_best_picks_list_national_selections_like_league_ones():
    """Kupon dnia → „Najlepsze typy do rozważenia” groups every selection with a forecast;
    a national selection lands in the same group as the same league selection."""
    from sports_edge import insights

    at = datetime(2026, 10, 31, 12, tzinfo=UTC)
    for status in ("validated_predictive", "experimental"):
        league = insights.candidate_groups(
            [cand("lg", "1.60", 0.80, 0.76, predictive_status=status)], now=at, max_odds=4.0
        )
        national = insights.candidate_groups(
            [cand("nt", "1.60", 0.80, 0.76, league=NATIONAL_HEADER, predictive_status=status)],
            now=at,
            max_odds=4.0,
        )
        assert len(national) == 1
        assert national[0]["group"] == league[0]["group"]
        assert national[0]["candidate"].league == NATIONAL_HEADER


# ---------------------------------------------------------------- frequency of play


def test_frequency_rules_of_the_national_scope_come_from_its_configuration():
    """User 12.11.2026: national teams do not play as often as a league — freshness, grids,
    the fitting window, v3 windows and retraining follow config/national_scope.toml; the
    leagues keep their values."""
    import tomllib

    from sports_edge import pools, retraining, rolling
    from sports_edge.probability_lab import HALF_LIVES
    from sports_edge.sportsdata import GRID, MAX_DATA_GAP_DAYS

    config = tomllib.loads((ROOT / "config" / "national_scope.toml").read_text(encoding="utf-8"))
    assert pools.max_gap_days("es.1", MAX_DATA_GAP_DAYS) == MAX_DATA_GAP_DAYS == 21
    assert pools.max_gap_days(NATIONAL_POOL, 21) == config["freshness"]["max_gap_days"]
    assert pools.elo_grid(services.CORE_COMPETITIONS, GRID) is GRID
    national = pools.elo_grid(NATIONAL_CODES, GRID)
    assert list(national["k"]) == config["elo"]["k"] and max(national["k"]) > max(GRID["k"])
    assert pools.half_lives(services.EXTENDED_COMPETITIONS, HALF_LIVES) is HALF_LIVES
    assert (
        list(pools.half_lives(NATIONAL_CODES, HALF_LIVES))
        == (config["dixon_coles"]["half_life_days"])
    )
    assert pools.dc_window_days("en.1", 730) == 730
    assert pools.dc_window_days(NATIONAL_POOL, 730) == config["dixon_coles"]["window_days"]
    assert pools.retraining_rules("core", 150, 7) == (150, 7)
    assert pools.retraining_rules("national", 150, 7) == (
        config["retraining"]["min_new_results"],
        config["retraining"]["min_days_between_trainings"],
    )
    assert (retraining.MIN_NEW_RESULTS, retraining.MIN_DAYS_BETWEEN_TRAININGS) == (150, 7)
    policy = rolling.load_policy(ROOT / "config")
    assert policy.for_scope("national").calibration_months == 24
    assert policy.for_scope("national").selection_months == 24
    assert policy.for_scope("extended").selection_months == policy.selection_months == 12


def test_national_versions_record_their_own_grid_and_window(trained):
    _store, _paths, run = trained
    k_values = {row["k"] for row in run["elo"]["selection"]}
    assert k_values == set(pools_grid()["k"])
    assert run["goals"]["window_days"] == 1460
    assert {row["half_life_days"] for row in run["goals"]["selection"]} == {
        240.0,
        480.0,
        720.0,
        1080.0,
    }


def pools_grid():
    from sports_edge import pools
    from sports_edge.sportsdata import GRID

    return pools.elo_grid(NATIONAL_CODES, GRID)
