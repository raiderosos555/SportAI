"""Historia i wyniki: analyses, virtual coupons, results, settlement, ROI, drawdown, CLV."""

import json
from datetime import datetime
from decimal import Decimal

import pandas as pd
import streamlit as st

from sports_edge import insights as ins
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.bookmakers import load_profiles
from sports_edge.coupon_store import (
    DUPLICATE_MESSAGE,
    RELATION_LABELS,
    prediction_ids_of,
    save_manual,
)
from sports_edge.forecast_quality import coupon_review
from sports_edge.paper import (
    MODES,
    closing_line_value,
    coupons_table,
    import_source_results,
    latest_result,
    performance,
    record_result,
    settle_coupon,
)
from sports_edge.performance import (
    builder_coupons,
    coupon_performance,
    leg_outcomes,
    selection_outcomes,
    selection_summary,
)
from sports_edge.repository import offer_days, offer_table
from sports_edge.services import normalizer, team_aliases, team_identities


def history_page() -> None:
    c.setup_page()
    ui.app_header("Historia i skuteczność", "Czy propozycje kuponów rzeczywiście trafiają?")
    db = c.store()
    _overview(db)
    ui.section(
        "Szczegóły", "kupony, wyniki, rozliczenia, skuteczność według profilu, CLV i analizy"
    )
    tabs = st.tabs(
        [
            "Kupony wirtualne",
            "Wyniki i rozliczenia",
            "Skuteczność kreatora kuponów",
            "Skuteczność i CLV",
            "Analizy",
        ]
    )
    with tabs[0]:
        _coupons(db)
    with tabs[1]:
        _results(db)
    with tabs[2]:
        _builder_performance(db)
    with tabs[3]:
        _performance(db)
    with tabs[4]:
        rows = db.rows("SELECT * FROM analyses ORDER BY created_at DESC LIMIT 200")
        if not rows:
            st.info("Brak zapisanych analiz.")
            return
        records = []
        for r in rows:
            summary = json.loads(r["summary_json"])
            counts = summary.get("counts", {})
            records.append(
                {
                    "Utworzono": c.local_time(r["created_at"]),
                    "Dzień": r["analysis_date"],
                    "Wynik": summary["headline"],
                    "Selekcje": summary["analysed"],
                    "EV netto > 0": counts.get("ev_positive"),
                    "Eksperymentalne": counts.get("experimental"),
                    "Zwalidowane": len(summary["accepted"]),
                    "Polityka": json.loads(r["policy_json"])["version"],
                }
            )
        st.dataframe(pd.DataFrame(records), hide_index=True, width="stretch")
        st.caption(
            "Analizy są niezmienne: nowe kursy tworzą nową analizę, stare zostają w historii."
        )


