"""UCZENIE AI: how the models learn, how good they are and who decides about a new model.

Champion (served) vs challenger (shadow mode) with a plain conclusion, controlled promotion
by the user (AKTYWUJ NOWY MODEL / POZOSTAW POPRZEDNI MODEL), rollback to an older version,
the weekly review with its minimum conditions, prospective quality of settled forecasts,
error analysis, coupon results, the feature ablation and the full learning history.
"""

import pandas as pd
import streamlit as st

from sports_edge import forecast_quality as fq
from sports_edge import insights as ins
from sports_edge import learning, retraining
from sports_edge import prospective_eval as pe
from sports_edge.app import advanced_ui, ui
from sports_edge.app import common as c
from sports_edge.collection import completeness
from sports_edge.performance import builder_coupons, coupon_performance
from sports_edge.services import (
    ELO_FAMILY,
    GOALS_FAMILY,
    MODEL_SCOPES,
    ROLE_LABELS,
    SCOPE_LABELS,
    ablation_reports,
    activation_block,
    challenger_versions,
    model_versions_of,
    switch_models,
    train_now,
)

STATUS_TONE = {
    "Oczekuje na nowe dane": "neutral",
    "Dane wystarczające": "info",
    "Trening w toku": "info",
    "Walidacja w toku": "info",
    "Challenger gotowy": "warn",
    "Odrzucony": "neutral",
    "Aktywowany": "ok",
    "Przywrócono starszą wersję": "info",
}
EVIDENCE_TONE = {"none": "neutral", "identical": "warn", "waiting": "info", "sufficient": "ok"}
FLAG_TONE = {"optimistic": "bad", "cautious": "warn", "ok": "ok", "insufficient": "neutral"}
FAMILY_LABEL = {"elo": "Elo (1X2, podwójna szansa)", "goals": "Dixon–Coles (gole i rynki)"}


def learning_page() -> None:
    c.setup_page()
    ui.app_header("Uczenie AI", "Jak modele się uczą, jak są oceniane i kto decyduje o zmianie")
    learning_panel()


def learning_panel() -> None:
    db = c.store()
    now = c.now()
    ui.explanation(
        "Zasady",
        [
            "Nowy model (challenger) nigdy nie zastępuje aktywnego sam: działa w trybie cienia, "
            "a o aktywacji decydujesz Ty.",
            "Wybór modelu i statusy: okno selekcji 2023/24. Holdout końcowy od 2024-07-01 jest "
            "tylko raportowany (wcześniej był używany do decyzji).",
            "Najważniejsza ocena jest prospektywna: prognozy zapisane przed meczem i rozliczone "
            "po wyniku.",
        ],
    )
    gate_panel(db)
    # TASK 019 §4.5: the rules of the evidence once; each scope keeps only its table
    ui.explanation(
        "Challengery i dowody — dla wszystkich zakresów",
        [
            "Różnica: log loss challengera minus aktywnej wersji na tych samych meczach (ujemna "
            "= challenger lepszy); przedział z bootstrapu po meczach.",
            "Aktywacja zawsze należy do Ciebie — system nigdy nie aktywuje modelu sam.",
            "Challenger bez wspólnych rozliczonych meczów po 14 dniach albo zastąpiony nowszym "
            "wygasa przy przeglądzie; identyczny z championem (ten sam protokół) wygasa od razu.",
            "Szacowana data to szacunek z tempa rozliczeń ostatnich 14 dni, nie termin.",
        ],
    )
    for scope in MODEL_SCOPES:
        _scope(db, scope, now)
    _review(db, now)
    zone_panel()
    market_panel()
    _quality(db, now)
    _coupons(db)
    advanced_ui.engine_panel()
    _ablation()
    ui.section("Historia uczenia", "każda decyzja jest zapisana i nie może być zmieniona")
    rows = retraining.history(db)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=320)
    else:
        st.caption("Brak zapisanych zdarzeń modeli.")


