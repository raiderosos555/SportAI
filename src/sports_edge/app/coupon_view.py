"""One coupon card for Kupon dnia, Dashboard and Mój kupon (TASK 009).

Level 1 (always visible): profile, date, number of selections, status, the key numbers in
tiles (total odds, chance, conservative chance, stake, payout, net profit), the ordered
list of selections and data warnings. Level 2/3 (closed panel with tabs): why this coupon,
AI evaluation (on request), risk, lineup check, change history, budget simulation.

Nothing here changes a stored coupon or forecast.
"""

import json
import math
from decimal import Decimal

import streamlit as st

from sports_edge import advisor, automation
from sports_edge import insights as ins
from sports_edge import my_coupon as mine
from sports_edge.app import cart, prematch_ui, ui
from sports_edge.app import common as c
from sports_edge.bookmakers import load_profiles

PROFILE_TEXT = {
    "high_hit": "Kurs 1,25–1,60: najwyższa szansa trafienia, preferowany SOLO.",
    "standard": "Kurs 1,80–2,20: główny profil — szansa i wypłata w równowadze.",
    "higher_payout": "Kurs 2,50–4,00: wyższa wypłata za wyraźnie niższą szansę.",
    "tasiemka": "Kurs 8–12: długi kupon z najmniejszą uzasadnioną liczbą zdarzeń — wysoka "
    "wypłata przy niskiej szansie.",
    "safe": "Profil sprzed TASK 007 (Bezpieczny).",
    "balanced": "Profil sprzed TASK 007 (Zrównoważony).",
    "aggressive": "Profil sprzed TASK 007 (Agresywny).",
}
OUTCOME = {"win": ("Trafiony", "ok"), "loss": ("Nietrafiony", "bad")}
SIMULATED = 20  # coupons in the budget simulation


def status_badges(payload: dict) -> list[str]:
    validated = payload.get("level") == "validated_predictive"
    out = [
        ui.pill("Zwalidowany predykcyjnie", "ok")
        if validated
        else ui.pill("SYMULACJA — model eksperymentalny", "warn")
    ]
    out.append(ui.pill(*OUTCOME.get(payload.get("outcome") or "", ("Nierozliczony", "neutral"))))
    return out


def stats(payload: dict) -> str:
    p = payload["probability"]
    return ui.stat_strip(
        [
            ("Kurs łączny", ins.num(payload["total_odds"])),
            ("Szansa", ins.pct(p["calibrated"])),
            ("Ostrożnie", ins.pct(p["conservative"])),
            ("Stawka", ins.money(payload.get("stake", "10.00"))),
            ("Wypłata", ins.money(payload["payout"])),
            ("Zysk netto", ins.money(payload["net_profit"])),
        ],
        emphasis=(0, 1),
    )


def legs(payload: dict, db) -> list[dict]:
    out = []
    for leg in payload["legs"]:
        out.append(
            {
                "event": leg["event"].replace(" - ", " – "),
                "when": f"{c.local_time(leg['start_at'], '%d.%m %H:%M')} · {leg.get('league', '')}"
                + (f" · nr STS {leg['offer_code']}" if leg.get("offer_code") else ""),
                "pick": ins.pick_label(
                    leg.get("market_key", ""),
                    leg.get("line"),
                    leg["selection"],
                    leg["event"],
                    leg["market"],
                ),
                "odds": ins.num(leg["odds"]),
                "chance": f"szansa {ins.pct(leg['p_model'])}",
                "detail": f"ostrożnie {ins.pct(leg['p_conservative'])} · "
                + (
                    "zwalidowany"
                    if leg["predictive_status"] == "validated_predictive"
                    else "eksperymentalny"
                ),
                "note": prematch_ui.leg_status(
                    db, leg.get("event_id") or prematch_ui.event_of_odds(db, leg["odds_id"])
                ),
            }
        )
    return out


def header_html(name: str, payload: dict, *, date_label: str, label: str | None = None) -> str:
    n = len(payload["legs"])
    return (
        '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:.45rem .6rem">'
        + ui.profile_badge(name, label)
        + f'<span class="se-muted se-num">{ui.esc(date_label)}</span>'
        + f'<span class="se-muted se-num">· {ui.esc(ins.plural(n, "selekcja", "selekcje", "selekcji"))}</span>'
        + "</div>"
        + f'<p class="se-desc" style="margin:.35rem 0 .2rem">{ui.esc(PROFILE_TEXT.get(name, ""))}</p>'
        + "".join(status_badges(payload))
    )


