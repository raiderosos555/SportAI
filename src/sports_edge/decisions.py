"""DECYZJE DO PODJĘCIA (TASK 015 §3): every decision waiting for the user in one place.

Five decisions: the v3 gate (TASK 013), option D — the definition of the cautious bound of an
Elo double chance (TASK 013 §5.4 / TASK 014 §5.2), the optional profile PEWNY SOLO (TASK 014),
the limit of shared selections (TASK 010/014) and the challengers waiting for evidence (n/300,
no activation button before the threshold). Each shows what it changes, the evidence in
numbers (live from the measuring functions of TASK 011–014, plus the measurements of the
reports, named as such), the risk and a button with a confirmation.

Every decision is a new row of the append-only table ``user_decisions``: who, when, what, the
previous and the new state. A withdrawal is another row. Reading the panel changes nothing;
the ``decide_*`` functions are called ONLY by buttons of the interface — the system never
takes any of these decisions by itself.
"""

import json
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sports_edge import learning
from sports_edge.analysis.edge_engine import BOUND_LABELS, BOUND_SINGLE, BOUND_SUM, bound_of
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.settings import AppPaths, AppSettings, save_settings
from sports_edge.storage import Store

GATE = "v3_gate"
OPTION_D = "option_d"
SAFE_SOLO = "safe_solo"
LIMIT = "diversity_limit"
CHALLENGERS = "challengers"
NATIONAL_1X2 = "national_1x2"
KEYS = (GATE, OPTION_D, SAFE_SOLO, LIMIT, CHALLENGERS, NATIONAL_1X2)
TITLES = {
    GATE: "Bramka protokołu v3",
    OPTION_D: "Opcja D: granica „ostrożnie” podwójnej szansy Elo",
    SAFE_SOLO: "Profil PEWNY SOLO",
    LIMIT: "Limit różnicowania kuponów dnia",
    CHALLENGERS: "Challengery czekające na dowody",
    NATIONAL_1X2: "1X2 reprezentacji w Kuponie dnia",
}
WARSAW = ZoneInfo("Europe/Warsaw")
SAFE_PROFILE = "safe_solo"
HIGH_HIT = "high_hit"
SCOPE_NAMES = {"core": "5 czołowych lig", "extended": "7 lig"}
FAMILY_NAMES = {"elo": "Elo", "goals": "Dixon–Coles"}

