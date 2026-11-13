"""Results typed in by the user and coupons waiting for a result (TASK 019 §1.3, §1.5).

A manual result is the emergency exit when no free provider publishes a match (in November
2026: the national teams — see the TASK 019 report). Rules:

* the 90-minute score and a SOURCE (text or link) are mandatory; the entry is accepted only
  after the typical duration of the match (``repository.EVENT_DURATION``) — a result is never
  typed in for a match that cannot be over;
* append-only: a ``results`` row with source ``manual_entry`` (it settles coupons and
  forecasts exactly like a provider's result) and — when the competition and both teams are
  known to the results history — a ``historical_results`` row of provider ``manual_entry``
  with ``available_at`` = the moment of the entry, so it can never leak into a forecast made
  before it was typed in;
* any provider's later result takes precedence (lowest place in ``SOURCE_PRIORITY``); a
  different score creates the alert ``result_mismatch`` (``paper.import_source_results``);
* statistics count it apart: the column "Źródło wyniku" (``result_source_label``).
"""

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sports_edge.datasources.competitions import COMPETITIONS, by_category, for_sts_league
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.paper import (
    MANUAL_SOURCE,
    latest_result,
    record_result,
    resolve,
    source_context,
    source_result,
    unresolved_reason,
)
from sports_edge.repository import EVENT_DURATION
from sports_edge.results_engine import SOURCE_LABELS, effective_results, known_names
from sports_edge.storage import Store

WARSAW = ZoneInfo("Europe/Warsaw")
PENDING_AFTER = timedelta(hours=24)  # a coupon unsettled this long after its last match
MANUAL_LABEL = "ręczny (ze źródłem)"
# the job whose run brings the results of each kind of competition
RESULT_JOBS = {
    "national": ("auto:national_results", "international_results"),
    "european": ("auto:uefa_results", "international_results"),
    "domestic": ("auto:results", "results_update"),
}


def finished_by(event: dict) -> datetime | None:
    """The earliest moment the match can be over (start + typical duration)."""
    if not event.get("start_at"):
        return None
    start = datetime.fromisoformat(event["start_at"])
    return start + EVENT_DURATION.get(event.get("sport") or "", timedelta(hours=3))


def _validate(home: int, away: int, source: str) -> str:
    for value in (home, away):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 99:
            raise ValueError("Wynik: liczby całkowite od 0 do 99")
    text = (source or "").strip()
    if len(text) < 3:
        raise ValueError("Podaj źródło wyniku (nazwa strony lub link) — bez źródła nie zapisuję")
    return text[:300]


def _history_names(store: Store, code: str, event: dict, *, now, names, aliases, identities):
    """Names of both teams in the results history of the competition's pool, or the reason
    why the entry cannot feed the models."""
    from sports_edge.matching import match_participant

    league = COMPETITIONS[code]
    pool = by_category(league.category) if league.category != "domestic" else (code,)
    rows = effective_results(
        store, sport=league.sport, until=now, competitions=pool, identities=identities
    )
    known = known_names(rows, league.sport, names)
    found = {
        side: match_participant(event[side] or "", league.sport, known, names, aliases)
        for side in ("home", "away")
    }
    missing = [event[s] for s, m in found.items() if m.status not in {"exact", "alias"}]
    if missing:
        return None, "model nie dostanie tego wyniku: nazwa bez aliasu — " + ", ".join(missing)
    return (found["home"].target, found["away"].target), None