def gate_text(db) -> tuple[str, str]:
    """(state label, tone) of the v3 activation gate — the user's recorded decision."""
    state = learning.gate_state(db)
    decision = state["decision"]
    if state["accepted"]:
        return (
            "Bramka v3 zaakceptowana przez Ciebie "
            + c.local_time(decision["created_at"], "%d.%m.%Y %H:%M"),
            "ok",
        )
    if decision is not None:
        return (
            "Bramka v3: akceptacja wycofana "
            + c.local_time(decision["created_at"], "%d.%m.%Y %H:%M")
            + " — "
            + learning.GATE_WAITING,
            "warn",
        )
    return "Bramka v3: " + learning.GATE_WAITING, "warn"


def gate_panel(db) -> None:
    """TASK 013 §6: the user's explicit, recorded decision about the activation gate of
    protocol v3. Accepting activates nothing; a withdrawal is a new row of the history."""
    from sports_edge import rolling

    n = rolling.load_policy(c.paths().config).shadow_matches
    state = learning.gate_state(db)
    label, tone = gate_text(db)
    ui.section("Bramka protokołu v3", "Twoja decyzja o metodologii aktywacji")
    ui.pills([(label, tone)])
    ui.explanation(
        "Co oznacza bramka v3",
        [
            "Protokół v3 nie ma nietkniętego historycznego holdoutu: okna przesuwają się z datą "
            "treningu, a ostatnie 12 miesięcy wybiera hiperparametry.",
            f"Dlatego o aktywacji challengera v3 rozstrzyga porównanie z aktywną wersją w trybie "
            f"cienia — co najmniej {n} wspólnych rozliczonych meczów.",
            "Bez Twojej akceptacji challengery v3 są trenowane i obserwowane w cieniu, ale ich "
            "aktywacja jest zablokowana. Akceptacja niczego nie aktywuje: aktywacja to osobna "
            f"decyzja i wymaga dowodów (n/{n}).",
            "Każda decyzja (akceptacja, wycofanie) to nowy wpis historii uczenia — nic nie jest "
            "nadpisywane.",
        ],
    )
    if state["accepted"]:
        sure = st.checkbox(
            "Chcę wycofać akceptację bramki v3 (aktywacja challengerów v3 znów zablokowana)",
            key="gate_withdraw_sure",
        )
        if st.button(
            "Wycofaj akceptację bramki v3",
            key="gate_withdraw",
            disabled=not sure,
            icon=":material/undo:",
        ):
            learning.withdraw_gate(db, now=c.now(), note="panel Modele → Uczenie AI")
            st.rerun()
        return
    sure = st.checkbox(learning.GATE_TEXT.format(n=n), key="gate_accept_sure")
    if st.button(
        "Zapisz akceptację bramki v3",
        key="gate_accept",
        disabled=not sure,
        icon=":material/verified:",
    ):
        learning.accept_gate(db, now=c.now(), shadow_matches=n, note="panel Modele → Uczenie AI")
        st.rerun()


