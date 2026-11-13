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


def category_label(league: str | None, sport: str | None) -> str:
    """TASK 019 §3.3: the competition category is a football notion — other sports: "—"."""
    if sport != "football":
        return "—"
    return CHOICES.get(category(league, sport), "inne")


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


def schedule_of(db, event_id: str, now: datetime) -> dict | None:
    """TASK 019 §3.1: the schedule of both teams of a cup / national event — the one source
    of the rest days for the cards and the comparison table."""
    view = _view(db, c.data_version(db), event_id, now.replace(second=0, microsecond=0))
    return view if view and view.get("schedule") else None


def event_block(db, event_id: str, now: datetime) -> None:
    """Cup / national-team context of one STS event (level 1 of the analysis)."""
    view = _view(db, c.data_version(db), event_id, now.replace(second=0, microsecond=0))
    if view is None:
        return
    ui.section(view["name"], f"{view['category_label']} · wyniki w bazie: {view['results']}")
    shadow = view.get("shadow")
    validated = [m for m, v in view["status"].items() if v["validated"]]
    if view["category"] == "national":
        # TASK 018: the forecast above comes from the national scope of the league path
        ui.explanation(
            "Reprezentacje — ta sama ścieżka co ligi",
            [
                "Prognoza modelu (Elo / Dixon–Coles zakresu „reprezentacje”), bramka per rynek "
                "i progi profili są takie same jak w ligach.",
                "Boisko: przewaga gospodarza tylko wtedy, gdy terminarz jednoznacznie wskazuje "
                "gospodarza; w przeciwnym razie miejsce meczu jest nieznane i bez przewagi.",
            ],
            tone="info",
        )
    elif shadow:
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
        points.append("Prognoza cieniowa zamrożona przed meczem — pozostaje poza kuponami.")
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
        team = view["teams"][side].get("name") or view["teams"][side]["team"]
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
                        value=f"{r.get('outcome', '')} {r.get('own_score', r['score_90'])}",
                        value_hint=f"{team} : rywal, po 90 min",
                    )
                    for r in plan["recent"]
                ]
            )
    st.caption(
        (
            "Odpoczynek liczony ze wszystkich meczów reprezentacji w bazie (te same liczby co w "
            "porównaniu drużyn); W wygrana · R remis · P porażka, wynik z perspektywy drużyny."
        )
        if view["category"] == "national"
        else "Odpoczynek i obciążenie liczone ze wszystkich wiarygodnie zidentyfikowanych meczów "
        "(liga, europejskie puchary); bez danych o minutach nie szacujemy zmęczenia zawodników."
    )


