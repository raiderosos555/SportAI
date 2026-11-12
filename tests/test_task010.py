"""TASK 010 — one coupon, one history: coupon identity, lineage and statistics counted once.

Every team, price and coupon below is SYNTHETIC test data (the fixture STS offer of
offer_fixtures.py), not a real fact.
"""

import sqlite3
import subprocess
from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from conftest import ROOT

from sports_edge import coupon_store, paper, performance, reassessment
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.repository import ensure_reference_data, import_offer

DAY = "2026-10-31"
AT8 = datetime(2026, 10, 31, 8, tzinfo=UTC)
AT14 = datetime(2026, 10, 31, 14, tzinfo=UTC)


def _parsed(prices: dict | None = None):
    pytest.importorskip("pymupdf")
    import offer_fixtures

    from sports_edge.pdf.engine import parse_pdf

    result, _ = parse_pdf(offer_fixtures.sts_pdf(), file_name="oferta.pdf")
    if not prices:
        return result
    rows = []
    for row in result.rows:
        if row.market_key == "1x2" and row.offer_code in prices:
            row = dc_replace(
                row,
                selections=tuple(
                    dc_replace(s, odds=Decimal(prices[row.offer_code])) if s.key == "home" else s
                    for s in row.selections
                ),
            )
        rows.append(row)
    return dc_replace(result, rows=tuple(rows))


def _import(store, sha: str, at: datetime, prices: dict | None = None) -> str:
    return import_offer(
        store,
        _parsed(prices),
        sha256=sha * 64,
        file_name=f"oferta_{sha}.pdf",
        source_id="sts_pdf_manual",
        data_kind="prospective",
        ingested_at=at,
        # documents stored the way they were before TASK 016 (every document with its own
        # odds ids — as in the existing database); the thrifty write: tests/test_task016.py
        thrifty=False,
    ).import_id


def _odds(store, import_id: str, code: str, key: str = "home") -> str:
    rows = store.rows(
        """SELECT o.id FROM odds o JOIN quote_sets q ON q.id=o.quote_set_id
        JOIN markets m ON m.id=q.market_id JOIN selections s ON s.id=o.selection_id
        WHERE q.import_id=? AND q.offer_code=? AND m.market_key='1x2' AND m.period='FT'
        AND s.key=?""",
        (import_id, code, key),
    )
    assert rows, (import_id, code)
    return rows[0]["id"]


def _analysis(store) -> str:
    key = digest(["test-analysis", DAY])
    if store.get("analyses", key) is None:
        store.insert(
            "analyses",
            id=key,
            created_at=iso(AT8),
            analysis_date=DAY,
            policy_json=canonical({"version": "test"}),
            summary_json=canonical(
                {"headline": "test", "analysed": 0, "accepted": [], "counts": {}}
            ),
            items_json="[]",
        )
    return key


def _proposal(store, odds_ids: list[str], name: str = "standard"):
    legs = []
    for odds_id in odds_ids:
        row = store.rows(
            """SELECT o.id, o.price, o.selection_id, s.key, e.id event_id, e.home, e.away,
            e.league, e.start_at FROM odds o JOIN selections s ON s.id=o.selection_id
            JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id WHERE o.id=?""",
            (odds_id,),
        )[0]
        legs.append(
            SimpleNamespace(
                event=f"{row['home']} - {row['away']}",
                event_id=row["event_id"],
                league=row["league"],
                start_at=row["start_at"],
                market_label="Mecz",
                market_key="1x2",
                line=None,
                selection=row["key"],
                selection_id=row["selection_id"],
                offer_code=None,
                odds=Decimal(row["price"]),
                odds_id=odds_id,
                p_model=0.6,
                p_low=0.55,
                p_market=None,
                predictive_status="validated_predictive",
                calibration="",
                model="elo",
                model_version_id=None,
                document_at=None,
                odds_age_hours=0.0,
                freshness="fresh",
            )
        )
    total = Decimal(1)
    for leg in legs:
        total *= leg.odds
    return SimpleNamespace(
        profile=name,
        label=name.upper(),
        level="validated_predictive",
        p_calibrated=0.36,
        p_conservative=0.30,
        joint_basis="test",
        total_odds=total,
        stake=Decimal("10.00"),
        payout=Decimal("10.00") * total * Decimal("0.88"),
        net_profit=Decimal("10.00") * total * Decimal("0.88") - Decimal("10.00"),
        ev_net=Decimal(0),
        legs=legs,
        reasons=[],
        warnings=[],
    )