def _coupons(db) -> None:
    rows = coupons_table(db)
    relation = {
        r["coupon_id"]: r["relation"]
        for r in db.rows("SELECT coupon_id, relation FROM coupon_lineage")
    }
    hidden = sum(relation.get(r["id"]) in VERSIONED for r in rows)
    every = hidden and st.toggle(
        "Wersje i duplikaty",
        key="hist_all_coupons",
        help="Pokaż też rewizje, aktualizacje kursów i duplikaty kuponów dnia (poza statystykami).",
    )
    if not every:
        rows = [r for r in rows if relation.get(r["id"]) not in VERSIONED]
    if rows:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Utworzono": c.local_time(r["created_at"]),
                        "Relacja": RELATION_LABELS.get(relation.get(r["id"]), "—"),
                        "Tryb": MODES[r["mode"]],
                        "Typ": r["kind"].upper(),
                        "Zdarzenia": " | ".join(
                            f"{leg['home']} - {leg['away']}: {c.MARKETS.get(leg['market_key'])} "
                            f"{c.SELECTIONS.get(leg['selection'], leg['selection'])} @ {leg['price']}"
                            for leg in r["legs"]
                        ),
                        "Stawka": r["stake"],
                        "Kurs łączny": round(float(r["total_odds"]), 3),
                        "Wynik": r["outcome"] or "oczekuje",
                        "Zysk netto": r["net_profit"],
                    }
                    for r in rows
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        if hidden and not every:
            st.caption(
                f"Ukryte wersje i duplikaty kuponów dnia (poza statystykami): {hidden} — "
                "włącz „Wersje i duplikaty”, aby je zobaczyć."
            )
    else:
        st.info("Brak wirtualnych kuponów.")
    with st.expander("Dodaj wirtualny kupon (wybór użytkownika — nie rekomendacja systemu)"):
        days = offer_days(db)
        if not days:
            st.write("Brak ofert.")
            return
        day = st.selectbox("Dzień", days, index=len(days) - 1, key="manual_day")
        rows = offer_table(db, day=day, include_review=False, data_kinds=c.data_kinds())
        upcoming = [
            r
            for r in rows
            if r["start_at"] and r["start_at"] > c.now().isoformat() and r["presence"] == "current"
        ]
        if not upcoming:
            st.write("Brak nadchodzących wydarzeń z aktualnymi kursami.")
            return
        options = {}
        for r in upcoming:
            for key, quote in r["selections"].items():
                options[quote["odds_id"]] = (
                    f"{c.local_time(r['start_at'], '%H:%M')} · {' - '.join(r['participants'])} · "
                    f"{c.MARKETS.get(r['market_key'])} {r['line'] or ''} · "
                    f"{c.SELECTIONS.get(key, key)} @ {quote['price']}"
                )
        chosen = st.multiselect(
            "Selekcje (AKO: różne wydarzenia)",
            list(options),
            format_func=options.get,
            placeholder="wybierz…",
        )
        stake = st.number_input("Stawka (PLN)", min_value=1.0, value=10.0, step=1.0)
        mode = st.selectbox("Tryb", ["paper_manual", "simulation"], format_func=MODES.get)
        if st.button("Zapisz kupon", disabled=not chosen):
            # TASK 013 §4: every leg keeps the forecast its chance comes from (the day's
            # candidates, as in Kupon dnia); a leg without a forecast keeps an empty id
            _report, candidates = c.day_candidates(day)
            wanted = set(chosen)
            try:
                result = save_manual(
                    db,
                    odds_ids=chosen,
                    stake=Decimal(f"{stake:.2f}"),
                    mode=mode,
                    sts=load_profiles(c.paths().config)["sts"],
                    now=c.now(),
                    note="wybór użytkownika",
                    prediction_ids=prediction_ids_of(
                        [x for x in candidates if x.odds_id in wanted]
                    ),
                )
                if result.relation == "duplicate":
                    st.info(DUPLICATE_MESSAGE)
                else:
                    st.success("Zapisano.")
            except ValueError as error:
                st.error(str(error))


