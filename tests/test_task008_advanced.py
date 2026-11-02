"""TASK 008 (supplement): Advanced Football Prediction Engine and the lineup information in
coupons, the explainer and the worker.

Every league, team, player and score below is SYNTHETIC test data, not a real fact.
"""

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from sports_edge import advanced
from sports_edge.models.goals import markets, score_matrix
from sports_edge.sportsdata import Split

SPLIT = Split(
    validation_from="2021-07-01",
    calibration_from="2022-07-01",
    selection_from="2023-07-01",
    holdout_from="2024-07-01",
)
T0 = datetime(2026, 10, 31, 12, 40, tzinfo=UTC)  # the fixture offer arrived at 12:30


def league(code="xx.1", *, teams=18, first=2019, last=2025, fatigue=0.0, seed=3, names=None):
    """Double round robins; every 4th round is midweek (3 days of rest). ``fatigue`` lowers
    the goals of a team that rested ≤ 4 days — an effect the served forecasts do not know."""
    rng = np.random.default_rng(seed)
    names = list(names or [f"{code} Klub {i}" for i in range(teams)])
    teams = len(names)
    attack = dict(zip(names, rng.normal(0, 0.25, teams), strict=True))
    defence = dict(zip(names, rng.normal(0, 0.2, teams), strict=True))
    rows, elo, goals, rates = [], {}, {}, {}
    for season in range(first, last + 1):
        order = list(names)
        rng.shuffle(order)
        rounds = []
        for r in range(teams - 1):  # circle method: every team plays every round
            pairs = [(order[i], order[teams - 1 - i]) for i in range(teams // 2)]
            rounds.append(pairs if r % 2 == 0 else [(a, h) for h, a in pairs])
            order = [order[0], order[-1], *order[1:-1]]
        rounds += [[(a, h) for h, a in pairs] for pairs in rounds]
        day, last_played = date(season, 8, 10), {}
        for r, pairs in enumerate(rounds):
            day += timedelta(days=3 if r % 4 == 1 else 7)
            for h, a in pairs:
                lam = math.exp(0.35 + attack[h] - defence[a])
                mu = math.exp(0.15 + attack[a] - defence[h])
                tired = {
                    t: (day - last_played.get(t, day - timedelta(days=7))).days <= 4 for t in (h, a)
                }
                hs = int(rng.poisson(lam * (math.exp(-fatigue) if tired[h] else 1)))
                as_ = int(rng.poisson(mu * (math.exp(-fatigue) if tired[a] else 1)))
                rid = f"{code}-{season}-{r}-{h}-{a}"
                rows.append(
                    {
                        "id": rid,
                        "competition": code,
                        "match_date": day.isoformat(),
                        "home": h,
                        "away": a,
                        "home_score": hs,
                        "away_score": as_,
                    }
                )
                served = markets(score_matrix(lam, mu, -0.05), (1.5, 2.5))
                elo[rid], goals[rid], rates[rid] = served[("1x2", None)], served, (lam, mu, -0.05)
                last_played[h] = last_played[a] = day
    return rows, elo, goals, rates


def _run(rows, elo, goals, rates, **kw):
    return advanced.run(
        rows,
        split=kw.pop("split", SPLIT),
        elo=elo,
        goals=goals,
        rates=rates,
        elo_params={"k": 20, "home_advantage": 60},
        coverage={"stats": {"holdout": 120}, "lineups": {"holdout": 2}},
        **kw,
    )


def _two_leagues(fatigue):
    out = [{}, {}, {}, {}]
    rows = []
    for i, code in enumerate(("xx.1", "yy.1")):
        r, *maps = league(code, fatigue=fatigue, seed=3 + i)
        rows += r
        for target, found in zip(out[1:], maps, strict=True):
            target.update(found)
    return rows, *out[1:]


# ---------------------------------------------------------------- features


def test_features_are_as_of_and_missing_values_are_never_imputed():
    rows = [
        {
            "id": "a",
            "competition": "xx.1",
            "match_date": "2025-01-01",
            "home": "A",
            "away": "B",
            "home_score": 3,
            "away_score": 0,
        },
        {
            "id": "b",
            "competition": "xx.1",
            "match_date": "2025-01-01",
            "home": "C",
            "away": "A",
            "home_score": 1,
            "away_score": 1,
        },  # A plays twice on one day (synthetic edge case)
        {
            "id": "c",
            "competition": "xx.1",
            "match_date": "2025-01-05",
            "home": "A",
            "away": "C",
            "home_score": 0,
            "away_score": 2,
        },
    ]
    feats = advanced.team_features(rows, k=20, home_advantage=60)
    first = feats["a"]["home"]
    assert all(value is None for value in first.values())  # no history: missing, not 0
    same_day = feats["b"]["away"]
    assert same_day["rest"] is None  # the same day's result is not known yet
    later = feats["c"]["home"]
    assert later["rest"] == 4.0 and later["load14"] == 2.0
    assert later["ppg5"] is None  # only 2 earlier matches: still missing
    assert not advanced.complete(feats["c"])
    # a later result never changes an earlier feature (no leakage)
    more = [
        *rows,
        {
            "id": "d",
            "competition": "xx.1",
            "match_date": "2025-01-09",
            "home": "B",
            "away": "A",
            "home_score": 5,
            "away_score": 0,
        },
    ]
    assert advanced.team_features(more, k=20, home_advantage=60)["c"] == feats["c"]
    # an upcoming match (no score) gets features and changes nothing
    upcoming = {
        "id": "u",
        "competition": "xx.1",
        "match_date": "2025-01-20",
        "home": "A",
        "away": "B",
    }
    found = advanced.team_features([*more, upcoming], k=20, home_advantage=60)
    assert found["u"]["home"]["rest"] == 11.0 and found["c"] == feats["c"]


def test_markets_come_from_one_score_distribution():
    rng = np.random.default_rng(1)
    lam, mu = rng.uniform(0.4, 2.8, 50), rng.uniform(0.3, 2.2, 50)
    rho = rng.uniform(-0.15, 0.05, 50)
    out = advanced.with_double_chance(advanced.score_markets(lam, mu, rho))
    for i in range(50):
        expected = markets(score_matrix(lam[i], mu[i], rho[i]), (1.5, 2.5))
        assert out["1x2"][i] == pytest.approx(
            [expected[("1x2", None)][o] for o in advanced.OUTCOMES], abs=1e-9
        )
        assert out["total_2.5"][i] == pytest.approx(expected[("total", 2.5)]["over"], abs=1e-9)
        assert out["btts"][i] == pytest.approx(expected[("btts", None)]["yes"], abs=1e-9)
    assert (out["total_1.5"] >= out["total_2.5"]).all()  # coherent goal lines
    assert out["double_chance"][:, 0] == pytest.approx(1 - out["1x2"][:, 2])  # 1X = 1 − P(2)


# ---------------------------------------------------------------- the experiment


@pytest.fixture(scope="module")
def without_effect():
    return _run(*_two_leagues(0.0))


@pytest.fixture(scope="module")
def with_effect():
    return _run(*_two_leagues(0.45))


def test_without_a_real_effect_the_active_models_stay(without_effect):
    report = without_effect
    assert not any(d["passed"] for d in report["decision"].values())
    assert "pozostają aktywne" in report["conclusion"] and report["models"] == {}
    assert report["worse"]  # worse experiments are recorded, not hidden
    assert report["leakage"]["ok"] and report["leakage"]["mismatches"] == 0
    assert {s["variant"] for s in report["skipped"]} == {"abl_xg", "F_squad"}
    # every variant of a market is judged on the SAME matches
    for market in advanced.MARKETS:
        sizes = {row["n"] for row in report["markets"][market]["windows"]["selection"].values()}
        assert len(sizes) == 1


def test_a_real_effect_is_found_but_the_user_decides(with_effect):
    report = with_effect
    passed = {m: d for m, d in report["decision"].items() if d["passed"]}
    assert set(passed) & {"total_1.5", "total_2.5", "btts"}  # fatigue lowers the goals
    for market, decision in passed.items():
        variant = advanced.BY_KEY[decision["candidate"]]
        assert "odpoczynek" in variant.groups, market  # the right information was found
        assert decision["criteria"][-1] == {
            "name": "Decyzja użytkownika",
            "ok": None,
            "detail": "wymagana przed oceną",
        }
        assert decision["holdout"] is not None  # reported ...
    assert set(report["models"]) == {d["candidate"] for d in passed.values()}
    assert "decyzji użytkownika" in report["conclusion"]


def test_the_holdout_never_chooses_the_model(with_effect):
    rows, elo, goals, rates = _two_leagues(0.45)
    flipped = [
        {**r, "home_score": r["away_score"], "away_score": r["home_score"]}
        if r["match_date"] >= SPLIT.holdout_from
        else r
        for r in rows
    ]
    again = _run(flipped, elo, goals, rates)
    for market in advanced.MARKETS:
        first, second = with_effect["decision"][market], again["decision"][market]
        assert (first["candidate"], first["passed"]) == (second["candidate"], second["passed"])
    assert (
        again["markets"]["1x2"]["windows"]["holdout"]["A_elo"]["log_loss"]
        != with_effect["markets"]["1x2"]["windows"]["holdout"]["A_elo"]["log_loss"]
    )


def test_a_stored_challenger_forecasts_exactly_like_the_backtest(with_effect):
    key = next(iter(with_effect["models"]))
    model = with_effect["models"][key]
    rows, elo, _goals, rates = _two_leagues(0.45)
    feats = advanced.team_features(rows, k=20, home_advantage=60)
    match = next(
        r["id"]
        for r in rows
        if r["match_date"] >= SPLIT.selection_from and advanced.complete(feats[r["id"]])
    )
    one = advanced.predict_match(model, key, feats[match], elo=elo[match], rates=rates[match])
    assert sum(one["1x2"]) == pytest.approx(1.0)
    assert one["double_chance"][0] == pytest.approx(1 - one["1x2"][2])
    if advanced.BY_KEY[key].goals:
        assert one["total_1.5"] >= one["total_2.5"]


# ---------------------------------------------------------------- challenger in the app


def _import(store, rows, competition, *, now):
    from sports_edge.sportsdata import import_results

    plain = [
        {
            "match_date": r["match_date"],
            "home": r["home"],
            "away": r["away"],
            "home_score": r["home_score"],
            "away_score": r["away_score"],
            "available_at": datetime.fromisoformat(r["match_date"]).replace(tzinfo=UTC)
            + timedelta(hours=30),
        }
        for r in rows
    ]
    import_results(
        store,
        plain,
        file_bytes=repr(len(plain)).encode() + competition.encode(),
        file_name="synthetic.json",
        source_name="dane syntetyczne",
        license_note="dane syntetyczne do testów",
        sport="football",
        competition=competition,
        season=None,
        now=now,
        availability_basis="source_timestamp",
        provider="pitchapi",
    )


def test_challenger_waits_for_the_user_then_forecasts_in_shadow_mode(
    store, app_paths, import_sts, monkeypatch
):
    from sports_edge import advanced_lab, learning, services
    from sports_edge.models.registry import latest_predictions, register_version, save_prediction
    from sports_edge.results_engine import effective_results

    now = T0 + timedelta(minutes=5)
    words = (
        "Alfa Beta Gamma Delta Epsilon Zeta Theta Jota Kappa Lambda Omikron Sigma "
        "Omega Orion Pegaz Feniks Hydra Lyra Wega Syriusz"
    ).split()
    names = ["Legia Warszawa", "Lech Poznań", *[f"Syntetyczni {w}" for w in words]]
    rows, _elo, _goals, _rates = league(
        "pl.1", names=names, first=2020, last=2025, fatigue=0.6, seed=11
    )
    _import(store, [r for r in rows if r["match_date"] < "2026-10-30"], "pl.1", now=now)
    stored = effective_results(
        store,
        sport="football",
        until=now,
        competitions=("pl.1",),
        identities=services.team_identities(app_paths),
    )
    key = {(r["match_date"], r["home"], r["away"]): r["id"] for r in stored}
    served = {}
    for r in rows:
        found = key.get((r["match_date"], r["home"], r["away"]))
        if found:
            served[found] = r["id"]
    _rows, elo, goals, rates = league(
        "pl.1", names=names, first=2020, last=2025, fatigue=0.6, seed=11
    )
    report = _run(
        stored,
        {k: elo[v] for k, v in served.items()},
        {k: goals[v] for k, v in served.items()},
        {k: rates[v] for k, v in served.items()},
        split=services.PROTOCOLS["extended"],
    )
    assert report["models"], report["conclusion"]

    def version(family):
        return register_version(
            store,
            family=family,
            sport="football",
            description="syntetyczna",
            version=family,
            created_at=now - timedelta(days=1),
            trained_through=None,
            training_data={"competitions": ["pl.1"]},
            validation={
                "protocol": "v2",
                "competitions": ["pl.1"],
                "hyperparameters": {"k": 20, "home_advantage": 60},
            },
            policy=services.policy(app_paths),
        )[0]

    elo_id, goals_id = version(services.ELO_FAMILY), version(services.GOALS_FAMILY)
    report.update(
        scope="extended",
        experiment_id="test",
        elo_version_id=elo_id,
        goals_version_id=goals_id,
        half_life_days=240.0,
    )
    [challenger] = advanced_lab.register(store, app_paths, report, scope="extended", now=now)
    assert store.get("model_versions", challenger)["status"] == "experimental"
    assert advanced_lab.versions(store, "extended")[0]["role"] == "czeka na decyzję"
    monkeypatch.setattr(
        services,
        "active_versions",
        lambda s: {
            "core": {"elo": None, "goals": None},
            "extended": {"elo": {"version_id": elo_id}, "goals": {"version_id": goals_id}},
        },
    )
    event = store.rows("SELECT * FROM events WHERE home='Legia Warszawa'")[0]
    teams = ("Legia Warszawa", "Lech Poznań")
    assert set(teams) <= {r["home"] for r in stored}  # names as the results engine keeps them
    save_prediction(
        store,
        model_version_id=elo_id,
        event_id=event["id"],
        market_key="1x2",
        period="FT",
        line=None,
        probabilities={"home": 0.46, "draw": 0.27, "away": 0.27},
        uncertainty={},
        inputs={
            "info_cutoff": (now - timedelta(hours=1)).isoformat(),
            "data_snapshot": "s",
            "code_state": "c",
            "teams": list(teams),
            "competition": "pl.1",
        },
        kind="prospective",
        created_at=now - timedelta(minutes=1),
    )
    assert advanced_lab.generate(store, app_paths, now=now)["saved"] == 0  # no decision yet
    advanced_lab.admit(store, version_id=challenger, now=now)
    assert advanced_lab.versions(store, "extended")[0]["role"] == "tryb cienia"
    first = advanced_lab.generate(store, app_paths, now=now + timedelta(minutes=1))
    assert first["saved"] >= 1, first
    shown = advanced_lab.event_view(store, event["id"])
    assert {row["market_key"] for row in shown} >= {"1x2"}
    assert all(row["uncertainty"]["challenger"] for row in shown)
    again = advanced_lab.generate(store, app_paths, now=now + timedelta(minutes=2))
    assert again["saved"] == 0 and again["kept"] == first["saved"]  # frozen, no duplicates
    served_now = latest_predictions(store, as_of=now + timedelta(minutes=3), versions={elo_id})
    assert all(p["model_version_id"] == elo_id for p in served_now.values())  # never served
    assert learning.champion_id(store, "extended", "advanced_goals") is None  # not promoted
    assert not advanced_lab.prospective(store, advanced_lab.shadow(store)[0])["promotion"][
        "possible"
    ]


def test_quality_reports_count_only_served_forecasts(store, app_paths, monkeypatch):
    from sports_edge import collection, learning, services
    from sports_edge import forecast_quality as fq
    from sports_edge.models.registry import register_version

    start = datetime(2026, 9, 1, tzinfo=UTC)

    def version(name):
        return register_version(
            store,
            family=services.ELO_FAMILY,
            sport="football",
            description="syntetyczna",
            version=name,
            created_at=start - timedelta(days=1),
            trained_through=None,
            training_data={"competitions": ["en.1"]},
            validation={"protocol": "v2", "competitions": ["en.1"]},
            policy=services.policy(app_paths),
        )[0]

    champion, challenger = version("champion"), version("challenger")
    assert learning.served_check(store)(challenger, start)  # no roles yet: every version
    learning.record(
        store,
        kind="activated",
        actor="user",
        reason="test",
        now=start,
        version_id=champion,
        scope="core",
        family=services.ELO_FAMILY,
    )
    served = learning.served_check(store)
    assert served(champion, start + timedelta(days=1))
    assert not served(challenger, start + timedelta(days=1))
    assert not served(champion, start - timedelta(days=1))

    def row(version_id, name):
        return {
            "status": "settled",
            "model_version_id": version_id,
            "prediction_id": f"p-{name}",
            "event_id": "e1",
            "market_key": "1x2",
            "line": None,
            "league": "x",
            "predicted_at": (start + timedelta(days=2)).isoformat(),
            "start_at": (start + timedelta(days=3)).isoformat(),
            "probabilities": {"home": 0.5, "draw": 0.3, "away": 0.2},
            "outcome": {"selections": {"home": 1, "draw": 0, "away": 0}},
            "uncertainty": {},
            "inputs": {"competition": "en.1"},
        }

    monkeypatch.setattr(
        collection,
        "settled_forecasts",
        lambda s: [row(champion, "champion"), row(challenger, "challenger")],
    )
    assert {item["event"] for item in fq.items(store)} == {"p-champion"}


# ---------------------------------------------------------------- lineups in the coupon


def _lineup(store, event, *, confirmed, keeper, when, checkpoint):
    from test_task008 import _payload

    from sports_edge import prematch

    return prematch.check_event(
        store,
        event,
        fetch_lineup=lambda e: ("m_test", _payload(confirmed=confirmed, goalkeeper=keeper), 1),
        absence_provider=prematch.NoVerifiedAbsenceSource(),
        checkpoint=checkpoint,
        now=when,
    )


def test_coupon_evaluation_and_explainer_use_only_stored_lineups(store, import_sts, profiles):
    from sports_edge import advisor, llm
    from sports_edge import explainer as ex

    event = store.rows("SELECT * FROM events WHERE home='Legia Warszawa'")[0]
    cart = [
        {
            "event": "Legia Warszawa - Lech Poznań",
            "label": "1",
            "market": "1X2",
            "selection_id": "s1",
            "odds_id": "o1",
            "event_id": event["id"],
            "day": "2026-10-31",
            "added_odds": "2.10",
            "start_at": event["start_at"],
        }
    ]

    def report(at):
        return advisor.evaluate(
            cart,
            [None],
            stake=Decimal("10.00"),
            cost=profiles["sts"].cost,
            on_date=None,
            now=at,
            store=store,
        )

    empty = ex.answer("Czy opublikowano oficjalne składy?", report(T0), cart=cart, rows=[None])
    assert "Brak zapisanych obserwacji składów" in empty["answer"]
    _lineup(
        store,
        event,
        confirmed=False,
        keeper=("p_gk1", "Bramkarz Jeden"),
        when=T0 + timedelta(minutes=1),
        checkpoint="T-6h",
    )
    _lineup(
        store,
        event,
        confirmed=True,
        keeper=("p_gk2", "Bramkarz Dwa"),
        when=T0 + timedelta(hours=5),
        checkpoint="T-90min",
    )
    found = report(T0 + timedelta(hours=5, minutes=1))
    lineup = found["legs"][0]["lineups"]
    assert lineup["lineup_confirmed"] and lineup["model_uses_lineups"] is False
    assert lineup["absence_source"] == "brak zweryfikowanego źródła"  # nothing invented

    def ask(question):
        return ex.answer(question, found, cart=cart, rows=[None])["answer"]

    assert "potwierdzony" in ask("Czy opublikowano oficjalne składy?")
    assert "Bramkarz Dwa" in ask("Czy podstawowy bramkarz wystąpi?")
    assert "Bramkarz Jeden → Bramkarz Dwa" in ask("Co zmieniło się od ostatniej analizy?")
    assert ask("Czy obecna prognoza uwzględnia składy?").startswith("Nie.")
    assert "zweryfikowanych absencji" in ask("Jakich danych brakuje?")
    facts = llm.fact_sheet(found)
    leg = facts["typy"][0]
    assert leg["sklady"]["prognoza_uwzglednia_sklady"] is False
    assert "Bramkarz Dwa" in leg["sklady"]["bramkarze"]


def test_check_buttons_queue_worker_commands_without_scheduled_jobs(app_paths, monkeypatch):
    from sports_edge import automation, services

    store = services.open_store(app_paths)
    started = []
    monkeypatch.setattr(automation, "start_worker", lambda paths, **kw: started.append(kw) or 4242)
    message = automation.request(
        app_paths, store, "check_events", now=T0, payload={"event_ids": ["e1", "e2"]}
    )
    assert "jednorazowo" in message and started == [{"commands_only": True}]
    seen, calls = [], []
    monkeypatch.setattr(
        automation,
        "job_lineups_events",
        lambda s, p, *, now, settings, event_ids=(): (
            seen.append(list(event_ids))
            or automation.JobOutcome("ok", "", {"processed": len(event_ids)})
        ),
    )

    def runner(job):
        def run(s, p, *, now, settings):
            calls.append(job)
            return automation.JobOutcome("ok", "", {})

        return run

    worker = automation.Worker(app_paths, runners={j: runner(j) for j in automation.JOBS})
    assert worker.run(commands_only=True, sleep=lambda s: None) == "polecenia wykonane"
    assert seen == [["e1", "e2"]] and calls == []  # the check only, no scheduled job
    assert automation.pending_commands(store) == []


def test_a_blocked_lineup_source_is_one_reason_not_an_error_per_match(store, app_paths, import_sts):
    from sports_edge import services
    from sports_edge.settings import load_settings

    def fetch(event):
        raise AssertionError("never called when the source is blocked")

    fetch.blocked = "brak klucza PITCHAPI_KEY"
    event = store.rows("SELECT id FROM events WHERE home='Legia Warszawa'")[0]
    found = services.run_prematch(
        store, app_paths, load_settings(app_paths), now=T0, fetch=fetch, event_ids=[event["id"]]
    )
    assert found["blocked"] == "brak klucza PITCHAPI_KEY" and found["checked"] == 0
    assert store.scalar("SELECT COUNT(*) FROM prematch_checks") == 0
    assert store.scalar("SELECT COUNT(*) FROM prematch_alerts") == 0


def test_dashboard_overview_shows_monitored_coupon_matches(store, import_sts, profiles):
    from sports_edge import services
    from sports_edge.paper import create_coupon

    event = store.rows("SELECT * FROM events WHERE home='Legia Warszawa'")[0]
    leg = store.rows(
        """SELECT o.id FROM odds o JOIN selections s ON s.id=o.selection_id
        JOIN markets m ON m.id=s.market_id WHERE m.event_id=? AND m.market_key='1x2'
        AND s.key='home'""",
        (event["id"],),
    )[0]["id"]
    create_coupon(
        store,
        odds_ids=[leg],
        stake=Decimal("10.00"),
        mode="real_manual",
        profile=profiles["sts"],
        now=T0,
        note="kupon testowy",
    )
    before = services.prematch_overview(store, now=T0 + timedelta(minutes=1))
    assert before["monitored"] == before["on_coupons"] == 1
    assert before["without_data"] == 1 and before["last_update"] is None
    _lineup(
        store,
        event,
        confirmed=False,
        keeper=("p_gk1", "Bramkarz Jeden"),
        when=T0 + timedelta(minutes=2),
        checkpoint="T-6h",
    )
    after = services.prematch_overview(store, now=T0 + timedelta(minutes=3))
    assert after["predicted"] == 1 and after["last_update"] is not None


def test_browser_qa_runs_on_a_synthetic_home_only():
    import shutil

    from sports_edge import qa_browser

    home = qa_browser._home()
    try:
        assert (home / "data" / "demo" / "demo.db").exists()  # the DEMO offer only
        assert not (home / "data" / "sports_edge.db").exists()  # never a real database
        assert not (home / ".env").exists()  # never keys
        assert qa_browser.PAGES[0][0] == ""  # the default page lives at the root URL
        assert qa_browser.load_offline(home)  # no internet during the browser run
    finally:
        shutil.rmtree(home, ignore_errors=True)