def _history_row(
    store: Store,
    *,
    code: str,
    match_date: str,
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    source: str,
    now: datetime,
    event_id: str | None,
) -> str:
    """One append-only ``historical_results`` row of provider ``manual_entry``."""
    league = COMPETITIONS[code]
    entry = {
        "competition": code,
        "date": match_date,
        "home": home,
        "away": away,
        "score": [home_score, away_score],
        "source": source,
        "entered_at": iso(now),
        "event_id": event_id,
    }
    body = canonical(entry)
    import_id = digest(["data_import", "manual_entry", body])
    year = int(match_date[:4])
    season = (
        str(year)
        if league.nations
        else f"{year if int(match_date[5:7]) >= 7 else year - 1}-"
        f"{str((year if int(match_date[5:7]) >= 7 else year - 1) + 1)[2:]}"
    )
    details = {
        "manual": True,
        "source_text": source,
        "score_basis": "90 minut (wpis ręczny)",
        "regulation_certain": True,
        "event_id": event_id,
    }
    if league.nations:  # the venue is not typed in: no home advantage (TASK 017/018 rule)
        details |= {"home_country": home, "away_country": away, "venue_country": None}
    row_id = digest(["hist", MANUAL_SOURCE, code, match_date, home, away, home_score, away_score])
    with store.transaction():
        store.insert(
            "data_imports",
            id=import_id,
            source_name=f"Wpis ręczny: {source}"[:200],
            license_note="wynik wpisany ręcznie przez użytkownika z podanego źródła",
            file_sha256=digest([body]),
            file_name=None,
            kind="results",
            rows=1,
            ingested_at=iso(now),
        )
        store.insert(
            "historical_results",
            id=row_id,
            import_id=import_id,
            sport=league.sport,
            competition=code,
            season=season,
            match_date=match_date,
            home=home,
            away=away,
            home_score=home_score,
            away_score=away_score,
            available_at=iso(now),
            available_at_basis="manual_entry",
            details_json=canonical(details),
            provider=MANUAL_SOURCE,
            observed_at=iso(now),
        )
    return row_id


def record_manual(
    store: Store,
    *,
    event_id: str,
    home_score: int,
    away_score: int,
    source: str,
    entered_on: date,
    now: datetime,
    normalizer,
    team_aliases,
    identities=None,
) -> dict:
    """The user's 90-minute result of an STS event (settles coupons and forecasts)."""
    from sports_edge.matching import alias_map

    event = store.get("events", event_id)
    if event is None:
        raise ValueError("Nieznane wydarzenie")
    text = _validate(home_score, away_score, source)
    over = finished_by(event)
    if over is None:
        raise ValueError("Wydarzenie bez godziny startu — nie można potwierdzić, że się odbyło")
    if now < over:
        raise ValueError(
            "Mecz mógł się jeszcze nie zakończyć — wynik można wpisać po "
            + over.astimezone(WARSAW).strftime("%d.%m %H:%M")
        )
    start_day = datetime.fromisoformat(event["start_at"]).astimezone(WARSAW).date()
    if entered_on < start_day or entered_on > now.astimezone(WARSAW).date():
        raise ValueError("Data wpisu: od dnia meczu do dziś")
    previous = latest_result(store, event_id)
    if previous is not None and previous["source"] != MANUAL_SOURCE:
        label = SOURCE_LABELS.get(previous["source"], previous["source"])
        raise ValueError(
            f"Wynik od dostawcy już jest ({label}): {previous['home_score']}:"
            f"{previous['away_score']} — wpis ręczny nie jest potrzebny"
        )
    if (
        previous is not None
        and (previous["home_score"], previous["away_score"]) == (home_score, away_score)
        and json.loads(previous["details_json"] or "{}").get("source_text") == text
    ):
        return {"result_id": previous["id"], "duplicate": True, "history": None}
    result_id = record_result(
        store,
        event_id=event_id,
        status="completed",
        now=now,
        home_score=home_score,
        away_score=away_score,
        details={
            "manual": True,
            "source": SOURCE_LABELS[MANUAL_SOURCE],
            "source_text": text,
            "entered_on": entered_on.isoformat(),
            "score_basis": "90 minut",
        },
        source=MANUAL_SOURCE,
        verified=True,
    )
    history, why = None, None
    league = for_sts_league(event["league"] or "", event["sport"])
    if league is None:
        why = "rozgrywki bez historii wyników — wynik rozlicza kupony, model go nie zna"
    else:
        names, why = _history_names(
            store,
            league.code,
            event,
            now=now,
            names=normalizer,
            aliases=alias_map(store, team_aliases),
            identities=identities,
        )
        if names is not None:
            history = _history_row(
                store,
                code=league.code,
                match_date=datetime.fromisoformat(event["start_at"])
                .astimezone(ZoneInfo(league.zone))
                .date()
                .isoformat(),
                home=names[0],
                away=names[1],
                home_score=home_score,
                away_score=away_score,
                source=text,
                now=now,
                event_id=event_id,
            )
    return {"result_id": result_id, "duplicate": False, "history": history, "history_note": why}


