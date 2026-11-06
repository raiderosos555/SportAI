"""Europejskie puchary i reprezentacje w interfejsie (TASK 009).

One category choice for the analysis, the events and the generator; one block for a cup or
national-team event; the UEFA coverage in Źródła and the separate statuses in Modele.
Nothing here invents a percentage: a market without a validated model shows the message
``international.GENERATOR_MESSAGE``.
"""

from datetime import datetime

import streamlit as st

from sports_edge import international
from sports_edge.app import common as c
from sports_edge.app import ui
from sports_edge.datasources.competitions import CATEGORIES, COMPETITIONS, for_sts_league

CHOICES = {"all": "Wszystkie", **CATEGORIES}
OTHER = (
    "KLUBOWE",
    "MŁODZIEŻOWE",
    "REPREZENTAC",
    "LIGA NARODÓW",
    "ELIMINACJE",
    "MISTRZOSTWA",
    "TOWARZYSKIE",
)
MARKET_LABELS = {
    "1x2": "1X2 (90 min)",
    "double_chance": "Podwójna szansa",
    "total_1.5": "Over/Under 1,5",
    "total_2.5": "Over/Under 2,5",
    "btts": "BTTS",
}


def category(league: str | None, sport: str | None) -> str:
    """Catalogue category of an STS header; an unmapped header with an international marker
    is "other" (shown only under "Wszystkie"), every other header a domestic league."""
    found = for_sts_league(league or "", sport)
    if found is not None:
        return found.category
    return "other" if any(m in (league or "").upper() for m in OTHER) else "domestic"


def category_filter(key: str) -> str:
    chosen = st.segmented_control(
        "Rozgrywki", list(CHOICES), default="all", format_func=CHOICES.get, key=key
    )
    return chosen or "all"


def matches(choice: str, league: str | None, sport: str | None) -> bool:
    return choice == "all" or category(league, sport) == choice


@st.cache_data(ttl=600, show_spinner=False, max_entries=32)
def _view(_db, version: tuple, event_id: str, minute: datetime) -> dict | None:
    event = _db.get("events", event_id)
    return international.event_view(_db, c.paths(), event, now=minute) if event else None