def _results(db) -> None:
    pending = db.rows(
        """SELECT DISTINCT e.id, e.home, e.away, e.start_at, e.sport FROM coupon_legs l
        JOIN selections s ON s.id=l.selection_id JOIN markets m ON m.id=s.market_id
        JOIN events e ON e.id=m.event_id ORDER BY e.start_at"""
    )
    if not pending:
        st.info("Brak kuponów do rozliczenia.")
        return
    st.caption(
        "Wyniki: automatycznie z niezależnych źródeł (hierarchia: organizator > football-data.org "
        "> openfootball) tylko przy pewnym dopasowaniu i bez rozbieżności między źródłami; "
        "rynki, których nie rozstrzyga wynik końcowy (np. rzuty rożne), czekają na dodatkowe "
        "dane. Korekta tworzy nowy rekord, a selekcja nierozstrzygnięta nigdy nie jest przegrana."
    )
    if st.button("Przenieś pewne wyniki z danych niezależnych (lokalnie)"):
        out = import_source_results(
            db,
            now=c.now(),
            normalizer=normalizer(c.paths()),
            team_aliases=team_aliases(c.paths()),
            identities=team_identities(c.paths()),
        )
        st.write(f"Zapisano wyników: {out['recorded']}")
        for line in out["unresolved"][:20]:
            st.write("• " + line)
    labels = {
        e["id"]: f"{c.local_time(e['start_at'])} {e['home']} - {e['away']} "
        f"[{'wynik zapisany' if latest_result(db, e['id']) else 'brak wyniku'}]"
        for e in pending
    }
    event = st.selectbox("Wydarzenie", list(labels), format_func=labels.get)
    with st.form("result"):
        status = st.selectbox(
            "Status", ["completed", "void", "postponed", "cancelled", "abandoned"]
        )
        cols = st.columns(4)
        home = cols[0].number_input("Gospodarze (czas regulaminowy)", min_value=0, step=1)
        away = cols[1].number_input("Goście (czas regulaminowy)", min_value=0, step=1)
        ht_home = cols[2].number_input("1. połowa — gospodarze", min_value=-1, value=-1, step=1)
        ht_away = cols[3].number_input("1. połowa — goście", min_value=-1, value=-1, step=1)
        winner = st.selectbox("Zwycięzca (tenis, MMA itp.)", ["—", "home", "away"])
        source = st.text_input("Źródło wyniku (np. oficjalna strona ligi)")
        verified = st.checkbox("Wynik sprawdzony w oficjalnym źródle")
        if st.form_submit_button("Zapisz wynik"):
            try:
                record_result(
                    db,
                    event_id=event,
                    status=status,
                    now=c.now(),
                    home_score=int(home) if status == "completed" else None,
                    away_score=int(away) if status == "completed" else None,
                    ht_home=int(ht_home) if ht_home >= 0 else None,
                    ht_away=int(ht_away) if ht_away >= 0 else None,
                    details={"winner": winner} if winner != "—" else {},
                    source=source or "manual",
                    verified=verified,
                )
                st.success("Zapisano wynik.")
            except ValueError as error:
                st.error(str(error))
    if st.button("Rozlicz kupony"):
        profiles = load_profiles(c.paths().config)
        for coupon in db.rows("SELECT id FROM coupons"):
            outcome = settle_coupon(db, coupon["id"], profiles, now=c.now())
            status = {
                "pending": "oczekuje",
                "manual": "rozliczenie ręczne",
                "settled": "rozliczony",
            }
            st.write(
                f"`{coupon['id'][:10]}…` → {status.get(outcome['status'], outcome['status'])}",
                outcome.get("net_profit", ""),
                outcome.get("reason", ""),
                "; ".join(outcome.get("reasons", [])),
            )


MIN_FOR_ROI = 30  # fewer settled coupons: ROI is shown only with a clear warning
OUTCOME = {
    "win": ("Trafiony", "ok"),
    "loss": ("Nietrafiony", "bad"),
    "push": ("Zwrot", "warn"),
    "void": ("Unieważniony", "warn"),
}