# Measurements of the reports (on history or as-of replays), shown with their source — the
# panel never recomputes them on the fly and never adds a guess of its own.
REPORTED = {
    GATE: [
        "Walk-forward v3 kontra v2 (TASK 013 §3, retrening co miesiąc 07.2024–11.2026, końcowa "
        "reguła): 7 lig — Dixon–Coles v3 lepszy (log loss −0,0062; 95%: −0,0100…−0,0030), Elo "
        "v3 lepszy (−0,0038); 5 lig — bez istotnej różnicy (−0,0004; −0,0023…+0,0017).",
    ],
    OPTION_D: [
        "Pokrycie granicy na historii (TASK 014 §5.2, as-of 07.2024–11.2026, strefa granicy "
        "≥ 0,75): dzisiejsza definicja — zapas +10,3 pkt (5 lig, 1202 mecze) i +15,2 pkt "
        "(7 lig, 365 meczów); opcja D — zapas +2,8 pkt (1834 mecze) i +2,4 pkt (1916 meczów), "
        "cały przedział 95% nad granicą.",
        "Opcja D obejmuje o 53% (5 lig) i 5,2 razy (7 lig) więcej meczów w strefie ≥ 0,75. "
        "Najmniejszy zapas opcji D tuż pod progiem (0,70–0,75): +0,8 pkt i +0,7 pkt.",
        "Replay 24 decyzji 31.10–07.11 (TASK 014 §5.3): WYSOKA TRAFNOŚĆ — 0 kuponów dziś, "
        "1 kupon z opcją D (ostrożnie 76,9%).",
    ],
    SAFE_SOLO: [
        "Replay 24 decyzji 31.10–07.11 (TASK 014 §3.1): PEWNY SOLO — 2 kupony w 8 dni "
        "(śr. szansa 84,8%, ostrożnie 77,4%, kurs 1,19), oba przegrały — dwie obserwacje.",
    ],
    LIMIT: [
        "Replay 24 decyzji 31.10–07.11 z limitem 1 (TASK 014 §4.1): kupony 7 → 7, śr. szansa "
        "ostrożna 41,3% → 41,1%, dni z 2+ porażkami od jednej nogi 1 → 0.",
    ],
}
RISKS = {
    GATE: "Protokół v3 nie ma nietkniętego holdoutu — jedynym niezależnym sprawdzianem jest tryb "
    "cienia (≥ 300 wspólnych rozliczonych meczów). Akceptacja niczego nie aktywuje.",
    OPTION_D: "Węższy margines bezpieczeństwa: zapas granicy maleje z +10–15 pkt do +2–3 pkt. "
    "Więcej typów przekroczy próg 75% — także takich, które dziś są tuż pod nim. Granica wchodzi "
    "do progów nóg każdego profilu, więc mogą się zmienić także nogi innych kuponów dnia.",
    SAFE_SOLO: "Trafność ≠ zysk: przy kursie 1,15–1,25 wygrana daje 0,12–1,00 zł na 10 zł "
    "stawki, przegrana zabiera 10 zł. Próba (2 kupony) jest za mała na ocenę.",
    LIMIT: "Selekcja użyta już w N kuponach nie trafi do kolejnych profili — ich nogi mogą się "
    "zmienić (progi nie). Mniej wspólnych porażek, ale czasem niższa szansa kuponu.",
    CHALLENGERS: "Aktywacja przed zebraniem 300 wspólnych meczów nie jest możliwa — za mało "
    "dowodów, by odróżnić lepszy model od przypadku.",
    NATIONAL_1X2: "Po akceptacji 1X2 reprezentacji może pojawić się w puli kuponu. Błędne "
    "mapowanie, nieaktualny kurs albo przyszła zmiana modelu ponownie ją zablokują.",
}
CHANGES = {
    GATE: "Po akceptacji challenger v3 może zostać aktywowany — osobnym kliknięciem w Uczeniu AI "
    "i dopiero po 300 wspólnych rozliczonych meczach. Bez akceptacji v3 trenuje się i działa w "
    "cieniu, ale jego aktywacja jest zablokowana.",
    OPTION_D: "Szansa ostrożna podwójnej szansy Elo = 1 − górna granica wykluczonego wyniku 1X2 "
    "(jeden przedział, jak Dixon–Coles) zamiast sumy dolnych granic dwóch wyników — we wszystkich "
    "profilach. Progi profili bez zmian. Definicja zapisuje się w każdej propozycji; zmiana w "
    "ciągu dnia daje rewizję — kupon oficjalny dnia zostaje.",
    SAFE_SOLO: "Kreator buduje piąty kupon: jedna selekcja, ostrożnie ≥ 75%, kurs od 1,15, zysk "
    "netto po podatku > 0, walidacja predykcyjna — po czterech profilach (ich kupony się nie "
    "zmieniają).",
    LIMIT: "Ta sama selekcja najwyżej w N kuponach dnia (0 = bez limitu).",
    CHALLENGERS: "Stan dowodów challengerów w trybie cienia — decyzja o aktywacji pojawi się "
    "dopiero po progu.",
    NATIONAL_1X2: "Użycie wyłącznie prognoz 1X2 po 90 minutach dla wersji reprezentacyjnej, "
    "która spełnia bramki walidacji historycznej. Prognozy cieniowe pozostają poza kuponami.",
}
SOURCES = {"panel": "panel Decyzje do podjęcia", "settings": "Ustawienia"}
# TASK 016 §7: saving a decision that changes nothing adds no row — the panel says so
UNCHANGED = "bez zmian — nic nie zapisano"


# ---------------------------------------------------------------- the history


def record(
    store: Store,
    *,
    decision: str,
    previous,
    new,
    summary: str,
    now: datetime,
    source: str = "panel",
    payload: dict | None = None,
) -> str:
    """Append one decision of the USER (never overwritten; a withdrawal is a new row)."""
    if decision not in KEYS:
        raise ValueError(f"Nieznana decyzja: {decision}")
    decision_id = digest(["user_decision", decision, iso(now), canonical(previous), canonical(new)])
    store.insert(
        "user_decisions",
        id=decision_id,
        created_at=iso(now),
        decision=decision,
        actor="user",
        previous_json=canonical(previous),
        new_json=canonical(new),
        summary=summary,
        source=SOURCES.get(source, source),
        payload_json=canonical(payload or {}),
    )
    return decision_id