def record_manual_history(
    store: Store,
    *,
    competition: str,
    match_date: date,
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    source: str,
    now: datetime,
) -> str:
    """A national-team match outside the STS archive (e.g. September–October 2026) typed in
    with its source: teams are chosen from the results history (never free text)."""
    league = COMPETITIONS.get(competition)
    if league is None or not league.nations:
        raise ValueError("Ręczny wpis spoza oferty: tylko rozgrywki reprezentacji")
    text = _validate(home_score, away_score, source)
    if home == away:
        raise ValueError("Wybierz dwie różne drużyny")
    if match_date >= now.astimezone(WARSAW).date() + timedelta(days=1):
        raise ValueError("Mecz z przyszłości — wynik można wpisać po meczu")
    known = {
        r[side]
        for r in effective_results(
            store, sport="football", until=now, competitions=by_category("national")
        )
        for side in ("home", "away")
    }
    if home not in known or away not in known:
        raise ValueError("Drużyny muszą pochodzić z historii wyników reprezentacji")
    return _history_row(
        store,
        code=competition,
        match_date=match_date.isoformat(),
        home=home,
        away=away,
        home_score=home_score,
        away_score=away_score,
        source=text,
        now=now,
        event_id=None,
    )


def result_source_label(source: str | None) -> str:
    """The column "Źródło wyniku": a provider or the user's entry."""
    if not source:
        return "—"
    if source == MANUAL_SOURCE:
        return MANUAL_LABEL
    return SOURCE_LABELS.get(source, source)


# ---------------------------------------------------------------- waiting for a result


def last_attempt(store: Store, category: str) -> dict | None:
    """The newest run of the job that brings results of this kind of competition."""
    jobs = RESULT_JOBS.get(category, RESULT_JOBS["domestic"])
    rows = store.rows(
        f"""SELECT job, status, started_at, reason FROM job_runs
        WHERE job IN ({",".join("?" for _ in jobs)}) ORDER BY started_at DESC LIMIT 1""",
        jobs,
    )
    return rows[0] if rows else None


def _expected(code: str | None, store: Store, *, now: datetime) -> str:
    """Which source the result is expected from — and whether it publishes the season."""
    if code is None:
        return "brak niezależnego źródła tych rozgrywek — wpisz wynik ręcznie ze źródłem"
    league = COMPETITIONS[code]
    labels = []
    for provider, _ref in league.sources:
        family = {"football_data": "football_data_org"}.get(provider, provider)
        labels.append(SOURCE_LABELS.get(family, family))
    newest = store.scalar(
        "SELECT MAX(match_date) FROM historical_results WHERE competition=? AND provider!=?",
        (code, MANUAL_SOURCE),
    )
    text = "z: " + ", ".join(dict.fromkeys(labels))
    if newest:
        text += f" (najnowszy wynik tych rozgrywek w źródle: {newest})"
    return text


def pending_coupons(
    store: Store,
    *,
    now: datetime,
    normalizer,
    team_aliases,
    identities=None,
    after: timedelta = PENDING_AFTER,
) -> list[dict]:
    """Counted coupons still unsettled ``after`` the end of their last match, each with the
    missing legs, the expected result and source, the reason and the last attempt."""
    from sports_edge.coupon_store import counted_sql

    coupons = store.rows(
        f"""SELECT c.* FROM coupons c WHERE {counted_sql("c")}
        AND NOT EXISTS (SELECT 1 FROM settlements s WHERE s.coupon_id=c.id)
        AND c.first_start_at < ? ORDER BY c.first_start_at""",
        (iso(now - after),),
    )
    if not coupons:
        return []
    legs_of: dict[str, list[dict]] = {}
    for coupon in coupons:
        legs_of[coupon["id"]] = store.rows(
            """SELECT e.*, m.market_key, m.period, m.overtime, m.line, s.key selection,
            m.id market_id FROM coupon_legs l JOIN selections s ON s.id=l.selection_id
            JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id
            WHERE l.coupon_id=? ORDER BY e.start_at""",
            (coupon["id"],),
        )
    events = {leg["id"]: leg for legs in legs_of.values() for leg in legs}
    context = source_context(
        store,
        list(events.values()),
        now=now,
        normalizer=normalizer,
        team_aliases=team_aliases,
        identities=identities,
    )
    out = []
    for coupon in coupons:
        legs = legs_of[coupon["id"]]
        ends = [finished_by(leg) for leg in legs]
        if any(e is None for e in ends) or max(ends) + after > now:
            continue
        missing = []
        for leg in legs:
            result = latest_result(store, leg["id"])
            if result is not None and resolve(leg, leg["selection"], result) is not None:
                continue
            code = context["league_for"].get((leg["league"], leg["sport"]))
            category = COMPETITIONS[code].category if code else "domestic"
            if result is None:
                _match, reason = source_result(leg, context)
                reason = reason or "wynik jest w źródle — zostanie przeniesiony przy rozliczeniu"
            else:
                reason = unresolved_reason(leg, result)
            attempt = last_attempt(store, category)
            missing.append(
                {
                    "event_id": leg["id"],
                    "event": f"{leg['home']} - {leg['away']}",
                    "start_at": leg["start_at"],
                    "league": leg["league"],
                    "expected": "wynik po 90 minutach"
                    if leg["period"] == "FT"
                    else "wynik do przerwy",
                    "source": _expected(code, store, now=now),
                    "reason": reason,
                    "last_attempt": attempt["started_at"] if attempt else None,
                    "last_status": attempt["status"] if attempt else None,
                    "manual_possible": result is None,
                }
            )
        if not missing:
            continue
        payload = json.loads(coupon["proposal_json"] or "{}")
        out.append(
            {
                "coupon_id": coupon["id"],
                "label": payload.get("label") or coupon["profile"] or coupon["kind"],
                "first_start_at": coupon["first_start_at"],
                "last_end": max(ends),
                "legs": len(legs),
                "missing": missing,
                "hours_waiting": round((now - max(ends)).total_seconds() / 3600, 1),
            }
        )
    return out