def proposal_history(db, day: str, profile: str) -> list[dict]:
    """Every saved variant of a profile for an analysis day, newest first (never deleted)."""
    out = []
    for row in db.rows(
        """SELECT c.* FROM coupons c JOIN analyses a ON a.id=c.analysis_id
        WHERE a.analysis_date=? AND c.profile=? AND c.proposal_json IS NOT NULL
        ORDER BY c.created_at DESC""",
        (day, profile),
    ):
        payload = json.loads(row["proposal_json"])
        out.append(
            {
                "Zapisano": c.local_time(row["created_at"], "%d.%m %H:%M"),
                "Selekcje": len(payload["legs"]),
                "Kurs": ins.num(payload["total_odds"]),
                "Szansa": ins.pct(payload["probability"]["calibrated"]),
                "Mecze": ", ".join(leg["event"] for leg in payload["legs"][:3]),
            }
        )
    return out


def _tail(n: int, k: int, p: float) -> float:
    """P(at least k successes in n Bernoulli(p) trials)."""
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(max(0, k), n + 1))


def budget(payload: dict, n: int = SIMULATED) -> dict:
    """What a series of n identical coupons can do: hits needed to be in profit and the
    probability of it, with the model and the conservative chance (exact binomial)."""
    stake = Decimal(str(payload.get("stake", "10.00")))
    win = Decimal(str(payload["net_profit"])) + stake  # returned on a hit
    need = math.ceil(float(n * stake / win)) if win > 0 else n + 1
    p = payload["probability"]
    return {
        "n": n,
        "stake_total": n * stake,
        "need": need,
        "profit": {k: _tail(n, need, float(p[k])) for k in ("calibrated", "conservative")},
        "hits": {k: n * float(p[k]) for k in ("calibrated", "conservative")},
    }


def _ai(db, payload: dict, by_odds: dict, day: str, key: str) -> None:
    rows = [by_odds.get(leg["odds_id"]) for leg in payload["legs"]]
    if not all(rows):
        st.caption("Ocena AI wymaga aktualnych kursów wszystkich selekcji w ofercie.")
        return
    if not st.session_state.get(f"ai_{key}"):
        if st.button(
            "Pokaż ocenę AI tego kuponu", key=f"ai_btn_{key}", icon=":material/psychology:"
        ):
            st.session_state[f"ai_{key}"] = True
            st.rerun()
        st.caption("Ocena powstaje z danych aplikacji (modele, kursy, składy) — bez zgadywania.")
        return
    now = c.now()
    legs_ = [mine.leg_from(x, day=day, now=now) for x in rows]
    sts = load_profiles(c.paths().config)["sts"]
    report = advisor.evaluate(
        legs_,
        rows,
        stake=Decimal(str(payload.get("stake", "10.00"))),
        cost=sts.cost,
        on_date=c.today_local(),
        now=now,
        store=db,
    )
    ui.explanation("Ocena", [report["verdict"]], tone="accent")
    if report["weakest"] is not None:
        weakest = report["legs"][report["weakest"]]
        st.markdown(
            f"**Najsłabsze ogniwo:** {weakest['event']} — {weakest['pick']} "
            f"({ins.pct(weakest.get('p_model'))})"
        )
    if report["uncertainties"]:
        ui.explanation("Największe niepewności", report["uncertainties"], tone="warn")


def _risk(payload: dict) -> None:
    p = payload["probability"]
    points = [
        f"Szansa całego kuponu od {ins.pct(p['conservative'])} (ostrożnie) do "
        f"{ins.pct(p['calibrated'])} (model); {p['basis']}.",
        "Szansa AKO zakłada niezależność meczów — to przybliżenie.",
    ]
    weak = [
        leg for leg in payload["legs"] if leg.get("predictive_status") != "validated_predictive"
    ]
    if weak:
        points.append(f"Selekcje z modelem eksperymentalnym: {len(weak)} z {len(payload['legs'])}.")
    stale = [leg for leg in payload["legs"] if leg.get("freshness") != "fresh"]
    if stale:
        points.append(f"Kursy niepotwierdzone w aktualnej ofercie: {len(stale)}.")
    points.append(
        f"Opłacalność (diagnostyka): EV netto {ins.num(payload['ev_net'], 3)} zł na 1 zł — "
        "wysoka szansa trafienia nie oznacza zysku."
    )
    ui.explanation("Analiza ryzyka", points, tone="warn" if weak or stale else "")
    for warning in payload.get("warnings") or []:
        st.warning(warning)