def unchanged(store: Store, decision: str, previous, new) -> bool:
    """The state stays as it was AND the decision is already taken (it has a row of the
    user): nothing to record. The first confirmation of a default still closes an open
    decision — that is a change of the decision's state (open → taken)."""
    return previous == new and last(store, decision) is not None


def history(store: Store, decision: str | None = None) -> list[dict]:
    """Every recorded decision, oldest first."""
    sql = "SELECT * FROM user_decisions"
    args: tuple = ()
    if decision is not None:
        sql += " WHERE decision=?"
        args = (decision,)
    rows = store.rows(sql + " ORDER BY created_at, rowid", args)
    for row in rows:
        row["previous"] = json.loads(row.pop("previous_json"))
        row["new"] = json.loads(row.pop("new_json"))
        row["payload"] = json.loads(row.pop("payload_json") or "{}")
    return rows


def last(store: Store, decision: str) -> dict | None:
    found = history(store, decision)
    return found[-1] if found else None


# ---------------------------------------------------------------- current states


def gate_value(store: Store) -> bool:
    return learning.gate_state(store)["accepted"]


def option_d_value(settings) -> str:
    return bound_of(getattr(settings, "elo_dc_bound", BOUND_SUM))


def safe_solo_value(settings) -> bool:
    return SAFE_PROFILE in (getattr(settings, "enabled_profiles", None) or [])


def policy_limit(paths: AppPaths) -> int:
    from sports_edge.services import probability_policy

    return int(probability_policy(paths).builder.get("max_profiles_per_selection", 0) or 0)


def limit_value(paths: AppPaths, settings) -> int:
    value = getattr(settings, "max_profiles_per_selection", None)
    return int(value) if value is not None else policy_limit(paths)


def _local(moment: str) -> str:
    return datetime.fromisoformat(moment).astimezone(WARSAW).strftime("%d.%m.%Y %H:%M")


def _day(day: str) -> str:
    return date.fromisoformat(day).strftime("%d.%m.%Y")


def _bool_text(value: bool) -> str:
    return "włączony" if value else "wyłączony"


def _limit_text(value: int) -> str:
    return "bez limitu" if not value else f"najwyżej w {value} kuponach dnia"


# ---------------------------------------------------------------- the user's actions


def decide_gate(
    store: Store, paths: AppPaths, *, accept: bool, now: datetime, source: str = "panel"
) -> str | None:
    """The USER accepts the v3 gate, keeps it blocked or withdraws the acceptance (the gate's
    own model events of TASK 013 + a row of this history). Nothing is activated. ``None``:
    nothing changed and nothing was recorded (an accepted gate is a taken decision even when
    it was accepted in Uczenie AI, before this history existed)."""
    from sports_edge import rolling

    previous = gate_value(store)
    if accept == previous and (accept or last(store, GATE) is not None):
        return None
    n = rolling.load_policy(paths.config).shadow_matches
    if accept and not previous:
        learning.accept_gate(store, now=now, shadow_matches=n, note=SOURCES.get(source, source))
    elif not accept and previous:
        learning.withdraw_gate(store, now=now, note=SOURCES.get(source, source))
    if accept:
        summary = "Akceptacja bramki v3 (aktywacja v3 możliwa po 300 wspólnych meczach)"
    elif previous:
        summary = "Wycofanie akceptacji bramki v3 — aktywacja v3 znów zablokowana"
    else:
        summary = "Bramka v3 pozostaje niezaakceptowana — aktywacja v3 zablokowana"
    return record(
        store,
        decision=GATE,
        previous={"accepted": previous},
        new={"accepted": accept},
        summary=summary,
        now=now,
        source=source,
    )


def _save(paths: AppPaths, settings: AppSettings) -> None:
    save_settings(paths, settings)