def _scope(db, scope: str, now) -> None:
    status = retraining.scope_status(db, scope, now=now)
    ui.section(SCOPE_LABELS[scope].capitalize(), "champion i challenger")
    ui.pills([(status, STATUS_TONE.get(status, "neutral"))])
    compared = retraining.comparison(db, scope, now=now)
    _decision_table(compared)
    cards = []
    for key, entry in compared.items():
        champion, challenger = entry["champion"], entry["challenger"]
        lines = [
            f"Aktywna: {short_version(champion.get('version'))} "
            f"(protokół {champion.get('protocol', '—')})",
            f"Challenger: {short_version(challenger.get('version'), 'brak')}"
            + (f" (protokół {challenger['protocol']})" if challenger else ""),
            entry["conclusion"],
        ]
        cards.append(
            ui.card(
                f"<b>{ui.esc(FAMILY_LABEL[key])}</b>" + ui.bullets(lines),
                tone="warn" if challenger else "",
            )
        )
    ui.card_grid(cards)
    waiting = challenger_versions(db).get(scope, {})
    if waiting:
        sure = st.checkbox(
            "Rozumiem: zmiana modelu wpływa na wszystkie przyszłe prognozy i kupony tego zakresu",
            key=f"learn_sure_{scope}",
        )
        cols = st.columns(2)
        versions = {
            {"elo": ELO_FAMILY, "goals": GOALS_FAMILY}[k]: row["version_id"]
            for k, row in waiting.items()
        }
        blocked = activation_block(db, scope, versions)  # TASK 013 §6.2
        if blocked:
            st.caption(f"Aktywacja zablokowana: {blocked}.")
        if cols[0].button(
            "AKTYWUJ NOWY MODEL",
            key=f"learn_on_{scope}",
            type="primary",
            disabled=not sure or bool(blocked),
            icon=":material/rocket_launch:",
        ):
            _switch(scope, versions, "Decyzja użytkownika w panelu Uczenie AI (aktywacja)")
        if cols[1].button(
            "POZOSTAW POPRZEDNI MODEL",
            key=f"learn_keep_{scope}",
            disabled=not sure,
            icon=":material/undo:",
        ):
            learning.keep_previous(
                db,
                scope=scope,
                versions=versions,
                now=c.now(),
                reason="Decyzja użytkownika w panelu Uczenie AI",
            )
            st.rerun()
    _versions(db, scope)
    _train(scope)


def _switch(scope: str, versions: dict[str, str], reason: str) -> None:
    with st.spinner("Zmiana modelu i prognozy nadchodzących meczów…"):
        try:
            switch_models(
                c.store(), c.paths(), scope=scope, versions=versions, now=c.now(), reason=reason
            )
        except ValueError as error:  # TASK 013: the v3 gate or too little shadow evidence
            st.error(str(error))
            return
    st.cache_data.clear()
    st.rerun()


def _versions(db, scope: str) -> None:
    """Every version of the scope; any of them can be served again (also a rejected one)."""
    rows = model_versions_of(db, scope)
    with st.expander("Wszystkie wersje modeli i przywracanie"):
        if not rows:
            st.caption("Brak wersji w tym zakresie.")
            return
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Model": "Elo" if r["family"] == ELO_FAMILY else "Dixon–Coles",
                        "Wersja": r["version"],
                        "Utworzona": c.local_time(r["created_at"], "%d.%m.%Y %H:%M"),
                        "Protokół": r["protocol"],
                        "Rola": ROLE_LABELS[r["role"]],
                        "Log loss (okno selekcji)": r["log_loss"],
                        "Zwalidowane rynki": ", ".join(r["validated"]) or "—",
                    }
                    for r in rows
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "Wersje protokołu v1 mają statusy zawieszone (holdout był użyty do decyzji) — "
            "ich typy są zawsze eksperymentalne."
        )
        chosen = {}
        cols = st.columns(2)
        for col, family, label in (
            (cols[0], ELO_FAMILY, "Elo (1X2, podwójna szansa)"),
            (cols[1], GOALS_FAMILY, "Dixon–Coles (gole i rynki)"),
        ):
            options = [r for r in rows if r["family"] == family]
            if not options:
                continue
            active = next((i for i, r in enumerate(options) if r["role"] == "active"), 0)
            pick = col.selectbox(
                label,
                options,
                index=active,
                key=f"learn_pick_{scope}_{family}",
                format_func=lambda r: (
                    f"{r['version']} · {r['protocol']} · {ROLE_LABELS[r['role']]}"
                ),
            )
            if pick["role"] != "active":
                chosen[family] = pick["version_id"]
        sure = st.checkbox(
            "Rozumiem: wybrane wersje zastąpią aktywne modele tego zakresu",
            key=f"learn_pick_sure_{scope}",
        )
        blocked = activation_block(db, scope, chosen) if chosen else None  # TASK 013 §6.2
        if blocked:
            st.caption(f"Aktywacja zablokowana: {blocked}.")
        if st.button(
            "Ustaw wybrane jako aktywne",
            key=f"learn_pick_go_{scope}",
            icon=":material/restore:",
            disabled=not (sure and chosen) or bool(blocked),
        ):
            _switch(scope, chosen, "Wybór wersji przez użytkownika w panelu Uczenie AI")