def _lineups(db, payload: dict, key: str) -> None:
    for leg in legs(payload, db):
        st.markdown(f"**{leg['event']}** — {leg['note']}")
    ids = sorted(
        {
            leg.get("event_id") or prematch_ui.event_of_odds(db, leg["odds_id"])
            for leg in payload["legs"]
        }
        - {None}
    )
    if st.button(
        "Sprawdź składy tych meczów teraz",
        key=f"lineup_{key}",
        icon=":material/groups:",
        disabled=c.demo_mode() or not ids,
    ):
        st.success(
            automation.request(
                c.paths(), db, "check_events", now=c.now(), payload={"event_ids": ids}
            )
        )
    st.caption("Składy i absencje są ostrzeżeniem — prognozy ich nie uwzględniają.")


def simulation(payload: dict) -> None:
    found = budget(payload)
    st.markdown(
        ui.stat_strip(
            [
                ("Kuponów w serii", str(found["n"])),
                ("Łączna stawka", ins.money(found["stake_total"])),
                ("Trafień do zysku", f"≥ {found['need']}"),
                ("Szansa zysku", ins.pct(found["profit"]["calibrated"])),
                ("Ostrożnie", ins.pct(found["profit"]["conservative"])),
            ]
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        f"Oczekiwana liczba trafień: {ins.num(found['hits']['calibrated'], 1)} (ostrożnie "
        f"{ins.num(found['hits']['conservative'], 1)}) z {found['n']}. Dokładny rozkład "
        "dwumianowy; pełny symulator Monte Carlo w Symulatorze budżetu."
    )
    c.page_link("budzet", "Otwórz Symulator budżetu")


def _exposed(rows: list[dict], payload: dict, name: str, exposure: dict | None) -> list:
    """Each leg says which other coupons of the day share its match (correlated risk)."""
    if not exposure:
        return rows
    for row, leg in zip(rows, payload["legs"], strict=True):
        others = [
            label for profile, label, _pick in exposure.get(leg["event_id"], []) if profile != name
        ]
        if others:
            note = f"ten mecz jest też w: {', '.join(others)}"
            row["note"] = f"{row['note']} · {note}" if row.get("note") else note
    return rows


def status_html(status: dict | None, payload: dict) -> str:
    """Official coupon of the day, a revision, a price update or "no change" (TASK 010)."""
    if not status:
        return ""
    relation = status.get("relation")
    unchanged = relation == "duplicate"
    created = status.get("created_at")
    if unchanged and status.get("version_relation") in ("price_update", "revision"):
        # the analysis repeated a later version: show that version, not the official coupon
        relation = status["version_relation"]
        created = status.get("version_created_at") or created
    official = status.get("official_created_at")
    since = c.local_time(official, "%H:%M") if official else "—"
    at = c.local_time(created, "%H:%M") if created else "—"
    lines: list[str] = []
    if relation == "revision":
        badge = ui.pill(f"Rewizja z {at}", "warn")
        if unchanged:
            lines.append(f"Bez zmian — rewizja z {at} jest aktualna.")
        lines.append(f"Oficjalny kupon dnia z {since} pozostaje w statystykach.")
    elif relation == "price_update":
        documents = [leg.get("document_at") for leg in payload["legs"] if leg.get("document_at")]
        pdf = c.local_time(max(documents), "%H:%M") if documents else "—"
        badge = ui.pill(f"Kursy zaktualizowane (PDF z {pdf})", "info")
        if unchanged:
            lines.append(f"Bez zmian — aktualizacja kursów z {at} jest aktualna.")
        lines.append(f"Oficjalny kupon dnia z {since} pozostaje w statystykach.")
    else:
        badge = ui.pill("Oficjalny kupon dnia", "ok")
        if unchanged:
            lines.append(f"Bez zmian — kupon dnia z {since} jest aktualny.")
    changes = status.get("changes") or {}
    if changes.get("added"):
        lines.append("Dodane: " + "; ".join(changes["added"]))
    if changes.get("removed"):
        lines.append("Usunięte: " + "; ".join(changes["removed"]))
    for move in changes.get("prices") or []:
        lines.append(
            f"Kurs {move['selection']}: {ins.num(move['before'])} → {ins.num(move['after'])}"
        )
    if relation in ("revision", "price_update") and changes.get("total_odds"):
        odds, payout = changes["total_odds"], changes.get("payout") or ["—", "—"]
        lines.append(
            f"Kurs łączny {ins.num(odds[0])} → {ins.num(odds[1])} · wypłata "
            f"{ins.money(payout[0])} → {ins.money(payout[1])}"
        )
    newest = status.get("newest_version")
    if newest:
        label = "Rewizja" if newest["relation"] == "revision" else "Aktualizacja kursów"
        lines.append(
            f"Nowsza wersja: {label} z {c.local_time(newest['created_at'], '%H:%M')} — "
            "kupon oficjalny się nie zmienia."
        )
    return (
        '<div class="se-coupon-status">'
        + badge
        + "".join(f'<div class="sub">{ui.esc(line)}</div>' for line in lines)
        + "</div>"
    )


def card(
    name: str,
    payload: dict | None,
    *,
    db,
    day: str,
    by_odds: dict,
    refusal: str | None = None,
    coupon_id: str | None = None,
    alternatives: list[dict] = (),
    status: dict | None = None,
    exposure: dict | None = None,
) -> None:
    """The coupon of one profile for one day (or why there is none). ``status`` (TASK 010)
    is what saving did: official coupon, no change, price update or revision; ``exposure``
    names the other coupons of the day with the same match."""
    if payload is None:
        st.markdown(
            ui.list_row(
                title=PROFILE_TEXT.get(name, name),
                meta=refusal or "Profil nie znalazł kombinacji spełniającej warunki.",
                badges=[ui.profile_badge(name), ui.pill("Brak propozycji", "neutral")],
            ),
            unsafe_allow_html=True,
        )
        return
    tone = "violet" if name == "tasiemka" else ""
    with st.container(border=True, key=f"coupon-{day}-{name}{'-' + tone if tone else ''}"):
        st.markdown(
            header_html(name, payload, date_label=c.day_label(day))
            + status_html(status, payload)
            + stats(payload)
            + ui.legs_html(_exposed(legs(payload, db), payload, name, exposure)),
            unsafe_allow_html=True,
        )
        rows = [by_odds.get(leg["odds_id"]) for leg in payload["legs"]]
        with st.container(key=f"row-actions-{day}-{name}"):
            left, right = st.columns([1, 2], vertical_alignment="center")
            if all(rows):
                left.button(
                    cart.ADD_LABEL,
                    key=f"to_cart_{day}_{name}",
                    icon=":material/add_shopping_cart:",
                    on_click=cart.add_many,
                    args=([(x, day) for x in rows],),
                    help="Przenosi typy tej propozycji do Mojego kuponu (do oceny lub zmian)",
                    width="stretch",
                )
            if coupon_id and not str(coupon_id).startswith("nie zapisano"):
                right.caption(
                    f"Zapisany kupon wirtualny {str(coupon_id)[:10]}… — rozliczy się sam."
                )
        with ui.details("Szczegóły, ocena AI, ryzyko, składy i historia", icon=":material/tune:"):
            tabs = st.tabs(
                ["Dlaczego", "Ocena AI", "Ryzyko", "Składy", "Historia zmian", "Symulacja budżetu"]
            )
            with tabs[0]:
                for line in payload["reasons"]:
                    st.write("• " + line)
                if alternatives:
                    st.markdown("**Alternatywne propozycje**")
                    for alt in alternatives:
                        q = alt["probability"]
                        st.write(
                            "• "
                            + ", ".join(leg["event"] for leg in alt["legs"])
                            + f" — kurs {ins.num(alt['total_odds'])}, szansa "
                            f"{ins.pct(q['calibrated'])} (ostrożnie {ins.pct(q['conservative'])})"
                        )
            with tabs[1]:
                _ai(db, payload, by_odds, day, f"{day}_{name}")
            with tabs[2]:
                _risk(payload)
            with tabs[3]:
                _lineups(db, payload, f"{day}_{name}")
            with tabs[4]:
                history = proposal_history(db, day, name)
                if history:
                    st.dataframe(history, hide_index=True, width="stretch")
                    st.caption("Każdy zapisany wariant zostaje w bazie — nic nie jest usuwane.")
                else:
                    st.caption("Ten wariant nie był jeszcze zapisany.")
            with tabs[5]:
                simulation(payload)