def _overview(db) -> None:
    coupons = builder_coupons(db)
    settled = [x for x in coupons if x["outcome"]]
    decided = [x for x in settled if x["outcome"] in {"win", "loss"}]
    wins = sum(x["outcome"] == "win" for x in decided)
    stake = sum((Decimal(x["stake"]) for x in settled), Decimal(0))
    profit = sum((x["net_profit"] for x in settled), Decimal(0))
    predicted = [
        x["proposal"]["probability"]["calibrated"]
        for x in decided
        if x["proposal"]["probability"].get("calibrated") is not None
    ]
    roi = profit / stake if stake else None
    ui.section("Podsumowanie", "kupony kreatora (wirtualne)")
    ui.metric_cards(
        [
            ("Zapisane kupony", str(len(coupons)), f"nierozliczone: {len(coupons) - len(settled)}"),
            ("Rozliczone", str(len(settled)), f"wygrane {wins} · przegrane {len(decided) - wins}"),
            (
                "Trafność",
                ins.pct(wins / len(decided)) if decided else ins.NO_DATA,
                f"prognozowana średnio {ins.pct(sum(predicted) / len(predicted))}"
                if predicted
                else "brak rozliczonych kuponów",
            ),
            (
                "Zysk / strata netto",
                ins.money(profit) if settled else ins.NO_DATA,
                "po podatku STS",
            ),
            (
                "ROI",
                ins.pct(float(roi)) if roi is not None else ins.NO_DATA,
                f"za mało rozliczeń (n={len(settled)} < {MIN_FOR_ROI}) — wynik przypadkowy"
                if settled and len(settled) < MIN_FOR_ROI
                else "zwrot z sumy stawek",
            ),
            (
                "Średni kurs",
                ins.num(sum(float(x["total_odds"]) for x in coupons) / len(coupons))
                if coupons
                else ins.NO_DATA,
                "wszystkich zapisanych kuponów",
            ),
        ]
    )
    if not coupons:
        ui.empty_state(
            "Brak kuponów w historii",
            "Kupony kreatora zapisują się automatycznie po analizie w zakładce Kupon dnia i "
            "rozliczają się po pobraniu wyników.",
        )
        return
    if settled:
        series, running = [], Decimal(0)
        for x in sorted(settled, key=lambda x: x["first_start_at"]):
            running += x["net_profit"]
            series.append(
                {"Data": x["first_start_at"][:10], "Wynik narastająco (zł)": float(running)}
            )
        ui.section("Skuteczność w czasie")
        st.line_chart(pd.DataFrame(series).set_index("Data"), height=220)
    _coupon_list(db, coupons)


LEGACY_PROFILES = {"safe", "balanced", "aggressive"}  # profiles before TASK 007
STATUS = {
    "wszystkie": None,
    "nierozliczone": lambda x: not x["outcome"],
    "trafione": lambda x: x["outcome"] == "win",
    "nietrafione": lambda x: x["outcome"] == "loss",
    "inne rozliczenia": lambda x: x["outcome"] not in (None, "win", "loss"),
}


def newest_variants(db, coupons: list[dict]) -> set[str]:
    """The newest saved proposal of every profile and analysis day; older ones are
    historical variants (kept in the database, shown as such)."""
    days = {r["id"]: r["analysis_date"] for r in db.rows("SELECT id, analysis_date FROM analyses")}
    newest: dict[tuple, dict] = {}
    for x in coupons:
        key = (x["profile"], days.get(x.get("analysis_id")) or x["first_start_at"][:10])
        if key not in newest or x["created_at"] > newest[key]["created_at"]:
            newest[key] = x
    return {x["id"] for x in newest.values()}


VERSIONED = ("revision", "price_update", "duplicate")


def _grouped(coupons: list[dict]) -> list[dict]:
    """Every coupon in start order, each version right after its official coupon."""
    by_official: dict[str, list[dict]] = {}
    heads = []
    for x in coupons:
        if x.get("relation") in VERSIONED:
            by_official.setdefault(x["official_coupon_id"], []).append(x)
        else:
            heads.append(x)
    out = []
    for head in heads:
        out.append(head)
        out.extend(sorted(by_official.pop(head["id"], []), key=lambda v: v["created_at"]))
    for orphans in by_official.values():
        out.extend(orphans)
    return out


def kind_label(coupon: dict, now, newest: set[str] | None = None) -> tuple[str, str]:
    """Which kind of saved coupon this is: settled, real, current candidate, saved virtual
    or a historical variant of a proposal. TASK 010: the relation of ``coupon_lineage``
    (official coupon of the day, revision, price update, duplicate) comes first."""
    relation = coupon.get("relation")
    if relation in ("revision", "price_update", "duplicate"):
        return RELATION_LABELS[relation] + " (poza statystykami)", "neutral"
    if coupon["outcome"]:
        return "Kupon rozliczony", "neutral"
    if relation == "official" and datetime.fromisoformat(coupon["first_start_at"]) > now:
        return "Oficjalny kupon dnia", "info"
    if coupon["mode"] == "real_manual":
        if (coupon.get("proposal") or {}).get("entered_after_start"):
            return "Rzeczywisty — wpis po fakcie", "info"
        return "Rzeczywisty (postawiony w STS)", "info"
    builder = coupon["mode"] != "paper_manual"
    if builder and coupon.get("profile") in LEGACY_PROFILES:
        return "Historyczny wariant propozycji", "neutral"  # a profile the builder no longer uses
    if builder and relation is None and newest is not None and coupon["id"] not in newest:
        return "Historyczny wariant propozycji", "neutral"
    if datetime.fromisoformat(coupon["first_start_at"]) > now:
        return ("Bieżący kandydat", "info") if builder else ("Zapisany wirtualny kupon", "info")
    return "Zapisany wirtualny — czeka na wyniki", "warn"