def _train(scope: str) -> None:
    """Training on demand: new versions become challengers; activation stays with the user."""
    force = st.checkbox(
        "Wymuś trening także bez nowych meczów (challenger zostanie oznaczony jako wymuszony)",
        key=f"learn_force_{scope}",
        help="Bez nowych meczów od treningu aktywnej wersji challenger miałby te same dane — "
        "domyślnie trening jest wtedy odrzucany.",
    )
    if st.button(
        f"Trenuj teraz — {SCOPE_LABELS[scope]}",
        key=f"learn_train_{scope}",
        icon=":material/model_training:",
        help="Protokół v3 (okna ruchome do dziś; v2, gdy historia jest za krótka) na aktualnych "
        "danych (kilka minut). Nowe wersje trafiają do trybu cienia jako challengery — "
        "aktywujesz je sam po porównaniu.",
    ):
        with st.status("Trening w toku…", expanded=True) as box:
            try:
                train_now(
                    c.store(),
                    c.paths(),
                    now=c.now(),
                    scope=scope,
                    progress=box.write,
                    force=force,
                )
            except ValueError as error:
                box.update(label="Trening nieudany", state="error")
                st.error(str(error))
                return
            box.update(label="Walidacja zakończona — challengery gotowe", state="complete")
        st.cache_data.clear()
        st.rerun()


def _statuses(entry: dict) -> str:
    if not entry:
        return "—"
    statuses = entry.get("statuses") or {}
    if not statuses:
        return "brak pomiaru statusów"
    validated = sorted(k for k, v in statuses.items() if v == "validated_predictive")
    other = sorted(k for k, v in statuses.items() if v != "validated_predictive")
    return (
        "zwalidowane: "
        + (", ".join(validated) or "—")
        + " · eksperymentalne: "
        + (", ".join(other) or "—")
    )


def _window(entry: dict) -> str:
    decision = entry.get("decision") or {}
    if not decision:
        return "—" if not entry else f"protokół {entry.get('protocol')} (bez okna selekcji)"
    windows = entry.get("windows") or {}
    period = (
        f"v3 od {windows.get('selection_from')} · "  # TASK 012: rolling window of this version
        if windows
        else f"{entry.get('protocol')} · "
    )
    return (
        f"{period}n={decision.get('n')} · log loss {ins.num(decision.get('log_loss'), 5)} · "
        f"ECE {ins.num(decision.get('ece'), 4)}"
    )


def _holdout(entry: dict) -> str:
    holdout = entry.get("holdout") or {}
    if not holdout:
        return "—"
    return f"n={holdout.get('n')} · log loss {ins.num(holdout.get('log_loss'), 4)}"


def _evidence(pro: dict) -> str:
    needed = pro.get("needed") or pe.load_policy().decision_matches
    return f"{pro.get('matches', 0)}/{needed}"


def _difference(pro: dict) -> str:
    ci = pro.get("ci95")
    if not ci:
        return "—"
    return f"{ins.num(pro['difference'], 4)} ({ins.num(ci[0], 4)}…{ins.num(ci[1], 4)})"


def short_version(value, empty: str = "—") -> str:
    """TASK 019 §4.5: a model version without its full identifier in the basic view."""
    if not value:
        return empty
    text = str(value)
    return text if len(text) <= 24 else text[:12] + "…"