def _save(store, app_paths, odds_ids, now, name="standard"):
    built = SimpleNamespace(proposals={name: _proposal(store, odds_ids, name)})
    return coupon_store.save_proposals(
        store, app_paths, built, analysis_id=_analysis(store), now=now, day=DAY
    )[name]


def _relations(store) -> list[str]:
    return [
        r["relation"]
        for r in store.rows(
            """SELECT g.relation FROM coupon_lineage g JOIN coupons c ON c.id=g.coupon_id
            ORDER BY c.created_at, c.rowid"""
        )
    ]


@pytest.fixture
def two_imports(store):
    return _import(store, "a", AT8), _import(store, "b", AT14)


# 1 ---------------------------------------------------------------- the scenario of 1.1


def test_a_new_pdf_with_the_same_offer_never_makes_a_second_official_coupon(
    store, app_paths, two_imports
):
    first, second = two_imports
    legs1 = [_odds(store, first, "4101"), _odds(store, first, "4201")]
    legs2 = [_odds(store, second, "4101"), _odds(store, second, "4201")]
    assert set(legs1).isdisjoint(legs2)  # every STS document has its own odds ids
    official = _save(store, app_paths, legs1, AT8 + timedelta(minutes=5))
    again = _save(store, app_paths, legs1, AT8 + timedelta(minutes=6))  # another click
    after_pdf = _save(store, app_paths, legs2, AT14 + timedelta(minutes=5))  # the new PDF
    assert official["relation"] == "official"
    assert again["relation"] == after_pdf["relation"] == "duplicate"
    assert again["coupon_id"] == after_pdf["coupon_id"] == official["coupon_id"]
    assert "Bez zmian" in after_pdf["message"]
    assert store.scalar("SELECT COUNT(*) FROM coupons") == 1
    assert performance.coupon_performance(store)["standard"]["coupons"] == 1
    assert len(performance.selection_outcomes(store)) == 2


# 2 ---------------------------------------------------------------- price update


def test_another_price_of_one_leg_is_a_price_update_not_a_new_official_coupon(store, app_paths):
    first = _import(store, "a", AT8)
    moved = _import(store, "b", AT14, prices={"4101": "2.30"})
    official = _save(store, app_paths, [_odds(store, first, "4101")], AT8 + timedelta(minutes=5))
    update = _save(store, app_paths, [_odds(store, moved, "4101")], AT14 + timedelta(minutes=5))
    assert update["relation"] == "price_update"
    assert update["official_id"] == official["coupon_id"] != update["coupon_id"]
    assert update["changes"]["prices"][0]["after"] == "2.30"
    assert update["changes"]["total_odds"] == ["2.10", "2.30"]
    assert update["changes"]["payout"] == ["18.48", "20.24"]  # as on the card: 10 × odds × 0.88
    assert "Real Madrid" in update["changes"]["prices"][0]["selection"]
    assert ": Wygrana: " in update["changes"]["prices"][0]["selection"]  # words, not keys
    assert _relations(store) == ["official", "price_update"]
    saved = coupon_store.saved_proposals(store, DAY)["standard"]
    assert saved["coupon_id"] == official["coupon_id"]
    assert saved["newest_version"]["relation"] == "price_update"
    assert performance.coupon_performance(store)["standard"]["coupons"] == 1
    # the next analysis with the same newer prices repeats the UPDATE, not the official coupon
    again = _save(store, app_paths, [_odds(store, moved, "4101")], AT14 + timedelta(minutes=9))
    assert again["relation"] == "duplicate" and again["coupon_id"] == update["coupon_id"]
    assert again["version_relation"] == "price_update"
    assert again["message"] == (
        "Bez zmian — aktualizacja kursów z 15:05 jest aktualna; "
        "kupon dnia z 09:05 pozostaje w statystykach"
    )
    assert store.scalar("SELECT COUNT(*) FROM coupons") == 2


# 3 ---------------------------------------------------------------- revision