def _coupon_list(db, coupons: list[dict]) -> None:
    ui.section("Kupony", "filtry · zaznacz wiersz, aby zobaczyć szczegóły")
    now = c.now()
    versions = st.toggle(
        "Wersje",
        key="hist_versions",
        help="Pokaż też rewizje, aktualizacje kursów i duplikaty — pod kuponem oficjalnym. "
        "Domyślnie widać kupony oficjalne i kupony użytkownika (te liczą się w statystykach).",
    )
    if versions:
        coupons = _grouped(builder_coupons(db, everything=True))
    newest = newest_variants(db, coupons)
    rows = [{**x, "kind": kind_label(x, now, newest)} for x in reversed(coupons)]
    if versions:
        rows.reverse()  # official first, its versions right below
    dates = [datetime.fromisoformat(x["first_start_at"]).date() for x in rows]
    with st.container(key="row-history"):
        cols = st.columns([1.5, 1, 1, 1])
        span = cols[0].date_input(
            "Data", value=(min(dates), max(dates)), format="DD.MM.YYYY", key="hist_dates"
        )
        profiles = sorted({x["proposal"].get("label") or x["profile"] for x in rows})
        chosen = cols[1].multiselect(
            "Profil", profiles, key="hist_profiles", placeholder="wszystkie"
        )
        status = cols[2].selectbox("Status", list(STATUS), key="hist_status")
        kinds = sorted({x["kind"][0] for x in rows})
        types = cols[3].multiselect("Typ kuponu", kinds, key="hist_types", placeholder="wszystkie")
    low, high = (
        (span[0], span[-1]) if isinstance(span, tuple) and span else (min(dates), max(dates))
    )
    shown = [
        x
        for x, day in zip(rows, dates, strict=True)
        if low <= day <= high
        and (not chosen or (x["proposal"].get("label") or x["profile"]) in chosen)
        and (STATUS[status] is None or STATUS[status](x))
        and (not types or x["kind"][0] in types)
    ]
    if not shown:
        ui.empty_state("Brak kuponów dla filtrów", "Zmień zakres dat, profil, status lub typ.")
        return
    frame = pd.DataFrame(
        [
            {
                "Data": c.local_time(x["first_start_at"], "%d.%m.%Y %H:%M"),
                "Profil": ("↳ " if x.get("relation") in VERSIONED else "")
                + (x["proposal"].get("label") or x["profile"]),
                "Typ": x["kind"][0],
                "Mecze": ", ".join(leg["event"] for leg in x["proposal"]["legs"][:2])
                + (
                    f" + {len(x['proposal']['legs']) - 2}" if len(x["proposal"]["legs"]) > 2 else ""
                ),
                "Kurs": float(x["total_odds"]),
                "Szansa": round(x["proposal"]["probability"]["calibrated"] * 100, 1)
                if x["proposal"]["probability"].get("calibrated") is not None
                else None,
                "Stawka": float(x["stake"]),
                "Wynik": OUTCOME.get(x["outcome"], (x["outcome"] or "oczekuje",))[0],
                "Zysk netto": float(x["net_profit"]) if x["net_profit"] is not None else None,
            }
            for x in shown
        ]
    )
    picked = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=min(38 * (len(frame) + 1) + 4, 460),
        on_select="rerun",
        selection_mode="single-row",
        key="hist_table",
        column_config={
            "Kurs": st.column_config.NumberColumn(format="%.2f"),
            "Szansa": st.column_config.NumberColumn(format="%.1f%%"),
            "Stawka": st.column_config.NumberColumn(format="%.2f zł"),
            "Zysk netto": st.column_config.NumberColumn(format="%.2f zł"),
        },
    )
    st.caption(
        f"Pokazano {len(shown)} z {len(rows)} kuponów. Historyczne warianty propozycji zostają "
        "w bazie — nic nie jest usuwane."
    )
    if picked.selection.rows:
        _coupon_details(db, shown[picked.selection.rows[0]])