def _decision_table(compared: dict) -> None:
    """TASK 011 §5.1: champion and challenger side by side with the state of the evidence."""
    rows = []
    for key, entry in compared.items():
        champion, challenger = entry["champion"], entry["challenger"]
        pro = entry.get("prospective") or {}
        for role, version in (("aktywna", champion), ("challenger", challenger)):
            if role == "challenger" and not version:
                continue
            rows.append(
                {
                    "Model": FAMILY_LABEL[key],
                    "Rola": role,
                    "Wersja": short_version((version or {}).get("version")),
                    "Okno selekcji": _window(version or {}),
                    "Holdout (tylko raport)": _holdout(version or {}),
                    "Statusy rynków": _statuses(version or {}),
                    "Wspólne rozliczone mecze": str(pro.get("matches", 0)) if challenger else "—",
                    # TASK 015 §2.4: the evidence clock n/300 and its ESTIMATED date
                    "Dowody n/300": _evidence(pro) if challenger else "—",
                    "Szacowana data 300 meczów": (entry.get("estimate") or {}).get("label", "—")
                    if role == "challenger"
                    else "—",
                    "Różnica log loss (95%)": _difference(pro) if role == "challenger" else "—",
                    "Stan": entry["state_label"],
                }
            )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    ui.pills(
        [
            (f"{FAMILY_LABEL[key]}: {entry['state_label']}", EVIDENCE_TONE[entry["state"]])
            for key, entry in compared.items()
        ]
    )
    for key, entry in compared.items():
        if entry.get("estimate"):
            st.caption(f"{FAMILY_LABEL[key]} — {entry['estimate']['label']}")


def _zone_frame(rows: list[dict], minimum: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Model": pe.FAMILY_LABELS.get(r["family"], r["family"]),
                "Rynek": pe.market_label(r["market"]),
                "Przedział szansy": r["range"],
                "Mecze (n)": r["matches"],
                "Selekcje": r["selections"],
                "Średnia szansa": ins.pct(r["mean_p"]),
                "Trafność": ins.pct(r["freq"]),
                "95% (na meczach)": f"{ins.pct(r['ci95'][0])}–{ins.pct(r['ci95'][1])}",
                "Ocena": r["flag_label"]
                if r["flag"] != "insufficient"
                else f"za mało meczów (n < {minimum})",
            }
            for r in rows
        ]
    )


def zone_panel() -> None:
    """TASK 011 §2.3: reliability of the coupon zone (chance ≥ 60%) counted on MATCHES."""
    evaluation = c.evaluation()
    found, policy = evaluation["calibration"], evaluation["policy"]
    ui.section(
        "Strefa kuponów — kalibracja prospektywna",
        f"szansa ≥ {ins.pct(policy['zone'], 0)}, liczone na meczach (nie na selekcjach)",
    )
    if not found["rows"]:
        ui.empty_state(
            "Brak rozliczonych prognoz w strefie kuponów",
            "Tabela pojawi się po wynikach meczów z prognozą zapisaną przed meczem.",
        )
        return
    cols = st.columns(2)
    scope = cols[0].selectbox(
        "Zakres lig",
        ["all", "core", "extended"],
        format_func=lambda k: pe.SCOPE_LABELS[k],
        key="zone_scope",
    )
    role = cols[1].selectbox(
        "Wersje", ["active", "shadow"], format_func=lambda k: pe.ROLE_LABELS[k], key="zone_role"
    )
    chosen = [
        r
        for r in found["rows"]
        if r["scope"] == scope and r["role"] == role and (r["in_zone"] or r["zone_total"])
    ]
    minimum = found["min_matches"]
    coupon = [r for r in chosen if r["coupon_market"]]
    if coupon:
        st.dataframe(_zone_frame(coupon, minimum), hide_index=True, width="stretch")
    else:
        st.caption("Brak rozliczonych selekcji tej grupy w strefie kuponów.")
    flagged = [r for r in coupon if r["flag"] in ("optimistic", "cautious") and not r["zone_total"]]
    ui.pills(
        [
            (
                f"{pe.market_label(r['market'])} {r['range']}: {r['flag_label']}",
                FLAG_TONE[r["flag"]],
            )
            for r in flagged
        ]
        or [("Brak flag w strefie kuponów", "ok")]
    )
    other = [r for r in chosen if not r["coupon_market"]]
    if other:
        with st.expander("Pozostałe rynki (bez przedziału ostrożnego — nie trafiają do kuponów)"):
            st.dataframe(_zone_frame(other, minimum), hide_index=True, width="stretch")
    st.caption(
        "Jednostka to mecz: kilka rynków jednego meczu (np. „gospodarz strzeli” i „gospodarz "
        "powyżej 0,5”) to jedna zależna obserwacja — w przedziale mecz wnosi średnią szansę i "
        "średni wynik swoich selekcji. Przedział Wilsona 95% liczony na meczach (konserwatywny). "
        f"Flaga „zbyt optymistyczna” wymaga co najmniej {minimum} meczów i górnej granicy "
        "poniżej średniej szansy; flaga niczego nie zmienia w prognozach, progach ani statusach "
        "— jest informacją przy typach."
    )


