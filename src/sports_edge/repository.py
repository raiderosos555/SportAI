"""Offer imports into the append-only store and the read models used by the application."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.offers.catalog import MAPPING_VERSION
from sports_edge.offers.model import OfferRow, ParseResult
from sports_edge.offers.text import fold
from sports_edge.storage import Store

BOOKMAKER = "sts"  # TASK 003: the only operator in the active flow
BOOKMAKERS = {BOOKMAKER: "STS"}

SOURCES = {
    "sts_pdf_manual": (
        "STS — ręczny import oferty PDF",
        "pdf_manual",
        "https://www.sts.pl/oferta-pdf",
    ),
    "sts_pdf_official": (
        "STS — oficjalny link PDF (retail-api.sts.pl/printed-offer)",
        "pdf_download",
        "https://www.sts.pl/oferta-pdf",
    ),
    "fixture": ("Fixture testowy / DEMO — dane syntetyczne", "synthetic", "none"),
}


def ensure_reference_data(store: Store) -> None:
    with store.transaction():
        for key, name in BOOKMAKERS.items():
            store.insert("bookmakers", id=key, name=name)
        for key, (name, kind, terms) in SOURCES.items():
            store.insert(
                "sources",
                id=key,
                name=name,
                kind=kind,
                terms_ref=terms,
                retention="hash+parsed rows; raw copy only when enabled in settings",
                checked_on="2026-10-31",
            )


@dataclass(frozen=True)
class ImportOutcome:
    import_id: str
    duplicate: bool
    summary: dict


def event_id(row: OfferRow) -> str:
    who = [fold(p) for p in row.participants] or [fold(row.event_name_raw)]
    when = iso(row.start_local) if row.start_local else str(row.offer_date)
    return digest(["event", row.bookmaker, row.sport, fold(row.league_raw), who, when])


def market_id(row: OfferRow) -> str:
    overtime = "na" if row.includes_overtime is None else str(row.includes_overtime).lower()
    return digest(
        ["market", event_id(row), row.market_key, row.period, overtime, row.line, row.market_raw]
    )


def import_offer(
    store: Store,
    result: ParseResult,
    *,
    sha256: str,
    file_name: str | None,
    source_id: str,
    data_kind: str,
    ingested_at: datetime,
    raw_path: str | None = None,
    fetched_at: datetime | None = None,
    source_url: str | None = None,
) -> ImportOutcome:
    """Idempotent: the same file parsed by the same parser version is imported once.

    Only STS offers enter the active store; anything else is refused before writing.
    """
    if result.bookmaker != BOOKMAKER or any(r.bookmaker != BOOKMAKER for r in result.rows):
        raise ValueError("Aplikacja przyjmuje wyłącznie ofertę STS")
    if source_id not in SOURCES:
        raise ValueError("Nieaktywne źródło danych")
    parser = f"{result.parser}@{result.parser_version}"
    import_key = digest(["import", sha256, parser, data_kind])
    summary = result.summary()
    if store.get("imports", import_key) is not None:
        return ImportOutcome(import_key, True, summary)
    ingested = iso(ingested_at)
    valid_at = iso(result.document_generated_at) if result.document_generated_at else None
    with store.transaction():
        store.insert(
            "imports",
            id=import_key,
            source_id=source_id,
            bookmaker_id=BOOKMAKER,
            parser=parser,
            parser_verified=int(result.parser_verified),
            raw_sha256=sha256,
            file_name=file_name,
            raw_path=raw_path,
            data_kind=data_kind,
            offer_kind=result.offer_kind.value,
            generated_at=valid_at,
            ingested_at=ingested,
            rows_total=summary["rows"],
            rows_ok=summary["rows_ok"],
            rows_review=summary["rows_review"],
            unparsed=summary["unparsed_lines"],
            quality=summary["quality"],
            summary_json=canonical(summary),
            fetched_at=iso(fetched_at) if fetched_at else None,
            source_url=source_url,
        )
        for row in result.rows:
            _insert_row(store, row, import_key, valid_at, ingested)
        for index, item in enumerate(result.review):
            store.insert(
                "review_items",
                id=digest(["review", import_key, index]),
                import_id=import_key,
                page=item.page,
                source_text=item.source_text,
                reason=item.reason,
                context=item.context,
            )
    return ImportOutcome(import_key, False, summary)


def _insert_row(store: Store, row: OfferRow, import_key: str, valid_at, ingested: str) -> None:
    event_key = event_id(row)
    store.insert(
        "events",
        id=event_key,
        bookmaker_id=BOOKMAKER,
        sport=row.sport or "unknown",
        sport_raw=row.sport_raw,
        league=row.league_raw,
        country=row.country,
        home=row.home,
        away=row.away,
        participants_json=canonical(list(row.participants)),
        name_raw=" - ".join(row.participants) if row.participants else row.event_name_raw,
        start_at=iso(row.start_local) if row.start_local else None,
        offer_date=row.offer_date.isoformat() if row.offer_date else None,
        first_import_id=_first_import(store, event_key, import_key),
    )
    market_key = market_id(row)
    store.insert(
        "markets",
        id=market_key,
        event_id=event_key,
        market_key=row.market_key,
        period=row.period,
        overtime="na" if row.includes_overtime is None else str(row.includes_overtime).lower(),
        line=row.line,
        label_raw=row.market_raw,
        rules_ref=row.rules_ref,
        mapping_version=MAPPING_VERSION,
    )
    quote_key = digest(["quote", market_key, import_key, row.offer_code, row.page, row.y])
    store.insert(
        "quote_sets",
        id=quote_key,
        market_id=market_key,
        import_id=import_key,
        offer_code=row.offer_code,
        page=row.page,
        source_text=row.source_text,
        status=row.status.value,
        quality=row.quality,
        issues_json=canonical(list(row.issues)),
        valid_at=valid_at,
        ingested_at=ingested,
    )
    for selection in row.selections:
        selection_key = digest(["selection", market_key, selection.key])
        store.insert(
            "selections",
            id=selection_key,
            market_id=market_key,
            key=selection.key,
            label_raw=selection.label,
        )
        store.insert(
            "odds",
            id=digest(["odds", quote_key, selection_key]),
            quote_set_id=quote_key,
            selection_id=selection_key,
            price=str(selection.odds),
            price_text=selection.odds_text,
        )


def _first_import(store: Store, event_key: str, import_key: str) -> str:
    existing = store.get("events", event_key)
    return existing["first_import_id"] if existing else import_key


# ---------------------------------------------------------------- read models


def imports(store: Store, *, include_archived: bool = False) -> list[dict]:
    sql = "SELECT * FROM imports"
    if not include_archived:
        sql += " WHERE bookmaker_id=?"
        return store.rows(sql + " ORDER BY ingested_at DESC", (BOOKMAKER,))
    return store.rows(sql + " ORDER BY ingested_at DESC")


def offer_days(store: Store) -> list[str]:
    return [
        r["offer_date"]
        for r in store.rows(
            """SELECT DISTINCT offer_date FROM events WHERE offer_date IS NOT NULL
            AND bookmaker_id=? ORDER BY offer_date""",
            (BOOKMAKER,),
        )
    ]


def archived_summary(store: Store) -> list[dict]:
    """Rows kept from archived operators (e.g. Superbet); never used in active analyses."""
    return store.rows(
        """SELECT a.entity_id bookmaker, a.archived_at, a.reason,
        (SELECT COUNT(*) FROM imports i WHERE i.bookmaker_id=a.entity_id) imports,
        (SELECT COUNT(*) FROM events e WHERE e.bookmaker_id=a.entity_id) events
        FROM archived_entities a WHERE a.entity_type='bookmaker' ORDER BY a.entity_id"""
    )


def _coverage(store: Store) -> list[dict]:
    """STS imports with offer kind, document time and the offer dates they cover.

    Empty documents (no rows at all) never prove that a market was withdrawn.
    """
    out = []
    for row in store.rows(
        "SELECT id, offer_kind, generated_at, ingested_at, summary_json FROM imports "
        "WHERE bookmaker_id=? AND rows_total > 0",
        (BOOKMAKER,),
    ):
        dates = set(json.loads(row["summary_json"]).get("offer_dates", []))
        out.append({**row, "dates": dates})
    return out


def freshness(valid_at: str | None, start_at: str | None, now: datetime, max_age: timedelta) -> str:
    """fresh / stale / expired (event started) / unknown (no document timestamp)."""
    if start_at and now >= datetime.fromisoformat(start_at):
        return "expired"
    if not valid_at:
        return "unknown"
    age = now - datetime.fromisoformat(valid_at)
    if age < timedelta(0):
        return "unknown"
    return "fresh" if age <= max_age else "stale"


def offer_table(
    store: Store,
    *,
    day: str | None = None,
    include_review: bool = True,
    data_kinds: tuple[str, ...] = ("prospective", "archive"),
) -> list[dict]:
    """Latest STS quote per market (document time, then ingestion), selections pivoted.

    ``presence`` is 'withdrawn' when a newer document of the same offer kind covering the
    event's date no longer lists the market; such prices must not be analysed as current.
    """
    where = [f"i.data_kind IN ({','.join('?' for _ in data_kinds)})", "e.bookmaker_id=?"]
    params: list = [*data_kinds, BOOKMAKER]
    if day:
        where.append("e.offer_date=?")
        params.append(day)
    sql = f"""
      SELECT e.id event_id, e.bookmaker_id, e.sport, e.sport_raw, e.league, e.country, e.home,
             e.away, e.participants_json, e.name_raw, e.start_at, e.offer_date,
             m.id market_id, m.market_key, m.period, m.overtime, m.line, m.label_raw, m.rules_ref,
             q.id quote_set_id, q.offer_code, q.page, q.source_text, q.status, q.quality,
             q.issues_json, q.valid_at, q.ingested_at, q.import_id, i.parser_verified,
             i.data_kind, i.offer_kind, i.fetched_at, i.raw_sha256, i.parser
      FROM quote_sets q JOIN markets m ON m.id=q.market_id JOIN events e ON e.id=m.event_id
      JOIN imports i ON i.id=q.import_id
      WHERE {" AND ".join(where)}
    """
    latest: dict[str, dict] = {}
    for row in store.rows(sql, tuple(params)):
        order = (row["valid_at"] or "", row["ingested_at"])
        current = latest.get(row["market_id"])
        if current is None or order > (current["valid_at"] or "", current["ingested_at"]):
            latest[row["market_id"]] = row
    if not include_review:
        latest = {k: v for k, v in latest.items() if v["status"] == "ok"}
    if not latest:
        return []
    odds_rows = store.rows(
        f"""SELECT o.quote_set_id, o.id odds_id, o.price, s.key, s.id selection_id
        FROM odds o JOIN selections s ON s.id=o.selection_id
        WHERE o.quote_set_id IN ({",".join("?" for _ in latest)})""",
        tuple(v["quote_set_id"] for v in latest.values()),
    )
    by_quote: dict[str, dict] = {}
    for o in odds_rows:
        by_quote.setdefault(o["quote_set_id"], {})[o["key"]] = {
            "price": Decimal(o["price"]),
            "odds_id": o["odds_id"],
            "selection_id": o["selection_id"],
        }
    coverage = _coverage(store)
    result = []
    for row in latest.values():
        row = dict(row)
        row["participants"] = json.loads(row.pop("participants_json"))
        row["issues"] = json.loads(row.pop("issues_json"))
        row["selections"] = by_quote.get(row["quote_set_id"], {})
        newer = [
            c
            for c in coverage
            if c["offer_kind"] == row["offer_kind"] != "unknown"
            and row["offer_date"] in c["dates"]
            and (c["generated_at"] or "", c["ingested_at"])
            > (row["valid_at"] or "", row["ingested_at"])
        ]
        row["presence"] = "withdrawn" if newer else "current"
        result.append(row)
    result.sort(key=lambda r: (r["start_at"] or "", r["league"], r["event_id"], r["market_key"]))
    return result


def quote_history(store: Store, market_id: str) -> list[dict]:
    """Every observed price for a market, oldest first (CLV and audit)."""
    return store.rows(
        """SELECT q.valid_at, q.ingested_at, q.import_id, s.key, o.price, o.id odds_id
        FROM quote_sets q JOIN odds o ON o.quote_set_id=q.id
        JOIN selections s ON s.id=o.selection_id
        WHERE q.market_id=? AND q.status='ok' ORDER BY q.valid_at, q.ingested_at""",
        (market_id,),
    )


def counts(store: Store) -> dict:
    """Active (STS) record counts; archived operators are reported separately."""
    sts = (BOOKMAKER,)
    return {
        "imports": store.scalar("SELECT COUNT(*) FROM imports WHERE bookmaker_id=?", sts),
        "events": store.scalar("SELECT COUNT(*) FROM events WHERE bookmaker_id=?", sts),
        "markets": store.scalar(
            "SELECT COUNT(*) FROM markets m JOIN events e ON e.id=m.event_id "
            "WHERE e.bookmaker_id=?",
            sts,
        ),
        "quote_sets": store.scalar(
            "SELECT COUNT(*) FROM quote_sets q JOIN imports i ON i.id=q.import_id "
            "WHERE i.bookmaker_id=?",
            sts,
        ),
        "odds": store.scalar(
            "SELECT COUNT(*) FROM odds o JOIN quote_sets q ON q.id=o.quote_set_id "
            "JOIN imports i ON i.id=q.import_id WHERE i.bookmaker_id=?",
            sts,
        ),
        "review_items": store.scalar("SELECT COUNT(*) FROM review_items"),
        "predictions": store.scalar("SELECT COUNT(*) FROM predictions"),
    }