def coverage_block(db, now: datetime) -> None:
    """Źródła: separate UEFA and national-team coverage, P3 cups and unmapped headers."""
    from sports_edge import international_data as data

    rows = data.coverage(db, now=now)
    ui.section("Europejskie puchary i reprezentacje", "osobne pokrycie danych")
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
    _national_coverage(db, rows, now)
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
        national = report.get("national") or {}
        national_shadow = international.national_shadow_evaluation(db, now=c.now())
        national_status = international.market_status(report, "national")["1x2"]
        national_block = (national.get("markets") or {}).get("1x2") or {}
        registry = international.national_registry_status(db, c.paths())
        selection = ((national_block.get("windows") or {}).get("selection") or {}).get(
            national_status.get("best") or "", {}
        )
        holdout = ((national_block.get("windows") or {}).get("holdout") or {}).get(
            national_status.get("best") or "", {}
        )
        active_version = international.active_national_version_id(db)
        ui.section(
            "Walidacja reprezentacji · 1X2 (90 min)",
            "selekcja decyduje; holdout jest tylko raportowany",
        )
        criteria = national_status.get("verdict", "brak danych")
        criterion_rows = [
            [
                item.get("name"),
                item.get("value"),
                "spełnione" if item.get("passed") else "niespełnione",
            ]
            for item in national_block.get("decision", {}).get("criteria", [])
        ]
        if criterion_rows:
            _table(ui.table(["Warunek", "Wartość", "Status"], criterion_rows, numeric=(1,)))
        ui.explanation(
            "Status rynku: " + criteria,
            [
                f"Selekcja: n={selection.get('n', 0)}, log loss={selection.get('log_loss', 'brak danych')}, "
                f"baza={((national_block.get('windows') or {}).get('selection') or {}).get('baseline', {}).get('log_loss', 'brak danych')}, "
                f"Δ={selection.get('delta_log_loss', 'brak danych')}, CI95={selection.get('ci95', 'brak danych')}, ECE={selection.get('ece', 'brak danych')}.",
                f"Holdout (bez wpływu na wybór): n={holdout.get('n', 0)}, log loss={holdout.get('log_loss', 'brak danych')}, ECE={holdout.get('ece', 'brak danych')}.",
                (
                    "Benchmark STS 1X2 przed meczem na tych samych wydarzeniach: "
                    f"n={national_block['market_benchmark']['n_events']}, "
                    f"log loss modelu={national_block['market_benchmark']['model_log_loss']}, "
                    f"rynku={national_block['market_benchmark']['market_log_loss']}."
                    if national_block.get("market_benchmark")
                    else "Benchmark STS: nieznany — brak potwierdzonych kursów przedmeczowych na tych samych wydarzeniach."
                ),
                "Polityka rejestru modeli: "
                + (
                    "spełniona"
                    if registry["status"] == "validated"
                    else "niespełniona — " + "; ".join(registry["reasons"])
                ),
                f"Prognozy cieniowe: {national_shadow['forecasts']}; rozliczone pewnym wynikiem 90-minutowym: {national_shadow['settled']}; niedopasowane: {national_shadow['unmatched']}; nieprawidłowe: {national_shadow['invalid_result']}.",
                "Decyzja użytkownika: "
                + (
                    "aktywna dla bieżącej wersji " + str(active_version)
                    if active_version
                    else "brak aktywnej decyzji dla bieżącej wersji"
                ),
            ],
            tone="warn" if not national_status["validated"] or not active_version else "info",
        )
        friendly = national.get("friendly_evaluation") or {}
        if friendly:
            friendly_rows = []
            for window in ("selection", "holdout"):
                values = friendly.get(window) or {}
                chosen = values.get(national_status.get("best") or "") or {}
                baseline = values.get("baseline") or {}
                if values:
                    friendly_rows.append(
                        [
                            window,
                            chosen.get("n"),
                            chosen.get("log_loss"),
                            baseline.get("log_loss"),
                            chosen.get("ece"),
                        ]
                    )
            if friendly_rows:
                st.caption(
                    "Mecze towarzyskie — raport osobny, nie wybierają wariantu ani nie łączą się z oficjalną pulą."
                )
                _table(
                    ui.table(
                        ["Okno", "n", "log loss wariantu", "log loss bazy", "ECE"],
                        friendly_rows,
                        numeric=(1, 2, 3, 4),
                    )
                )
    if st.button("Uruchom eksperymenty międzynarodowe", key="intl_run"):
        with st.spinner("Eksperymenty A–E, reprezentacje i terminarz…"):
            international.run(db, c.paths(), now=c.now())
        st.rerun()


def national_coupon_block(db, day: str, now: datetime, candidates=()) -> None:
    """International STS matches of competitions WITHOUT data (TASK 018: the others follow
    the league path and are shown with the leagues)."""
    from sports_edge import international

    codes = (*international.NATIONAL_OFFICIAL, international.FRIENDLY)
    headers = [header for code in codes for header in COMPETITIONS[code].sts_leagues]
    marks = ",".join("?" for _ in headers) or "''"
    events = db.rows(
        f"""SELECT * FROM events WHERE bookmaker_id='sts' AND sport='football'
        AND (offer_date=? OR (offer_date IS NULL AND substr(start_at,1,10)=?))
        AND (league IN ({marks}) OR league IN (
            SELECT league FROM events WHERE bookmaker_id='sts' AND sport='football'
            GROUP BY league HAVING UPPER(league) LIKE '%REPREZENTAC%'
                OR UPPER(league) LIKE '%LIGA NARODÓW%'
                OR UPPER(league) LIKE '%ELIMINACJE%'
                OR UPPER(league) LIKE '%MISTRZOSTWA%'
                OR UPPER(league) LIKE '%TOWARZYSKIE%'
                OR UPPER(league) LIKE '%MIĘDZYNARODOWE%'
                OR UPPER(league) LIKE '%MŁODZIEŻOWE%'))
            AND UPPER(league) NOT LIKE '%KLUB%'
        ORDER BY start_at,id""",
        (day, day, *headers),
    )
    # TASK 018: matches of national competitions with history follow the league path (their
    # forecasts and selections are in "Najlepsze typy" and the coupons); only competitions
    # without data in the catalogue are listed here. TASK 019 §2.1: one row per match (the
    # same match printed twice in the offer is shown once), grouped by competition, in a
    # closed panel UNDER the coupons with a one-line summary.
    found = no_data_matches(events)
    if not found:
        return
    leagues = sorted({row[0] for row in found})
    summary = (
        f"Reprezentacje — rozgrywki bez danych: {_matches(len(found))} z "
        f"{_competitions(len(leagues))} bez historii"
    )
    with ui.details(summary, icon=":material/public:"):
        st.caption(
            "Te nagłówki STS nie mają historii wyników w katalogu rozgrywek — mecze są bez "
            "prognozy (tak samo jak liga bez danych). Mecze reprezentacji z historią są "
            "analizowane jak ligi (Najlepsze typy i kupony)."
        )
        _table(ui.table(["Rozgrywki (nagłówek STS)", "Mecz", "Godzina", "Status"], found))