def decide_option_d(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    *,
    value: str,
    now: datetime,
    source: str = "panel",
) -> str | None:
    """The USER chooses the definition of the cautious Elo double-chance bound (``None``:
    unchanged and already decided — nothing recorded)."""
    previous, value = option_d_value(settings), bound_of(value)
    if unchanged(store, OPTION_D, previous, value):
        return None
    _save(paths, replace(settings, elo_dc_bound=value))
    return record(
        store,
        decision=OPTION_D,
        previous={"elo_dc_bound": previous},
        new={"elo_dc_bound": value},
        summary=f"Granica „ostrożnie” podwójnej szansy Elo: {BOUND_LABELS[value]}"
        + (" (potwierdzenie dotychczasowego stanu)" if value == previous else ""),
        now=now,
        source=source,
    )


def decide_safe_solo(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    *,
    enabled: bool,
    now: datetime,
    source: str = "panel",
) -> str | None:
    """The USER switches the optional profile PEWNY SOLO on or off (``None``: unchanged and
    already decided — nothing recorded)."""
    previous = safe_solo_value(settings)
    if unchanged(store, SAFE_SOLO, previous, bool(enabled)):
        return None
    profiles = [p for p in (settings.enabled_profiles or []) if p != SAFE_PROFILE]
    if enabled:
        profiles.append(SAFE_PROFILE)
    _save(paths, replace(settings, enabled_profiles=profiles))
    return record(
        store,
        decision=SAFE_SOLO,
        previous={"enabled": previous},
        new={"enabled": enabled},
        summary=f"Profil PEWNY SOLO: {_bool_text(enabled)}"
        + (" (potwierdzenie dotychczasowego stanu)" if enabled == previous else ""),
        now=now,
        source=source,
    )


def decide_limit(
    store: Store,
    paths: AppPaths,
    settings: AppSettings,
    *,
    value: int,
    now: datetime,
    source: str = "panel",
) -> str | None:
    """The USER sets the limit of one selection in the day's coupons (0 = off; ``None``:
    unchanged and already decided — nothing recorded)."""
    previous, value = limit_value(paths, settings), int(value)
    if unchanged(store, LIMIT, previous, value):
        return None
    stored = value if value != policy_limit(paths) else None
    _save(paths, replace(settings, max_profiles_per_selection=stored))
    return record(
        store,
        decision=LIMIT,
        previous={"max_profiles_per_selection": previous},
        new={"max_profiles_per_selection": value},
        summary=f"Limit różnicowania: {_limit_text(value)}"
        + (" (potwierdzenie dotychczasowego stanu)" if value == previous else ""),
        now=now,
        source=source,
    )


def decide_national_1x2(
    store: Store,
    paths: AppPaths,
    *,
    accept: bool,
    now: datetime,
    source: str = "panel",
) -> str:
    """Append the user's decision for national 1X2 after 90 minutes and one exact version."""
    from sports_edge import international

    previous_row = last(store, NATIONAL_1X2)
    previous = (previous_row.get("new") or {}) if previous_row else {"accepted": False}
    report = international.latest(store)
    status = international.market_status(report, "national")["1x2"]
    if accept:
        if not report or not status["validated"] or not status.get("best"):
            raise ValueError("Akceptacja niemożliwa: 1X2 reprezentacji nie spełnia walidacji")
        registered = international.register_national_model_version(store, paths, now=now)
        new = {
            "accepted": True,
            "competition": "national",
            "market": "1x2",
            "period": "FT_90",
            "experiment_id": report["experiment_id"],
            "variant": status["best"],
            "model_version_id": registered["model_version_id"],
        }
        summary = (
            "Akceptacja national 1X2 po 90 minutach — "
            f"wersja {new['model_version_id']} ({new['variant']}); osobna bramka kursu i kuponu"
        )
    else:
        new = {
            "accepted": False,
            "competition": "national",
            "market": "1x2",
            "period": "FT_90",
            "experiment_id": (report or {}).get("experiment_id"),
            "variant": status.get("best"),
            "model_version_id": previous.get("model_version_id"),
        }
        summary = "Wycofanie zgody na national 1X2 po 90 minutach — nowe użycie zablokowane"
    return record(
        store,
        decision=NATIONAL_1X2,
        previous={
            "accepted": bool(previous.get("accepted")),
            "experiment_id": previous.get("experiment_id"),
            "variant": previous.get("variant"),
            "model_version_id": previous.get("model_version_id"),
        },
        new=new,
        summary=summary,
        now=now,
        source=source,
        payload={"competition": "national", "market": "1x2", "period": "FT_90"},
    )