def test_other_legs_of_the_same_profile_and_day_are_a_revision(store, app_paths):
    first = _import(store, "a", AT8)
    official = _save(store, app_paths, [_odds(store, first, "4101")], AT8)
    revision = _save(
        store,
        app_paths,
        [_odds(store, first, "4101"), _odds(store, first, "4202")],
        AT8 + timedelta(hours=1),
    )
    assert revision["relation"] == "revision"
    assert revision["official_id"] == official["coupon_id"]
    assert len(revision["changes"]["added"]) == 1 and not revision["changes"]["removed"]
    assert (
        coupon_store.saved_proposals(store, DAY)["standard"]["coupon_id"] == (official["coupon_id"])
    )
    other_profile = _save(store, app_paths, [_odds(store, first, "4202")], AT8, name="high_hit")
    assert other_profile["relation"] == "official"  # one official coupon per PROFILE and day


# 4 ---------------------------------------------------------------- no hindsight


def test_nothing_replaces_the_official_coupon_once_a_match_started(store, app_paths):
    first = _import(store, "a", AT8)
    leg = _odds(store, first, "4101")
    official = _save(store, app_paths, [leg], AT8)
    start = datetime.fromisoformat(
        store.rows(
            """SELECT e.start_at FROM odds o JOIN selections s ON s.id=o.selection_id
            JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id WHERE o.id=?""",
            (leg,),
        )[0]["start_at"]
    )
    late = _save(store, app_paths, [_odds(store, first, "4202")], start + timedelta(minutes=1))
    assert "error" in late or late["relation"] == "revision"
    officials = store.rows("SELECT coupon_id FROM coupon_lineage WHERE relation='official'")
    assert [r["coupon_id"] for r in officials] == [official["coupon_id"]]
    rules = coupon_store.load_probability_policy(ROOT / "config" / "probability_policy.toml")
    assert rules.official_rule == "first"


# 5 ---------------------------------------------------------------- backfill


def _old_style(store, profiles, odds_ids, now, analysis_id):
    """A coupon saved the way the code saved it before TASK 010 (key with the odds ids)."""
    payload = coupon_store.proposal_payload(
        _proposal(store, odds_ids), policy_version="2026-11-02.p3"
    )
    return paper.create_coupon(
        store,
        odds_ids=odds_ids,
        stake=Decimal("10.00"),
        mode="paper_system",
        profile=profiles["sts"],
        now=now,
        analysis_id=analysis_id,
        note="Kreator kuponów — STANDARDOWY",
        proposal=payload,
        key=digest(["proposal", "standard", sorted(odds_ids), "2026-11-02.p3", DAY]),
    )


def test_backfill_classifies_old_duplicates_and_is_idempotent(store, profiles, two_imports):
    first, second = two_imports
    analysis = _analysis(store)
    one = _old_style(store, profiles, [_odds(store, first, "4101")], AT8, analysis)
    two = _old_style(store, profiles, [_odds(store, second, "4101")], AT14, analysis)
    assert one != two  # the bug of 1.1: two coupons with the same match and price
    counts = coupon_store.backfill_lineage(store, now=AT14)
    assert counts == {"official": 1, "duplicate": 1}
    assert coupon_store.backfill_lineage(store, now=AT14) == {}  # a second run adds nothing
    assert _relations(store) == ["official", "duplicate"]
    assert store.scalar("SELECT COUNT(*) FROM coupons") == 2  # nothing deleted


# 6-7 -------------------------------------------------------------- statistics


def test_statistics_count_a_coupon_once_and_a_settled_duplicate_changes_nothing(
    store, profiles, two_imports
):
    first, second = two_imports
    analysis = _analysis(store)
    one = _old_style(store, profiles, [_odds(store, first, "4101")], AT8, analysis)
    two = _old_style(store, profiles, [_odds(store, second, "4101")], AT14, analysis)
    coupon_store.backfill_lineage(store, now=AT14)
    assert performance.coupon_performance(store)["standard"]["coupons"] == 1
    assert paper.performance(store)["paper_system"]["coupons"] == 1
    assert len(performance.selection_outcomes(store)) == 1
    event = store.rows(
        """SELECT e.id, e.start_at FROM coupon_legs l JOIN selections s ON s.id=l.selection_id
        JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id
        WHERE l.coupon_id=?""",
        (one,),
    )[0]
    after = datetime.fromisoformat(event["start_at"]) + timedelta(hours=3)
    paper.record_result(
        store, event_id=event["id"], status="completed", now=after, home_score=2, away_score=0
    )
    paper.settle_coupon(store, one, profiles, now=after)
    before = performance.coupon_performance(store)["standard"]
    roi_before = paper.performance(store)["paper_system"]["roi"]
    paper.settle_coupon(store, two, profiles, now=after)  # the duplicate settles too
    later = performance.coupon_performance(store)["standard"]
    assert (later["settled"], later["hit_rate"], later["roi"]) == (
        before["settled"],
        before["hit_rate"],
        before["roi"],
    )
    assert paper.performance(store)["paper_system"]["roi"] == roi_before
    assert paper.latest_settlement(store, two) is not None  # history stays complete