def _market_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Model": pe.FAMILY_LABELS.get(r["family"], r["family"]),
                "Rynek": pe.market_label(r["market"]),
                "Wspólne mecze": r["matches"],
                "Log loss modelu": r["model_log_loss"],
                "Log loss rynku STS": r["market_log_loss"],
                "Różnica (95%)": _difference(r),
                "Brier model / rynek": f"{ins.num(r['model_brier'], 4)} / "
                f"{ins.num(r['market_brier'], 4)}"
                if r["model_brier"] is not None
                else "—",
                "Stan": r["state_label"],
            }
            for r in rows
        ]
    )


def market_panel() -> None:
    """TASK 011 §3: the model against the de-margined STS price of the same matches."""
    rows = c.evaluation()["market"]
    ui.section(
        "Porównanie z rynkiem STS",
        "prospektywnie: te same rozliczone mecze, kurs z ostatniego dokumentu STS przed meczem",
    )
    scope = st.selectbox(
        "Zakres lig",
        ["all", "core", "extended"],
        format_func=lambda k: pe.SCOPE_LABELS[k],
        key="market_scope",
    )
    chosen = [
        r
        for r in rows
        if r["scope"] == scope
        and (r["market"] == pe.POOLED or r["market"] in pe.COUPON_MARKETS.get(r["family"], ()))
    ]
    st.dataframe(_market_frame(chosen), hide_index=True, width="stretch")
    _age_panel(chosen)
    st.caption(
        "Marża usunięta metodą „power” (p = (1/kurs)^k, suma 1) — ostrzejszy wzorzec niż "
        "proporcjonalny w strefie faworytów; wynik metody proporcjonalnej jest zapisany jako "
        "kontrola. Różnica = log loss modelu − rynku (ujemna: model lepszy). Werdykt dopiero "
        "od 300 wspólnych meczów; brak wspólnych meczów to „nieznane”, nigdy „false”. Kurs "
        "służy wyłącznie do oceny po fakcie — nigdy nie jest cechą modelu."
    )


def _age_panel(rows: list[dict]) -> None:
    """TASK 012 §4.2: the comparison per age of the STS document (sensitivity only)."""
    pooled = [r for r in rows if r["market"] == pe.POOLED and r.get("by_document_age")]
    if not pooled:
        return
    frame = []
    for r in pooled:
        age = r.get("document_age_hours") or {}
        for band, info in r["by_document_age"].items():
            frame.append(
                {
                    "Model": pe.FAMILY_LABELS.get(r["family"], r["family"]),
                    "Wiek kursu STS": band,
                    "Mecze": info["matches"],
                    "Udział meczów": ins.pct((age.get("bands") or {}).get(band)),
                    "Log loss model / rynek": f"{ins.num(info['model_log_loss'], 3)} / "
                    f"{ins.num(info['market_log_loss'], 3)}"
                    if info["model_log_loss"] is not None
                    else "—",
                    "Różnica (95%)": _difference(info) if info.get("ci95") else "—",
                    "Stan": info["state_label"],
                }
            )
    with st.expander("Według wieku kursu STS (≤ 3 h, 3–12 h, > 12 h przed meczem)"):
        st.dataframe(pd.DataFrame(frame), hide_index=True, width="stretch")
        medians = ", ".join(
            f"{pe.FAMILY_LABELS.get(r['family'], r['family'])}: "
            f"{ins.num((r.get('document_age_hours') or {}).get('median'), 1)} h"
            for r in pooled
        )
        st.caption(
            f"Mediana wieku użytego dokumentu (czas meczu − czas z dokumentu): {medians}. "
            "Podział to tylko kontrola wrażliwości — werdykt liczony jest łącznie."
        )