def log_settings_change(
    store: Store, paths: AppPaths, before: AppSettings, after: AppSettings, *, now: datetime
) -> list[str]:
    """Ustawienia → "Zapisz ustawienia" (a user action): a decision row for each of the three
    decisions whose setting really changed (option D, PEWNY SOLO, the limit)."""
    out = []
    if option_d_value(before) != option_d_value(after):
        out.append(
            record(
                store,
                decision=OPTION_D,
                previous={"elo_dc_bound": option_d_value(before)},
                new={"elo_dc_bound": option_d_value(after)},
                summary="Granica „ostrożnie” podwójnej szansy Elo: "
                + BOUND_LABELS[option_d_value(after)],
                now=now,
                source="settings",
            )
        )
    if safe_solo_value(before) != safe_solo_value(after):
        out.append(
            record(
                store,
                decision=SAFE_SOLO,
                previous={"enabled": safe_solo_value(before)},
                new={"enabled": safe_solo_value(after)},
                summary=f"Profil PEWNY SOLO: {_bool_text(safe_solo_value(after))}",
                now=now,
                source="settings",
            )
        )
    if limit_value(paths, before) != limit_value(paths, after):
        out.append(
            record(
                store,
                decision=LIMIT,
                previous={"max_profiles_per_selection": limit_value(paths, before)},
                new={"max_profiles_per_selection": limit_value(paths, after)},
                summary=f"Limit różnicowania: {_limit_text(limit_value(paths, after))}",
                now=now,
                source="settings",
            )
        )
    return out


# ---------------------------------------------------------------- live evidence (read only)


def option_d_today(store: Store, paths: AppPaths, settings, day: str, *, now: datetime) -> dict:
    """Option D on the offer of ``day``: Elo double-chance selections with a cautious chance
    at or above the WYSOKA TRAFNOŚĆ leg threshold under both definitions (all, eligible for
    the builder, validated and eligible). The same engine as the coupons; nothing is saved."""
    from sports_edge.analysis.coupon_builder import VALIDATED, eligibility
    from sports_edge.services import day_candidates_at, probability_policy

    policy = probability_policy(paths)
    floor = float(policy.profiles[HIGH_HIT]["min_leg_probability"])
    max_odds = float(policy.builder["max_odds_per_leg"])
    out = {"day": day, "floor": floor}
    for bound in (BOUND_SUM, BOUND_SINGLE):
        chosen = replace(settings, elo_dc_bound=bound)
        candidates, _ = day_candidates_at(store, paths, chosen, day, now=now)
        elo = [
            x
            for x in candidates
            if x.market_key == "double_chance"
            and (x.model or "").startswith("elo_ordered_draw")
            and x.p_low is not None
            and x.p_low >= floor
        ]
        eligible = [x for x in elo if eligibility(x, max_odds) is None]
        out[bound] = {
            "all": len(elo),
            "eligible": len(eligible),
            "validated": sum(x.predictive_status == VALIDATED for x in eligible),
        }
    return out


def _gate_item(store: Store, compared: dict) -> dict:
    state = learning.gate_state(store)
    mine = last(store, GATE)
    accepted = state["accepted"]
    evidence = []
    for scope, entries in compared.items():
        for key, entry in entries.items():
            challenger = entry.get("challenger") or {}
            if challenger.get("protocol") != "v3":
                continue
            evidence.append(
                f"{SCOPE_NAMES.get(scope, scope)}, {FAMILY_NAMES[key]}: challenger v3 w cieniu — "
                f"{entry['state_label']}"
                + (f"; {entry['estimate']['label']}" if entry.get("estimate") else "")
            )
    if not evidence:
        evidence.append("Brak challengerów v3 w trybie cienia (pierwszy przegląd je utworzy).")
    decided = state["decision"] is not None or mine is not None
    when = (mine or state["decision"] or {}).get("created_at")
    return {
        "key": GATE,
        "title": TITLES[GATE],
        "value": accepted,
        "state": ("zaakceptowana" if accepted else "niezaakceptowana — aktywacja v3 zablokowana")
        + (f" (decyzja z {_local(when)})" if when else " (brak decyzji)"),
        "open": not decided,
        "changes": CHANGES[GATE],
        "evidence": evidence + REPORTED[GATE],
        "risk": RISKS[GATE],
    }