# 8-9 -------------------------------------------------------------- reassessment and alerts


def test_one_change_is_one_alert_listing_the_coupons_and_lineups_once(
    store, app_paths, profiles, two_imports
):
    from sports_edge import services

    first, second = two_imports
    analysis = _analysis(store)
    leg1 = _odds(store, first, "4101")
    official = _save(store, app_paths, [leg1], AT8)["coupon_id"]
    _old_style(store, profiles, [_odds(store, second, "4101")], AT14, analysis)  # a duplicate
    coupon_store.backfill_lineage(store, now=AT14)
    mine = coupon_store.save_manual(
        store,
        odds_ids=[leg1],
        stake=Decimal("10"),
        mode="paper_manual",
        sts=profiles["sts"],
        now=AT8,
    )
    later = _import(store, "d", AT14 + timedelta(minutes=10), prices={"4101": "2.20"})
    placed = coupon_store.save_manual(  # the same selection, saved at another price
        store,
        odds_ids=[_odds(store, later, "4101")],
        stake=Decimal("10"),
        mode="real_manual",
        sts=profiles["sts"],
        now=AT14 + timedelta(minutes=15),
    )
    _import(store, "c", AT14 + timedelta(minutes=30), prices={"4101": "2.40"})  # +14% / +9%
    found = reassessment.reassess_all(store, now=AT14 + timedelta(hours=1))
    assert found["coupons"] == 3  # official + 2 user coupons; the duplicate is not re-checked
    alerts = store.rows("SELECT * FROM prematch_alerts WHERE kind='coupon_attention'")
    assert len(alerts) == 1  # one market move = one alert, whatever each coupon's price was
    assert "kurs zmienił się na 2.40" in alerts[0]["message"]
    assert "dotyczy kuponów: 3" in alerts[0]["message"]
    import json

    assert sorted(json.loads(alerts[0]["payload_json"])["coupons"]) == sorted(
        [official, mine.coupon_id, placed.coupon_id]
    )
    reasons = {r["coupon_id"]: r for r in store.rows("SELECT * FROM coupon_reassessments")}
    assert "z 2.20 na 2.40" in reasons[placed.coupon_id]["reasons_json"]  # per coupon: its own
    assert reassessment.reassess_all(store, now=AT14 + timedelta(hours=2))["alerts"] == 0
    targets = services.prematch_targets(store, now=AT8, horizon_hours=48)
    ids = [t["id"] for t in targets if t["priority"] == 1]
    assert len(ids) == len(set(ids)) == 1  # one lineup check per match


# 10 --------------------------------------------------------------- double clicks


def test_a_double_click_saves_one_coupon_on_every_manual_path(store, profiles, two_imports):
    first, _second = two_imports
    leg = _odds(store, first, "4201")
    sts = profiles["sts"]
    for mode in ("paper_manual", "paper_system", "real_manual"):  # Historia, Diagnostyka, STS
        one = coupon_store.save_manual(
            store, odds_ids=[leg], stake=Decimal("10.00"), mode=mode, sts=sts, now=AT8
        )
        two = coupon_store.save_manual(
            store,
            odds_ids=[leg],
            stake=Decimal("10"),
            mode=mode,
            sts=sts,
            now=AT8 + timedelta(seconds=2),
        )
        assert two.relation == "duplicate" and two.coupon_id == one.coupon_id
        assert two.message == coupon_store.DUPLICATE_MESSAGE
    assert store.scalar("SELECT COUNT(*) FROM coupons") == 3
    copy = coupon_store.save_manual(
        store,
        odds_ids=[leg],
        stake=Decimal("10"),
        mode="real_manual",
        sts=sts,
        now=AT8,
        confirm_copy=True,  # placed twice in STS: only with an explicit confirmation
    )
    assert copy.relation == "user" and store.scalar("SELECT COUNT(*) FROM coupons") == 4