def event_block(db, event_id: str, now: datetime) -> None:
    """Cup / national-team context of one STS event (level 1 of the analysis)."""
    view = _view(db, c.data_version(db), event_id, now.replace(second=0, microsecond=0))
    if view is None:
        return
    ui.section(view["name"], f"{view['category_label']} · wyniki w bazie: {view['results']}")
    shadow = view.get("shadow")
    validated = [m for m, v in view["status"].items() if v["validated"]]
    if shadow:
        markets = shadow["markets"]
        points = []
        if "1x2" in markets:
            p = markets["1x2"]
            points.append(
                f"1X2 (90 min): 1 {p['home']:.0%} · X {p['draw']:.0%} · 2 {p['away']:.0%}"
            )
        for key in ("total_1.5", "total_2.5"):
            if key in markets:
                points.append(f"{MARKET_LABELS[key]}: powyżej {markets[key]['over']:.0%}")
        points.append(
            "Prognoza cieniowa zamrożona przed meczem — model zwalidowany wyłącznie wstecznie; "
            "poza kuponami do Twojej decyzji."
        )
        ui.explanation("Prognoza eksperymentalna (cień)", points, tone="info")
    else:
        reasons = [
            m for m in (view["generator"]["message"],) if m != international.GENERATOR_MESSAGE
        ]
        reasons.append(
            "Rynki zwalidowane wstecznie: "
            + (", ".join(MARKET_LABELS[m] for m in validated) if validated else "brak")
        )
        ui.explanation(international.GENERATOR_MESSAGE, reasons, tone="warn")
    if view.get("note"):
        ui.empty_state("Identyfikacja drużyn", view["note"], tone="warn")
        return
    tie = view.get("tie")
    if tie:
        first = tie["first_leg"]
        before = tie["aggregate_before"]
        ui.explanation(
            f"Rewanż — {tie['stage_label']}",
            [
                f"Pierwszy mecz {first['date']}: {first['home']} - {first['away']} "
                f"{first['score'][0]}:{first['score'][1]}",
                f"Dwumecz przed rewanżem (gospodarz : gość): {before[0]}:{before[1]}",
                "Reguła bramek na wyjeździe: "
                + ("obowiązuje (sezon do 2020/21)" if tie["away_goals_rule"] else "nie obowiązuje"),
                "1X2 i gole rozlicza wynik po 90 minutach; dogrywka i karne liczą się tylko "
                "dla awansu.",
            ],
        )
    elif view["category"] == "european":
        fmt = view.get("season_format") or {}
        ui.explanation(
            "Etap rozgrywek",
            [
                f"Format sezonu {fmt.get('season', '')}: {fmt.get('main_phase', 'brak danych')}",
                "Brak rozegranego pierwszego meczu tej pary w tej rundzie — to mecz fazy "
                "ligowej, pierwszy mecz dwumeczu albo finał (nie zgadujemy).",
            ],
        )
    cols = st.columns(2)
    for col, side in zip(cols, ("home", "away"), strict=True):
        team = view["teams"][side]["team"]
        plan = view["schedule"][side]
        with col:
            ui.metric_cards(
                [
                    (
                        "Dni odpoczynku",
                        str(plan["rest_days"]) if plan["rest_days"] is not None else "brak danych",
                        team,
                    ),
                    (
                        "Mecze w 14 dniach",
                        str(plan["matches_14"]),
                        f"w tym europejskie: {plan['european_14']}",
                    ),
                ]
            )
            ui.rows(
                [
                    ui.list_row(
                        title=f"{r['date']} · {r['opponent']} ({r['venue']})",
                        meta=r["competition"] + (" · dogrywka" if r["extra_time"] else ""),
                        value=r["score_90"],
                        value_hint="po 90 min",
                    )
                    for r in plan["recent"]
                ]
            )
    st.caption(
        "Odpoczynek i obciążenie liczone ze wszystkich wiarygodnie zidentyfikowanych meczów "
        "(liga, europejskie puchary); bez danych o minutach nie szacujemy zmęczenia zawodników."
    )


def coverage_block(db, now: datetime) -> None:
    """Źródła: separate UEFA and national-team coverage, P3 cups and unmapped headers."""
    from sports_edge import international_data as data

    rows = data.coverage(db, now=now)
    ui.section("Europejskie puchary i reprezentacje", "osobne pokrycie danych (TASK 009)")
    _table(
        ui.table(
            [
                "Rozgrywki",
                "Źródła",
                "Sezony",
                "Wyniki",
                "Bieżący sezon",
                "Niepewne 90'",
                "Konflikty",
                "Nagłówek STS",
                "Mecze STS",
            ],
            [
                [
                    r["name"],
                    ", ".join(r["sources"]),
                    f"{r['seasons'][0]}–{r['seasons'][-1]}" if r["seasons"] else "brak",
                    r["results"],
                    r["current_results"],
                    r["uncertain_90"],
                    r["conflicts"],
                    ", ".join(r["sts_headers"]) or "nie zaobserwowano",
                    r["sts_events"],
                ]
                for r in rows
            ],
            numeric=(3, 4, 5, 6, 8),
        )
    )
    st.caption(
        "„Niepewne 90'”: źródło podaje wynik, który może zawierać gole z dogrywki — nie trafia do "
        "modeli ani rozliczeń. Nagłówek STS dodajemy wyłącznie po zaobserwowaniu w ofercie."
    )
    with ui.details("Inne puchary (P3) i nierozpoznane nagłówki STS", icon=":material/info:"):
        _table(
            ui.table(
                ["Rozgrywki", "Stan"], [[r["competition"], r["status"]] for r in data.p3_status()]
            )
        )
        unmapped = data.unmapped_headers(db)
        if unmapped:
            _table(
                ui.table(
                    ["Nagłówek STS (bez mapowania)", "Wydarzenia"],
                    [[u["header"], u["events"]] for u in unmapped],
                    numeric=(1,),
                )
            )
    suggestions = data.sts_suggestions(db, c.paths(), now=now)
    if suggestions:
        ui.section(
            "Nazwy STS do potwierdzenia",
            "dowód: ten sam mecz w źródle — ten sam dzień, godzina i rozpoznany przeciwnik",
        )
        for number, item in enumerate(suggestions[:30]):
            cols = st.columns([5, 1])
            cols[0].markdown(
                ui.list_row(
                    title=f"{item['sts']} → {item['canonical']}",
                    meta=f"{COMPETITIONS[item['competition']].name} · {item['evidence']}",
                ),
                unsafe_allow_html=True,
            )
            if cols[1].button("Potwierdź", key=f"intl_alias_{number}"):
                from sports_edge.matching import confirm_alias
                from sports_edge.services import normalizer

                confirm_alias(
                    db,
                    sport="football",
                    alias=item["sts"],
                    canonical=item["canonical"],
                    normalizer=normalizer(c.paths()),
                    at=c.now(),
                )
                st.rerun()
    settings = c.settings()
    if st.button(
        "Pobierz / uzupełnij dane międzynarodowe",
        disabled=settings.offline_mode,
        key="intl_import",
        help="Historia pucharów UEFA i reprezentacji, potem bieżące sezony (limity źródeł "
        "zachowane).",
    ):
        from sports_edge.services import import_international

        with st.spinner("Pobieranie wyników międzynarodowych…"):
            report = import_international(db, c.paths(), settings, now=c.now())
        entries = report["cups"] + report["national"]
        st.success(
            f"Nowe wyniki: {sum(e.get('inserted', 0) for e in entries)}, aliasy z meczów: "
            f"{sum(len(e.get('learned_aliases', [])) for e in entries)}, nazwy do potwierdzenia: "
            f"{len({n for e in entries for n in e.get('unmatched', [])})}"
        )