def no_data_matches(events: list[dict]) -> list[list[str]]:
    """[header, match, time, status] of the matches without data — unique, by competition."""
    seen: set[tuple] = set()
    rows = []
    for event in events:
        league = for_sts_league(event.get("league") or "", event.get("sport"))
        if league is not None and league.category == "national":
            continue
        name = (
            event.get("name_raw")
            or f"{event.get('home') or 'brak danych'} – {event.get('away') or 'brak danych'}"
        )
        key = (event.get("league"), event.get("home") or name, event.get("away"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            [
                event.get("league") or "brak danych",
                name,
                c.local_time(event.get("start_at"), "%d.%m %H:%M"),
                "brak danych — bez prognozy",
            ]
        )
    return sorted(rows, key=lambda r: (r[0], r[2], r[1]))


def _matches(n: int) -> str:
    if n == 1:
        return "1 mecz"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} mecze"
    return f"{n} meczów"


def _competitions(n: int) -> str:
    if n == 1:
        return "1 rozgrywek"
    return f"{n} rozgrywek"


def _national_coverage(db, rows: list[dict], now: datetime) -> None:
    from sports_edge import international

    codes = (*international.NATIONAL_OFFICIAL, international.FRIENDLY)
    coverage = {row["competition"]: row for row in rows}
    history = international._national_history(db, now=now)
    map_participant = international._national_mapper(db, c.paths(), history)
    evaluation = international.national_shadow_evaluation(db, now=now)
    job = db.rows(
        "SELECT started_at,finished_at,status,reason,payload_json FROM job_runs WHERE job=? ORDER BY finished_at DESC LIMIT 1",
        ("national_shadow",),
    )
    last_run = job[0] if job else None
    shadow_counts = {
        row["competition"]: row["n"]
        for row in db.rows(
            "SELECT competition,COUNT(*) n FROM stat_observations WHERE kind=? GROUP BY competition",
            (international.NATIONAL_SHADOW_KIND,),
        )
    }
    table = []
    for code in codes:
        source = coverage.get(code)
        if source is None:
            continue
        headers = source["sts_headers"]
        marks = ",".join("?" for _ in headers) or "''"
        events = db.rows(
            f"""SELECT * FROM events WHERE bookmaker_id='sts' AND sport='football'
            AND league IN ({marks}) ORDER BY start_at,id""",
            tuple(headers),
        )
        matched = ambiguous = missing_time = missing_results = 0
        known_results = [r for r in history if r["competition"] == code]
        for event in events:
            missing_time += not bool(event.get("start_at"))
            sides = [map_participant(event.get(side) or "") for side in ("home", "away")]
            if all(item.status in {"exact", "alias"} and item.target for item in sides):
                matched += 1
            if any(item.status == "ambiguous" for item in sides):
                ambiguous += 1
            if (
                event.get("start_at")
                and datetime.fromisoformat(event["start_at"]) <= now
                and all(item.status in {"exact", "alias"} and item.target for item in sides)
            ):
                day = event.get("offer_date") or event["start_at"][:10]
                pair = (sides[0].target, sides[1].target)
                if not any(
                    (r["match_date"], r["home"], r["away"]) == (day, *pair) for r in known_results
                ):
                    missing_results += 1
        best = international.market_status(international.latest(db), "national")["1x2"].get("best")
        settled = (
            evaluation.get("by_competition", {}).get(code, {}).get(best, {}).get("n", 0)
            if best
            else 0
        )
        table.append(
            [
                source["name"],
                source["sts_events"],
                matched,
                ambiguous,
                missing_time,
                len(known_results),
                missing_results,
                shadow_counts.get(code, 0),
                settled,
                f"{last_run['finished_at']} · {last_run['status']}"
                if last_run
                else "brak uruchomień",
            ]
        )
    if table:
        ui.section("Pokrycie STS reprezentacji", "tylko nagłówki zaobserwowane w ofercie")
        _table(
            ui.table(
                [
                    "Rozgrywki",
                    "Mecze STS",
                    "Dopasowane",
                    "Niejednoznaczne",
                    "Bez terminarza",
                    "Dostępne wyniki 90'",
                    "Braki wyniku",
                    "Prognozy cienia",
                    "Rozliczone",
                    "Ostatnie zadanie",
                ],
                table,
                numeric=(1, 2, 3, 4, 5, 6, 7, 8),
            )
        )


def _table(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def label(code: str | None) -> str:
    league = COMPETITIONS.get(code or "")
    return CATEGORIES.get(league.category, "") if league else ""
