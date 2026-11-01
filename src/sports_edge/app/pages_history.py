"""Historia i wyniki: analyses, virtual coupons, results, settlement, ROI, drawdown, CLV."""

import json
from decimal import Decimal

import pandas as pd
import streamlit as st

from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.paper import (
    MODES,
    closing_line_value,
    coupons_table,
    create_coupon,
    import_source_results,
    latest_result,
    performance,
    record_result,
    settle_coupon,
)
from sports_edge.repository import offer_days, offer_table
from sports_edge.services import normalizer, team_aliases


def history_page() -> None:
    c.setup_page()
    st.title("Historia i wyniki")
    db = c.store()
    tabs = st.tabs(["Kupony wirtualne", "Wyniki i rozliczenia", "Skuteczność i CLV", "Analizy"])
    with tabs[0]:
        _coupons(db)
    with tabs[1]:
        _results(db)
    with tabs[2]:
        _performance(db)
    with tabs[3]:
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
                    "EV netto > 0": counts.get("ev_positive", "—"),
                    "Eksperymentalne": counts.get("experimental", "—"),
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
    if rows:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Utworzono": c.local_time(r["created_at"]),
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
            "Selekcje (AKO: różne wydarzenia)", list(options), format_func=options.get
        )
        stake = st.number_input("Stawka (PLN)", min_value=1.0, value=10.0, step=1.0)
        mode = st.selectbox("Tryb", ["paper_manual", "simulation"], format_func=MODES.get)
        if st.button("Zapisz kupon", disabled=not chosen):
            try:
                create_coupon(
                    db,
                    odds_ids=chosen,
                    stake=Decimal(f"{stake:.2f}"),
                    mode=mode,
                    profile=load_profiles(c.paths().config)["sts"],
                    now=c.now(),
                    note="wybór użytkownika",
                )
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
        "Wyniki: automatycznie z niezależnego źródła tylko przy pewnym dopasowaniu (ta sama liga, "
        "data i obie drużyny), w przeciwnym razie ręcznie. Korekta tworzy nowy rekord."
    )
    if st.button("Pobierz wyniki z danych niezależnych (lokalnie)"):
        out = import_source_results(
            db, now=c.now(), normalizer=normalizer(c.paths()), team_aliases=team_aliases(c.paths())
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
            st.write(
                f"`{coupon['id'][:10]}…` → {outcome['status']}",
                outcome.get("net_profit", ""),
                outcome.get("reason", ""),
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