def _option_d_item(store: Store, settings, live: dict | None) -> dict:
    value = option_d_value(settings)
    mine = last(store, OPTION_D)
    evidence = []
    if live:
        a, b = live[BOUND_SUM], live[BOUND_SINGLE]
        evidence.append(
            f"Oferta {_day(live['day'])} (teraz): typy podwójnej szansy Elo z ostrożną szansą ≥ "
            f"{live['floor']:.0%} — dzisiejsza definicja {a['all']}, opcja D {b['all']}; "
            f"kwalifikujące się do kreatora {a['eligible']} → {b['eligible']} (zwalidowane "
            f"{a['validated']} → {b['validated']})."
        )
    else:
        evidence.append("Oferta dnia: brak danych (brak oferty STS z prognozami Elo).")
    return {
        "key": OPTION_D,
        "title": TITLES[OPTION_D],
        "value": value,
        "state": BOUND_LABELS[value] + ("" if mine else " — domyślnie (brak decyzji)"),
        "open": mine is None,
        "changes": CHANGES[OPTION_D],
        "evidence": evidence + REPORTED[OPTION_D],
        "risk": RISKS[OPTION_D],
    }


def _safe_solo_item(store: Store, settings) -> dict:
    from sports_edge.performance import expected_vs_actual

    value = safe_solo_value(settings)
    mine = last(store, SAFE_SOLO)
    found = expected_vs_actual(store)
    rows = [r for r in found["rows"] if r["label"] == "PEWNY SOLO"]
    if rows:
        r = rows[0]
        live = (
            f"Rozliczone kupony PEWNY SOLO: {r['n']}, trafione {r['hits']:.0f}, oczekiwane "
            f"{r['expected']:.1f} — {r['sentence']}."
        )
    else:
        live = "Rozliczone kupony PEWNY SOLO: brak danych (profil nie zapisał jeszcze kuponu)."
    return {
        "key": SAFE_SOLO,
        "title": TITLES[SAFE_SOLO],
        "value": value,
        "state": _bool_text(value) + ("" if mine else " — domyślnie (brak decyzji)"),
        "open": mine is None,
        "changes": CHANGES[SAFE_SOLO],
        "evidence": [live, *REPORTED[SAFE_SOLO]],
        "risk": RISKS[SAFE_SOLO],
    }


def _limit_item(store: Store, paths: AppPaths, settings) -> dict:
    from sports_edge.performance import shared_losses

    value = limit_value(paths, settings)
    mine = last(store, LIMIT)
    found = shared_losses(store)
    if found["settled_days"]:
        live = (
            f"Rozliczone dni: {found['settled_days']}; dni, w których jedna przegrana noga była w "
            f"2+ kuponach: {found['days_one_leg_2plus']} (kupony przegrane przez wspólną nogę: "
            f"{found['coupons_lost_by_shared_leg']})."
        )
    else:
        live = "Wspólne porażki: brak danych (brak rozliczonych kuponów)."
    return {
        "key": LIMIT,
        "title": TITLES[LIMIT],
        "value": value,
        "state": _limit_text(value) + ("" if mine else " — domyślnie (brak decyzji)"),
        "open": mine is None,
        "changes": CHANGES[LIMIT],
        "evidence": [live, *REPORTED[LIMIT]],
        "risk": RISKS[LIMIT],
    }


def _challengers_item(compared: dict) -> dict:
    evidence, sufficient = [], 0
    for scope, entries in compared.items():
        for key, entry in entries.items():
            if not entry.get("challenger"):
                evidence.append(
                    f"{SCOPE_NAMES.get(scope, scope)}, {FAMILY_NAMES[key]}: brak challengera"
                )
                continue
            sufficient += entry["state"] == "sufficient"
            protocol = entry["challenger"].get("protocol", "—")
            evidence.append(
                f"{SCOPE_NAMES.get(scope, scope)}, {FAMILY_NAMES[key]} (challenger {protocol}): "
                f"{entry['state_label']}"
                + (f"; {entry['estimate']['label']}" if entry.get("estimate") else "")
            )
    if not evidence:
        evidence.append("Brak modeli i challengerów (brak danych).")
    return {
        "key": CHALLENGERS,
        "title": TITLES[CHALLENGERS],
        "value": sufficient,
        "state": f"{sufficient} z dowodami wystarczającymi do decyzji"
        if sufficient
        else "czekają na dowody — decyzja dopiero po progu",
        "open": sufficient > 0,
        "changes": CHANGES[CHALLENGERS],
        "evidence": evidence,
        "risk": RISKS[CHALLENGERS],
    }