def test_my_coupon_saved_twice_is_one_coupon(store, app_paths, two_imports):
    from sports_edge import services

    first, _second = two_imports
    from sports_edge.settings import load_settings

    settings = load_settings(app_paths)
    day = DAY
    noon = datetime(2026, 10, 31, 12, 30, tzinfo=UTC)  # the fixture PDF is from 12:00
    report, candidates = services.analyse_day(store, app_paths, settings, day, now=noon)
    wanted = store.get("odds", _odds(store, first, "4201"))["selection_id"]
    row = next(c for c in candidates if c.selection_id == wanted)
    from sports_edge import my_coupon

    cart = [my_coupon.leg_from(row, day=day, now=noon)]
    one = services.save_my_coupon_result(
        store, app_paths, cart, [row], stake=Decimal("10"), now=noon
    )
    two = services.save_my_coupon_result(
        store, app_paths, cart, [row], stake=Decimal("10"), now=noon + timedelta(seconds=1)
    )
    assert one.relation == "user" and two.relation == "duplicate"
    assert store.scalar("SELECT COUNT(*) FROM coupons") == 1
    del report


# 11 --------------------------------------------------------------- diversity option


def test_profile_diversity_is_off_by_default_and_never_lowers_thresholds():
    from test_task009_1 import POLICY, _experimental, _validated, build

    from sports_edge.analysis.coupon_builder import build_coupons

    assert int(POLICY.builder["max_profiles_per_selection"]) == 0
    pool = [*_experimental(30), *_validated(71)]
    default = build(pool)
    off = build_coupons(
        pool, POLICY, cost=_cost(), on_date=_cost().valid_from, max_profiles_per_selection=0
    )
    for name, proposal in default.proposals.items():
        other = off.proposals[name]
        assert (proposal is None) == (other is None)
        if proposal is not None:
            assert [x.odds_id for x in proposal.legs] == [x.odds_id for x in other.legs]
    limited = build_coupons(
        pool, POLICY, cost=_cost(), on_date=_cost().valid_from, max_profiles_per_selection=1
    )
    uses: dict[str, int] = {}
    for name, proposal in limited.proposals.items():
        if proposal is None:
            continue
        floor = POLICY.profiles[name]["min_leg_probability"]
        assert all(leg.p_low >= floor for leg in proposal.legs)
        for leg in proposal.legs:
            uses[leg.selection_id] = uses.get(leg.selection_id, 0) + 1
    assert uses and max(uses.values()) == 1


def _cost():
    from test_task005 import STS

    return STS.cost


# 12 --------------------------------------------------------------- migration


def test_migration_to_v8_backs_up_and_keeps_every_row(tmp_path):
    from sports_edge.storage import SCHEMA_VERSION, Store

    path = tmp_path / "old.db"
    store = Store(path)
    ensure_reference_data(store)
    store.close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE coupon_lineage")
    raw.execute("PRAGMA user_version=7")
    tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {t: raw.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    raw.commit()
    raw.close()
    migrated = Store(path, backup_dir=tmp_path / "backups")
    assert SCHEMA_VERSION >= 8  # TASK 011: v9 adds market_benchmarks on top of v8
    assert migrated.migration_backup is not None and migrated.migration_backup.exists()
    assert migrated.migration_backup.name.startswith(f"pre-migration-v{SCHEMA_VERSION}_")
    for table, n in counts.items():
        if table == "schema_migrations":
            continue
        assert migrated.scalar(f"SELECT COUNT(*) FROM {table}") == n, table
    assert "coupon_lineage" in migrated.tables
    query = "SELECT name FROM sqlite_master WHERE type='trigger'"
    triggers = {r[0] for r in migrated.connection.execute(query)}
    assert {"immutable_coupon_lineage_UPDATE", "immutable_coupon_lineage_DELETE"} <= triggers
    migrated.close()


# 13 --------------------------------------------------------------- the public repository


def test_database_pdfs_keys_and_exports_never_enter_the_repository():
    def ignored(path: str) -> bool:
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT, check=False
            ).returncode
            == 0
        )

    for path in (
        "data/sports_edge.db",
        "x.db",
        "oferta.pdf",
        "docs/oferta.pdf",
        ".env",
        "export.xlsx",
        "eksport.csv",
        "backups/a.db",
    ):
        assert ignored(path), path
    assert not ignored("tests/fixtures/sts/oferta.pdf")
    assert not ignored("src/sports_edge/coupon_store.py")