@st.cache_data(ttl=600, show_spinner=False, max_entries=8)
def _conditions(_db, version: tuple, at) -> dict:
    """Training conditions of every scope (unique matches since training) — cached."""
    return {
        scope: retraining.conditions(_db, scope, competitions, now=at)
        for scope, competitions in MODEL_SCOPES.items()
    }


def _review(db, now) -> None:
    ui.section(
        "Przegląd modeli",
        "w panelu Automatyzacja AI według harmonogramu; na żądanie — Trenuj teraz wyżej",
    )
    due = retraining.due(db, now=now)
    ui.pills([("Przegląd zalecany" if due else "Przegląd aktualny", "warn" if due else "ok")])
    found = _conditions(db, c.data_version(db), c.minute())
    rows = []
    for scope in MODEL_SCOPES:
        ready, reasons = found[scope]
        moments = retraining.timeline(db, scope)
        rows.append(
            {
                "Zakres": SCOPE_LABELS[scope],
                "Ostatni przegląd": c.local_time(moments["review"], "%d.%m %H:%M"),
                "Ostatni trening": c.local_time(moments["training"], "%d.%m %H:%M"),
                "Ostatnia walidacja": c.local_time(moments["validation"], "%d.%m %H:%M"),
                "Ostatnia aktywacja": c.local_time(moments["activation"], "%d.%m %H:%M"),
                "Warunki treningu": "spełnione" if ready else "niespełnione",
                "Powód": "; ".join(reasons) or "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "Przegląd zakończony decyzją „za mało danych” jest zapisany jako wykonany — kolejny "
        "nastąpi po upływie okresu przeglądu. Walidacja w protokole v2 jest częścią treningu."
    )
    if st.button("Uruchom przegląd teraz", icon=":material/fact_check:"):
        with st.spinner("Przegląd modeli…"):
            result = retraining.review(db, c.paths(), now=c.now(), trigger="manual")
        st.cache_data.clear()
        st.json(result, expanded=False)


def _quality(db, now) -> None:
    ui.section("Jakość prognoz", "prospektywnie: zapisane przed meczem, rozliczone po wyniku")
    rows = fq.items(db)
    report = completeness(db, now=now)
    ui.metric_cards(
        [
            ("Prognozy", str(report["predictions"]), f"przyszłe: {report['upcoming']}"),
            (
                "Rozliczone",
                str(sum(report["settlements"].values())),
                f"oczekujące: {report['pending']}",
            ),
            ("Bez wyniku > 48 h", str(report["missing_results_count"]), "do uzupełnienia"),
        ]
    )
    if not rows:
        ui.empty_state("Brak rozliczonych prognoz", "Pojawią się po wynikach meczów z prognozą.")
        return
    markets = fq.by_market(rows)
    table = []
    for group, m in markets.items():
        low, high = m["log_loss_ci95"]
        table.append(
            {
                "Rynek": group,
                "Mecze (niezależne)": m["matches"],
                "Prognozy": m["events"],
                "Log loss": m["log_loss"],
                "95% log loss": f"{ins.num(low, 3)}–{ins.num(high, 3)}",
                "Brier": m["brier"],
                "ECE": m["ece"],
                "Trafność": ins.pct(m["accuracy"])
                if group != "Remisy"
                else f"prognoza {ins.pct(m['predicted'])} · wynik {ins.pct(m['observed'])}",
                "Co znaczy trafność": m["accuracy_basis"],
                "Próba": "mała" if m["small_sample"] else "wystarczająca",
            }
        )
    st.markdown("**Każdy rynek osobno** — bez mieszania łatwych i trudnych rynków")
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    if any(m["small_sample"] for m in markets.values()):
        st.warning(
            "Mała próba: mniej niż 30 niezależnych meczów w części rynków — wnioski są wstępne. "
            "Kilka rynków jednego meczu to nie są niezależne dowody."
        )
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Kalibracja wg przedziału szansy (wszystkie rynki, na selekcjach)**")
        st.dataframe(
            pd.DataFrame(
                [
                    {k: v for k, v in r.items() if k != "flag"}  # TASK 011: flags on matches only
                    for r in fq.calibration(rows)
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "Liczone na selekcjach: rynki jednego meczu nie są niezależne, więc przedziały są "
            "tu zbyt wąskie. Ocenę i flagi daje sekcja „Strefa kuponów” — liczona na meczach."
        )
    with right:
        st.markdown("**Kupony: SOLO, AKO i profile**")
        split = fq.coupon_split(builder_coupons(db))
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Grupa": r["grupa"],
                        "Kupony": r["kupony"],
                        "Rozliczone": r["rozliczone"],
                        "Trafność": ins.pct(r["trafność"]),
                        "95%": f"{ins.pct(r['trafność_ci95'][0])}–{ins.pct(r['trafność_ci95'][1])}"
                        if r["trafność_ci95"]
                        else "—",
                        "Prognoza (śr.)": ins.pct(r["prognoza_średnio"]),
                        "ROI": ins.pct(r["roi"]),
                    }
                    for r in split
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with st.expander("Wg ligi, wersji modelu i tygodnia (stabilność)"):
        for title, key in (("Liga", "competition"), ("Wersja modelu", "version")):
            st.markdown(f"**{title}**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            title: k[:12] if key == "version" else k,
                            **{m: v.get(m) for m in ("events", "log_loss", "ece", "small_sample")},
                        }
                        for k, v in fq.by(rows, key).items()
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        st.markdown("**Tydzień**")
        st.dataframe(
            pd.DataFrame(
                [
                    {"tydzień": k, **{m: v.get(m) for m in ("events", "log_loss", "ece")}}
                    for k, v in fq.stability(rows).items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    legs = [
        leg.get("league", "")
        for coupon in builder_coupons(db)
        for leg in coupon["proposal"]["legs"]
    ]
    findings = fq.error_analysis(rows, leg_leagues=legs or None)
    ui.section("Analiza błędów prognoz", "gdzie modele mylą się najczęściej")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Obszar": f["topic"],
                    "Wniosek": f["finding"],
                    "n": f["n"],
                    "Próba": "mała" if f["small_sample"] else "wystarczająca",
                }
                for f in findings
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _coupons(db) -> None:
    ui.section("Wyniki kuponów", "trafność wobec prognozy, ROI, obsunięcia i serie")
    report = coupon_performance(db)
    if not report:
        st.caption("Brak zapisanych kuponów.")
        return
    rows = []
    for r in report.values():
        ci = r["hit_rate_ci95"]
        rows.append(
            {
                "Profil": r["label"],
                "Kupony": r["coupons"],
                "Rozliczone": r["settled"],
                "Nierozliczone": r["pending"],
                "Wygrane": r["wins"],
                "Trafność": ins.pct(r["hit_rate"]),
                "95%": f"{ins.pct(ci[0])}–{ins.pct(ci[1])}" if ci else "—",
                "Prognoza (śr.)": ins.pct(r["avg_predicted"]),
                "ROI": ins.pct(float(r["roi"])) if r["roi"] is not None else "—",
                "Maks. obsunięcie": ins.money(r["max_drawdown"]),
                "Seria porażek": r.get("longest_losing", "—"),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("ROI i trafność są wiarygodne dopiero przy dziesiątkach rozliczonych kuponów.")


def _ablation() -> None:
    reports = ablation_reports(c.store())
    ui.section("Eksperyment ablacyjny", "A Elo · B Dixon–Coles · C DC + cechy · D prosty ML")
    if not reports:
        st.caption("Eksperyment nie był jeszcze uruchomiony (CLI: sports-edge research ablation).")
        return
    for report in reports[-2:]:
        st.markdown(f"**{SCOPE_LABELS.get(report.get('scope'), report.get('scope'))}**")
        rows = []
        for window, models in report["windows"].items():
            for model, m in models.items():
                rows.append({"okno": window, "model": model, **m})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(report.get("conclusion", ""))