def manual_candidates(store: Store, *, now: datetime, days: int = 14) -> list[dict]:
    """STS events that are over, have no result yet and are on a coupon or in a modelled
    competition — the list of the form "Wpisz wynik ręcznie (ze źródłem)"."""
    rows = store.rows(
        """SELECT e.* FROM events e WHERE e.bookmaker_id='sts' AND e.start_at IS NOT NULL
        AND e.start_at < ? AND e.start_at >= ?
        AND NOT EXISTS (SELECT 1 FROM results r WHERE r.event_id=e.id AND r.source!=?)
        ORDER BY e.start_at DESC""",
        (iso(now), iso(now - timedelta(days=days)), MANUAL_SOURCE),
    )
    on_coupons = {
        r["event_id"]
        for r in store.rows(
            """SELECT DISTINCT m.event_id FROM coupon_legs l JOIN selections s
            ON s.id=l.selection_id JOIN markets m ON m.id=s.market_id"""
        )
    }
    out = []
    for event in rows:
        over = finished_by(event)
        if over is None or over > now:
            continue
        if event["id"] not in on_coupons and for_sts_league(event["league"] or "") is None:
            continue
        out.append({**event, "on_coupon": event["id"] in on_coupons})
    out.sort(key=lambda e: (not e["on_coupon"], e["start_at"]), reverse=False)
    return out


def coupon_result_source(store: Store, coupon_id: str) -> str:
    """The column "Źródło wyniku" of a coupon: the provider, the user's entry or both."""
    sources = {
        r["source"]
        for r in store.rows(
            """SELECT (SELECT r.source FROM results r WHERE r.event_id=m.event_id
            ORDER BY r.recorded_at DESC, r.id DESC LIMIT 1) source FROM coupon_legs l
            JOIN selections s ON s.id=l.selection_id JOIN markets m ON m.id=s.market_id
            WHERE l.coupon_id=?""",
            (coupon_id,),
        )
        if r["source"]
    }
    if not sources:
        return "—"
    if sources == {MANUAL_SOURCE}:
        return MANUAL_LABEL
    if MANUAL_SOURCE in sources:
        return "dostawca + ręczny"
    return "dostawca"


def manual_stats(store: Store) -> dict:
    """How many results, settled forecasts and settled coupons come from the user's entry."""
    results = store.scalar(
        "SELECT COUNT(DISTINCT event_id) FROM results WHERE source=?", (MANUAL_SOURCE,)
    )
    forecasts = store.scalar(
        """SELECT COUNT(*) FROM prediction_settlements p WHERE p.source=? AND p.revision=(
        SELECT MAX(revision) FROM prediction_settlements q
        WHERE q.prediction_id=p.prediction_id)""",
        (MANUAL_SOURCE,),
    )
    return {"results": results, "forecasts": forecasts}


def today_warsaw(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(WARSAW).date()