def models_block(db) -> None:
    """Modele: domestic / European / national statuses kept apart."""
    report = international.latest(db)
    ui.section("Puchary europejskie i reprezentacje", "osobne pule ocen i osobna walidacja")
    if report is None:
        ui.empty_state(
            "Brak eksperymentu międzynarodowego",
            "Uruchom eksperymenty (po imporcie danych) — do tego czasu mecze pucharowe i "
            "reprezentacji nie mają prognoz: " + international.GENERATOR_MESSAGE + ".",
        )
    else:
        for scope in ("european", "national"):
            status = international.market_status(report, scope)
            block = report[scope]
            st.markdown(
                f"**{international.SCOPES[scope]}** · mecze w oknie selekcji: "
                f"{block['matches'].get('selection', 0)}"
            )
            body = []
            for market, item in status.items():
                selection = ((block["markets"].get(market) or {}).get("windows") or {}).get(
                    "selection"
                ) or {}
                best = selection.get(item["best"] or "", {})
                body.append(
                    [
                        MARKET_LABELS[market],
                        international.VARIANT_LABELS.get(item["best"] or "", "—"),
                        best.get("delta_log_loss"),
                        best.get("ece"),
                        "tak" if item["validated"] else "nie",
                        item["verdict"],
                    ]
                )
            _table(
                ui.table(
                    ["Rynek", "Najlepszy wariant", "Δ log loss", "ECE", "Zwalidowany", "Status"],
                    body,
                    numeric=(2, 3),
                )
            )
        schedule = report.get("schedule") or {}
        if schedule.get("verdict"):
            st.caption(f"Terminarz z pucharami w modelu ligowym: {schedule['verdict']}.")
        shadow = international.shadow_evaluation(db, now=c.now())
        st.caption(
            f"Prognozy cieniowe: {shadow['forecasts']}, rozliczone: {shadow['settled']}"
            + (f", log loss 1X2: {shadow['log_loss_1x2']}" if shadow["log_loss_1x2"] else "")
            + ". Aktywacja w generatorze wymaga Twojej decyzji (nie została podjęta)."
        )
    if st.button("Uruchom eksperymenty międzynarodowe", key="intl_run"):
        with st.spinner("Eksperymenty A–E, reprezentacje i terminarz…"):
            international.run(db, c.paths(), now=c.now())
        st.rerun()


def _table(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def label(code: str | None) -> str:
    league = COMPETITIONS.get(code or "")
    return CATEGORIES.get(league.category, "") if league else ""