def _national_1x2_item(store: Store, paths: AppPaths, *, now: datetime) -> dict:
    from sports_edge import international

    report = international.latest(store)
    status = international.market_status(report, "national")["1x2"]
    registry = international.national_registry_status(store, paths)
    mine = last(store, NATIONAL_1X2)
    previous = mine["new"] if mine else {}
    experiment_id = (report or {}).get("experiment_id")
    variant = status.get("best")
    current = bool(
        status["validated"]
        and experiment_id
        and previous.get("accepted")
        and previous.get("experiment_id") == experiment_id
        and previous.get("variant") == variant
        and previous.get("model_version_id")
        and international.active_national_version_id(store) == previous.get("model_version_id")
    )
    if not report:
        state, opened = "brak eksperymentu — aktywacja zablokowana", False
        evidence = ["Uruchom eksperymenty międzynarodowe po imporcie danych."]
    elif not status["validated"]:
        state, opened = "model niezwalidowany — aktywacja zablokowana", False
        entry = report["national"].get("markets", {}).get("1x2", {})
        evidence = [status["verdict"]]
        for criterion in entry.get("decision", {}).get("criteria", []):
            evidence.append(
                f"{criterion['name']}: {'spełnione' if criterion['passed'] else 'niespełnione'}"
                + (f" ({criterion['value']})" if criterion.get("value") is not None else "")
            )
    elif registry["status"] != "validated":
        opened = False
        state = "rejestr modelu nie spełnia polityki — aktywacja zablokowana"
        evidence = [status["verdict"], *registry["reasons"]]
    else:
        opened = True
        state = "aktywne po Twojej decyzji" if current else "czeka na Twoją decyzję"
        evidence = [status["verdict"], f"Wariant wybrany w oknie selekcji: {variant}."]
        if previous.get("accepted") and not current:
            evidence.append(
                "Poprzednia decyzja dotyczy innej wersji eksperymentu i nie jest użyta."
            )
    if report:
        block = report["national"].get("markets", {}).get("1x2", {})
        selection = (block.get("windows") or {}).get("selection") or {}
        chosen = selection.get(variant or "", {})
        evidence.append(
            f"Okno selekcji: {chosen.get('n', 0)} meczów; ECE {chosen.get('ece', 'brak danych')}."
        )
        from sports_edge.international import national_shadow_evaluation

        shadow = national_shadow_evaluation(store, now=now)
        variant_shadow = shadow["by_variant"].get(variant or "", {})
        evidence.append(
            f"Cień prospektywny {variant or '—'}: {variant_shadow.get('n', 0)} rozliczonych "
            "meczów; dane nie aktywują modelu automatycznie."
        )
    return {
        "key": NATIONAL_1X2,
        "title": TITLES[NATIONAL_1X2],
        "value": current,
        "state": state,
        "open": opened,
        "changes": CHANGES[NATIONAL_1X2],
        "evidence": evidence,
        "risk": RISKS[NATIONAL_1X2],
        "validated": bool(status["validated"] and registry["status"] == "validated"),
    }


def panel(
    store: Store,
    paths: AppPaths,
    settings,
    *,
    now: datetime,
    compared: dict | None = None,
    live_option_d: dict | None = None,
) -> list[dict]:
    """The six decisions with their state, evidence and risk — read only. ``compared``:
    scope → ``retraining.comparison`` (computed when not given)."""
    from sports_edge import retraining
    from sports_edge.services import MODEL_SCOPES

    if compared is None:
        compared = {scope: retraining.comparison(store, scope, now=now) for scope in MODEL_SCOPES}
    return [
        _gate_item(store, compared),
        _option_d_item(store, settings, live_option_d),
        _safe_solo_item(store, settings),
        _limit_item(store, paths, settings),
        _challengers_item(compared),
        _national_1x2_item(store, paths, now=now),
    ]


def open_count(items: list[dict]) -> int:
    return sum(1 for item in items if item["open"])