# 14 --------------------------------------------------------------- interface


def test_coupon_card_badges_and_history_hides_duplicates(app_paths, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from sports_edge.app.coupon_view import status_html

    payload = {"legs": [{"document_at": "2026-10-31T13:00:00+00:00"}]}
    official = status_html({"relation": "official", "official_created_at": iso(AT8)}, payload)
    same = status_html({"relation": "duplicate", "official_created_at": iso(AT8)}, payload)
    prices = status_html(
        {
            "relation": "price_update",
            "official_created_at": iso(AT8),
            "changes": {"total_odds": ["2.10", "2.30"], "payout": ["18.48", "20.24"]},
        },
        payload,
    )
    revision = status_html(
        {
            "relation": "revision",
            "official_created_at": iso(AT8),
            "created_at": iso(AT14),
            "changes": {"added": ["A - B: 1x2 home"], "removed": []},
        },
        payload,
    )
    assert "Oficjalny kupon dnia" in official
    assert "Bez zmian — kupon dnia z 09:00 jest aktualny" in same
    assert "Kursy zaktualizowane (PDF z 14:00)" in prices and "2,30" in prices
    assert "Rewizja z 15:00" in revision and "Dodane: A - B" in revision
    repeated = status_html(  # "Analizuj" again: the proposal repeats the revision of 15:00
        {
            "relation": "duplicate",
            "version_relation": "revision",
            "version_created_at": iso(AT14),
            "official_created_at": iso(AT8),
            "changes": {"added": ["A - B: 1x2 home"], "removed": []},
        },
        payload,
    )
    assert "Rewizja z 15:00" in repeated and "Bez zmian — rewizja z 15:00 jest aktualna" in repeated
    assert "Oficjalny kupon dnia z 09:00 pozostaje w statystykach" in repeated
    assert "jest aktualny" not in repeated  # never "the official coupon is current"
    # Historia: a duplicate saved by older code is hidden unless "Wersje" is switched on
    from sports_edge.services import open_store

    store = open_store(app_paths)
    first, second = _import(store, "a", AT8), _import(store, "b", AT14)
    from sports_edge.bookmakers import load_profiles

    profiles = load_profiles(app_paths.config)
    analysis = _analysis(store)
    _old_style(store, profiles, [_odds(store, first, "4101")], AT8, analysis)
    _old_style(store, profiles, [_odds(store, second, "4101")], AT14, analysis)
    coupon_store.backfill_lineage(store, now=AT14)
    store.close()
    from sports_edge.app import common

    monkeypatch.setattr(common, "now", lambda: datetime(2026, 10, 31, 15, tzinfo=UTC))
    app = AppTest.from_string(
        "from sports_edge.app.pages_history import history_page\nhistory_page()\n",
        default_timeout=60,
    )
    app.session_state["demo_mode"] = False
    app.run()
    assert not app.exception
    shown = [d.value for d in app.dataframe if "Profil" in d.value.columns]
    assert shown and len(shown[0]) == 1  # the official coupon only
    virtual = [d.value for d in app.dataframe if "Tryb" in d.value.columns]
    assert len(virtual[0]) == 1 and virtual[0]["Relacja"].iloc[0] == "Oficjalny kupon dnia"
    app.toggle(key="hist_versions").set_value(True).run()
    shown = [d.value for d in app.dataframe if "Profil" in d.value.columns]
    assert len(shown[0]) == 2 and shown[0]["Profil"].iloc[1].startswith("↳ ")
    app.toggle(key="hist_all_coupons").set_value(True).run()
    virtual = [d.value for d in app.dataframe if "Tryb" in d.value.columns]
    assert sorted(virtual[0]["Relacja"]) == ["Duplikat", "Oficjalny kupon dnia"]


def test_kupon_dnia_shows_the_official_coupon_then_no_change_after_a_second_analysis(
    app_paths, monkeypatch
):
    """The page of the scenario of 1.1: "Analizuj" twice gives one coupon and the message
    "Bez zmian" instead of a new card. The analysis itself is replaced by a stub that saves a
    synthetic proposal through the real ``save_proposals`` (the demo teams have no model)."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from sports_edge import services
    from sports_edge.analysis.coupon_builder import BuilderReport
    from sports_edge.app import common, page_coupon

    store = services.open_store(app_paths)
    legs = [_odds(store, _import(store, "a", AT8), "4101")]
    store.close()

    def analysis(db, paths, settings, days, *, now, **_options):
        runs = {}
        for day in days:
            built = BuilderReport(proposals={"standard": _proposal(db, legs)}, refusals={}, pool=())
            saves = coupon_store.save_proposals(
                db, paths, built, analysis_id=_analysis(db), now=now, day=day
            )
            runs[day] = services.PipelineRun(
                report=SimpleNamespace(headline="test", counts={}),
                candidates=[],
                analysis_id=_analysis(db),
                stages=[],
                coupons=built,
                coupon_ids={name: r["coupon_id"] for name, r in saves.items()},
                day=day,
                coupon_saves=saves,
            )
        return services.RangeRun(days=list(days), runs=runs, stages=[], refresh=[], run_at="")

    monkeypatch.setattr(page_coupon, "run_analysis", analysis)
    monkeypatch.setattr(common, "now", lambda: datetime(2026, 10, 31, 12, 30, tzinfo=UTC))
    app = AppTest.from_string(
        "from sports_edge.app.page_coupon import coupon_page\ncoupon_page()\n",
        default_timeout=120,
    )
    app.session_state["demo_mode"] = False
    app.run()

    def analyse() -> str:
        next(b for b in app.button if b.label.startswith("ANALIZUJ")).click().run()
        assert not app.exception, [e.value for e in app.exception]
        return " ".join(m.value for m in app.markdown)

    first = analyse()
    assert "Oficjalny kupon dnia" in first and "Bez zmian" not in first
    second = analyse()
    assert "Bez zmian — kupon dnia z 13:30 jest aktualny" in second  # 12:30 UTC, Warsaw
    store = services.open_store(app_paths)
    assert store.scalar("SELECT COUNT(*) FROM coupons") == 1
    assert _relations(store) == ["official"]
    store.close()


def test_a_settled_user_coupon_without_a_model_probability_keeps_the_panels_working(
    store, profiles, two_imports
):
    """Found on the copy of the real database: MÓJ KUPON on a market without a validated
    model has no probability; once settled, the performance panels must not fail."""
    first, _second = two_imports
    leg = _odds(store, first, "4201")
    payload = {
        "profile": "my_coupon",
        "label": "MÓJ KUPON",
        "source": "user",
        "policy_version": "test",
        "level": "experimental",
        "probability": {"calibrated": None, "conservative": None, "basis": "brak modelu"},
        "total_odds": "1.80",
        "stake": "10.00",
        "legs": [coupon_store._leg(x) for x in _proposal(store, [leg]).legs],
    }
    mine = coupon_store.save_manual(
        store,
        odds_ids=[leg],
        stake=Decimal("10"),
        mode="real_manual",
        sts=profiles["sts"],
        now=AT8,
        proposal=payload,
    )
    event = store.rows(
        """SELECT e.id, e.start_at FROM coupon_legs l JOIN selections s ON s.id=l.selection_id
        JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id
        WHERE l.coupon_id=?""",
        (mine.coupon_id,),
    )[0]
    after = datetime.fromisoformat(event["start_at"]) + timedelta(hours=3)
    paper.record_result(
        store, event_id=event["id"], status="completed", now=after, home_score=0, away_score=1
    )
    paper.settle_coupon(store, mine.coupon_id, profiles, now=after)
    stats = performance.coupon_performance(store)["my_coupon"]
    assert stats["settled"] == 1 and stats["wins"] == 0
    assert stats["avg_predicted"] is None and stats["avg_predicted_conservative"] is None


def test_leg_outcomes_answer_for_every_coupon_with_the_same_selection(
    store, app_paths, profiles, two_imports
):
    """selection_outcomes keeps one row per selection; the details of a coupon (also of a
    version outside the statistics) still show the outcome of each of its legs."""
    first, second = two_imports
    one = _save(store, app_paths, [_odds(store, first, "4101")], AT8)["coupon_id"]
    other = _save(store, app_paths, [_odds(store, second, "4101")], AT14, name="high_hit")
    revision = _save(
        store,
        app_paths,
        [_odds(store, second, "4101"), _odds(store, second, "4202")],
        AT14 + timedelta(minutes=1),
    )
    assert other["relation"] == "official" and revision["relation"] == "revision"
    event = store.rows(
        """SELECT e.id, e.start_at FROM coupon_legs l JOIN selections s ON s.id=l.selection_id
        JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id
        WHERE l.coupon_id=?""",
        (one,),
    )[0]
    after = datetime.fromisoformat(event["start_at"]) + timedelta(hours=3)
    paper.record_result(
        store, event_id=event["id"], status="completed", now=after, home_score=2, away_score=0
    )
    assert len(performance.selection_outcomes(store)) == 1  # one selection, counted once
    for coupon_id in (one, other["coupon_id"], revision["coupon_id"]):
        legs = store.rows("SELECT * FROM coupon_legs WHERE coupon_id=?", (coupon_id,))
        found = performance.leg_outcomes(store, coupon_id)
        home = next(x for x in legs if x["selection_id"] == legs[0]["selection_id"])
        assert found[home["selection_id"]] == found[home["odds_id"]]
        assert "win" in found.values()


def test_history_works_with_a_settled_coupon_without_a_forecast(app_paths, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from sports_edge.app import common
    from sports_edge.bookmakers import load_profiles
    from sports_edge.services import open_store

    store = open_store(app_paths)
    profiles = load_profiles(app_paths.config)
    leg = _odds(store, _import(store, "a", AT8), "4201")
    payload = {
        "profile": "my_coupon",
        "label": "MÓJ KUPON",
        "source": "user",
        "policy_version": "test",
        "level": "experimental",
        "probability": {"calibrated": None, "conservative": None, "basis": "brak modelu"},
        "total_odds": "1.80",
        "stake": "10.00",
        "legs": [
            {**coupon_store._leg(x), "p_model": None, "p_conservative": None}
            for x in _proposal(store, [leg]).legs
        ],
    }
    mine = coupon_store.save_manual(
        store,
        odds_ids=[leg],
        stake=Decimal("10"),
        mode="real_manual",
        sts=profiles["sts"],
        now=AT8,
        proposal=payload,
    )
    event = store.rows(
        """SELECT e.id, e.start_at FROM coupon_legs l JOIN selections s ON s.id=l.selection_id
        JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id
        WHERE l.coupon_id=?""",
        (mine.coupon_id,),
    )[0]
    after = datetime.fromisoformat(event["start_at"]) + timedelta(hours=3)
    paper.record_result(
        store, event_id=event["id"], status="completed", now=after, home_score=0, away_score=1
    )
    paper.settle_coupon(store, mine.coupon_id, profiles, now=after)
    for by in ("league", "range"):
        assert performance.selection_summary(performance.selection_outcomes(store), by) == []
    store.close()
    monkeypatch.setattr(common, "now", lambda: after + timedelta(hours=1))
    app = AppTest.from_string(
        "from sports_edge.app.pages_history import history_page\nhistory_page()\n",
        default_timeout=60,
    )
    app.session_state["demo_mode"] = False
    app.run()
    assert not app.exception, [e.value for e in app.exception]


def test_the_diversity_replay_measures_every_limit_without_saving(store, app_paths, two_imports):
    from datetime import time

    from sports_edge.settings import load_settings
    from sports_edge.simulation import diversity_replay

    coupons = store.scalar("SELECT COUNT(*) FROM coupons")
    found = diversity_replay(
        store, app_paths, load_settings(app_paths), [DAY], decision_time=time(13, 30)
    )
    assert set(found["limits"]) == {0, 1, 2}
    for limit in (0, 1, 2):
        row = found["limits"][limit]
        assert row["days"] == 1 and row["coupons"] >= 0
        assert row["days_one_loss_2plus"] <= row["days_with_coupon"] <= 1
    assert found["limits"][1]["days_one_loss_2plus"] == 0  # one selection, one coupon at most
    assert found["per_day"][0]["day"] == DAY and found["per_day"][0]["candidates"] > 0
    assert store.scalar("SELECT COUNT(*) FROM coupons") == coupons  # a measurement saves nothing