def _coupon_details(db, coupon: dict) -> None:
    payload = coupon["proposal"]
    outcomes = leg_outcomes(db, coupon["id"])  # this coupon's legs, also for a version
    legs = payload["legs"]
    with st.container(border=True):
        st.markdown(
            ui.profile_badge(coupon["profile"], payload.get("label"))
            + " "
            + ui.pill(*coupon["kind"])
            + ui.stat_strip(
                [
                    ("Kurs łączny", ins.num(coupon["total_odds"])),
                    ("Szansa", ins.pct(payload["probability"]["calibrated"])),
                    ("Ostrożnie", ins.pct(payload["probability"]["conservative"])),
                    ("Stawka", ins.money(coupon["stake"])),
                    ("Wypłata", ins.money(payload.get("payout"))),
                    (
                        "Zysk netto",
                        ins.money(coupon["net_profit"])
                        if coupon["net_profit"] is not None
                        else "czeka",
                    ),
                ]
            )
            + ui.legs_html(
                [
                    {
                        "event": leg["event"],
                        "when": c.local_time(leg.get("start_at"), "%d.%m %H:%M"),
                        "pick": ins.pick_label(
                            leg.get("market_key", ""),
                            leg.get("line"),
                            leg["selection"],
                            leg["event"],
                            leg["market"],
                        ),
                        "odds": ins.num(leg["odds"]),
                        "chance": f"szansa {ins.pct(leg.get('p_model'))}",
                        "detail": {"win": "trafiony", "loss": "nietrafiony"}.get(
                            outcomes.get(leg.get("selection_id") or leg["odds_id"]) or "",
                            "czeka na wynik",
                        ),
                    }
                    for leg in legs
                ]
            ),
            unsafe_allow_html=True,
        )
        if coupon["outcome"] in {"win", "loss"}:  # ANALIZA WYNIKU KUPONU (TASK 007)
            judged = {
                **coupon,
                "proposal": {
                    **payload,
                    "legs": [
                        {**leg, "outcome": outcomes.get(leg.get("selection_id") or leg["odds_id"])}
                        for leg in legs
                    ],
                },
            }
            ui.explanation("Analiza wyniku", coupon_review(judged)[:6])


def _pct(value, digits: int = 0) -> str:
    return f"{value:.{digits}%}" if value is not None else "—"


GROUPS = {
    "range": "Przedział prawdopodobieństwa",
    "market_family": "Rynek",
    "league": "Liga",
    "sport": "Dyscyplina",
}


