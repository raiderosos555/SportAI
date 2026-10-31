"""History: analyses, virtual coupons, results, settlement, ROI, CLV, models, exports."""

import json
from decimal import Decimal

import pandas as pd
import streamlit as st

from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles
from sports_edge.export import export_csv, export_json
from sports_edge.models.registry import model_overview
from sports_edge.paper import (
    MODES,
    closing_line_value,
    coupons_table,
    create_coupon,
    latest_result,
    performance,
    record_result,
    settle_coupon,
)
from sports_edge.repository import offer_days, offer_table


def history_page() -> None:
    c.setup_page()
    st.title("Historia")
    db = c.store()
    tabs = st.tabs(
        ["Kupony wirtualne", "Wyniki i rozliczenia", "Skuteczność", "Analizy", "Modele", "Eksport"]
    )
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
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Utworzono": c.local_time(r["created_at"]),
                            "Dzień": r["analysis_date"],
                            "Wynik": json.loads(r["summary_json"])["headline"],
                            "Selekcje": json.loads(r["summary_json"])["analysed"],
                            "Przyjęte": len(json.loads(r["summary_json"])["accepted"]),
                            "Polityka": json.loads(r["policy_json"])["version"],
                        }
                        for r in rows
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "Analizy są niezmienne: nowe kursy tworzą nową analizę, stare zostają w historii."
            )
    with tabs[4]:
        models = model_overview(db)
        if not models:
            st.info(
                "Brak modeli. Prognozy i walidacja pojawią się po imporcie danych i treningu baseline."
            )
        for model in models:
            validation = json.loads(model["validation_json"])
            st.write(
                f"**{model['family']} {model['version']}** — status `{model['status']}`, sport {model['sport']}"
            )
            st.json({k: v for k, v in validation.items() if k != "reliability"}, expanded=False)
    with tabs[5]:
        _exports(db)


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
                        "Bukmacher": c.BOOKMAKER_NAMES.get(r["bookmaker_id"]),
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
        rows = [
            r for r in offer_table(db, day=day, include_review=False, data_kinds=c.data_kinds())
        ]
        upcoming = [r for r in rows if r["start_at"] and r["start_at"] > c.now().isoformat()]
        if not upcoming:
            st.write("Brak nadchodzących wydarzeń z poprawnymi kursami.")
            return
        options = {}
        for r in upcoming:
            for key, quote in r["selections"].items():
                label = (
                    f"{c.local_time(r['start_at'], '%H:%M')} {c.BOOKMAKER_NAMES.get(r['bookmaker_id'])} · "
                    f"{' - '.join(r['participants'])} · {c.MARKETS.get(r['market_key'])} "
                    f"{r['line'] or ''} · {c.SELECTIONS.get(key, key)} @ {quote['price']}"
                )
                options[quote["odds_id"]] = (label, r["bookmaker_id"])
        chosen = st.multiselect(
            "Selekcje (AKO: różne wydarzenia, jeden bukmacher)",
            list(options),
            format_func=lambda k: options[k][0],
        )
        stake = st.number_input("Stawka (PLN)", min_value=1.0, value=10.0, step=1.0)
        mode = st.selectbox("Tryb", ["paper_manual", "simulation"], format_func=MODES.get)
        if st.button("Zapisz kupon", disabled=not chosen):
            bookmakers = {options[k][1] for k in chosen}
            if len(bookmakers) != 1:
                st.error("Kupon może zawierać selekcje tylko jednego bukmachera.")
                return
            profiles = load_profiles(c.paths().config)
            try:
                create_coupon(
                    db,
                    odds_ids=chosen,
                    stake=Decimal(f"{stake:.2f}"),
                    mode=mode,
                    profile=profiles[bookmakers.pop()],
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
        "Brak zweryfikowanego automatycznego źródła wyników: wyniki wprowadzasz ręcznie z oficjalnego "
        "źródła i oznaczasz jako zweryfikowane. Korekta tworzy nowy rekord (historia zostaje)."
    )
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
            details = {"winner": winner} if winner != "—" else {}
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
                    details=details,
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
        return
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
                    "Trafność": f"{s['hit_rate']:.1%}" if s["hit_rate"] is not None else "—",
                }
                for mode, s in stats.items()
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption("Tryby są liczone osobno. Mała liczba rozliczeń nie jest dowodem skuteczności.")
    st.subheader("CLV (kurs wejścia vs ostatni zaobserwowany kurs przed startem)")
    for coupon in db.rows("SELECT id, created_at FROM coupons ORDER BY created_at DESC LIMIT 50"):
        clv = closing_line_value(db, coupon["id"])
        values = [x["clv"] for x in clv if x["clv"] is not None]
        st.write(
            f"`{coupon['id'][:10]}…` — "
            + (f"CLV {sum(values) / len(values):+.2%}" if values else "brak późniejszego notowania")
        )


def _exports(db) -> None:
    paths = c.paths()
    col1, col2 = st.columns(2)
    if col1.button("Eksport JSON"):
        target = export_json(
            db, paths.exports / f"export_{c.now():%Y%m%d_%H%M%S}.json", now=c.now()
        )
        st.success(f"Zapisano {target}")
        st.download_button(
            "Pobierz JSON", target.read_bytes(), file_name=target.name, mime="application/json"
        )
    if col2.button("Eksport CSV"):
        files = export_csv(db, paths.exports / f"csv_{c.now():%Y%m%d_%H%M%S}")
        st.success(f"Zapisano {len(files)} plików w {files[0].parent}")