def _builder_performance(db) -> None:
    stats = coupon_performance(db)
    if not stats:
        st.info("Brak zapisanych kuponów kreatora — wygeneruj je w zakładce Kupon dnia.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Profil": s["label"],
                    "Kupony": s["coupons"],
                    "Oczekujące": s["pending"],
                    "Rozliczone": s["settled"],
                    "Wygrane": s["wins"],
                    "Trafność": _pct(s["hit_rate"]),
                    "Trafność (95%)": "—"
                    if s["hit_rate_ci95"] is None
                    else f"{s['hit_rate_ci95'][0]:.0%}–{s['hit_rate_ci95'][1]:.0%}",
                    "Śr. przewidywana szansa": _pct(s["avg_predicted"]),
                    "Śr. szansa ostrożnie": _pct(s["avg_predicted_conservative"]),
                    "Śr. liczba selekcji": round(s["avg_legs"], 2),
                    "Śr. kurs": round(s["avg_odds"], 2),
                    "Stawki": str(s["stake"]),
                    "Zysk netto": str(s["profit"]),
                    "ROI": _pct(s["roi"], 1),
                    "Maks. obsunięcie": str(s["max_drawdown"]),
                    "Najdłuższa seria wygranych": s["longest_win_streak"],
                    "Najdłuższa seria przegranych": s["longest_loss_streak"],
                    "Bieżąca seria": s["current_streak"],
                }
                for s in stats.values()
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Trafność porównujemy ze średnią przewidywaną szansą rozliczonych kuponów; przedział 95% "
        "(Wilson) pokazuje, jak mało mówi kilka rozliczeń. ROI po współczynniku 0,88 i podatku."
    )
    versions = coupon_performance(db, versions=True)
    if versions:
        with st.expander("Rewizje i aktualizacje kursów — tylko diagnostyka"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Profil": s["label"],
                            "Wersje": s["coupons"],
                            "Rozliczone": s["settled"],
                            "Wygrane": s["wins"],
                            "Trafność": _pct(s["hit_rate"]),
                            "ROI": _pct(s["roi"], 1),
                        }
                        for s in versions.values()
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "Późniejsze wersje kuponu dnia (inne nogi albo nowe kursy z nowszego PDF). "
                "Nie wchodzą do statystyk powyżej — liczy się kupon oficjalny."
            )
    rows = selection_outcomes(db)
    decided = [r for r in rows if r["outcome"] in {"win", "loss"}]
    st.subheader("Selekcje kreatora")
    st.caption(
        f"Selekcje w zapisanych kuponach: {len(rows)} (każda liczona raz), rozstrzygnięte: "
        f"{len(decided)}. Kalibracja: trafność selekcji w grupie wobec średniej prognozy."
    )
    by = st.radio("Grupuj według", list(GROUPS), format_func=GROUPS.get, horizontal=True)
    summary = selection_summary(rows, by)
    if not summary:
        st.info("Brak rozstrzygniętych selekcji.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    GROUPS[by]: s["group"],
                    "Selekcje": s["n"],
                    "Trafione": s["hits"],
                    "Trafność": _pct(s["hit_rate"]),
                    "Trafność (95%)": f"{s['ci95'][0]:.0%}–{s['ci95'][1]:.0%}",
                    "Śr. prognoza": _pct(s["mean_p_model"]),
                    "Śr. prognoza ostrożna": _pct(s["mean_p_conservative"]),
                    "Ocena kalibracji": s["flag"],
                }
                for s in summary
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _performance(db) -> None:
    stats = performance(db)
    if not stats:
        st.info("Brak rozliczeń.")
    else:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Tryb": MODES[mode],
                        "Kupony": s["coupons"],
                        "Rozliczone": s["settled"],
                        "Stawki": str(s["stake"]),
                        "Zysk netto": str(s["profit"]),
                        "ROI": f"{s['roi']:.2%}" if s["roi"] is not None else "—",
                        "Maks. obsunięcie": str(s["max_drawdown"]),
                        "Trafność": f"{s['hit_rate']:.1%}" if s["hit_rate"] is not None else "—",
                    }
                    for mode, s in stats.items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption("Tryby liczone osobno. Mała liczba rozliczeń nie jest dowodem skuteczności.")
    st.subheader("CLV")
    st.caption(
        "Cena zamknięcia: ostatnie notowanie STS w 30 min przed startem; bez niego CLV niedostępne."
    )
    for coupon in db.rows("SELECT id FROM coupons ORDER BY created_at DESC LIMIT 50"):
        for leg in closing_line_value(db, coupon["id"]):
            value = f"{leg['clv']:+.2%}" if leg["clv"] is not None else leg["status"]
            st.write(f"`{coupon['id'][:10]}…` kurs wejścia {leg['entry']}: {value}")
