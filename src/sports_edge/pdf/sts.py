"""STS printed offer ("OFERTA KURSOWA", sts.pl/oferta-pdf).

Layout verified on authentic documents downloaded 2026-10-31 (today/tomorrow/week): A4 pages,
two text columns, headers distinguished by font size, odds centred under column labels.
Odds are assigned to a selection only by the x-position of the label above them.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sports_edge.offers.catalog import MarketSpec, sport_key, sts_market
from sports_edge.offers.model import (
    OfferKind,
    OfferRow,
    OfferSelection,
    ParseResult,
    ReviewItem,
)
from sports_edge.offers.text import TIME_RE, parse_odds, split_line, split_participants
from sports_edge.pdf.layout import Document, Row, Token, rows

PARSER = "sts-printed-offer"
VERSION = "1.0.0"
WARSAW = ZoneInfo("Europe/Warsaw")
TITLE = "OFERTA KURSOWA"
DAY_RE = re.compile(
    r"^(PONIEDZIAŁEK|WTOREK|ŚRODA|CZWARTEK|PIĄTEK|SOBOTA|NIEDZIELA)\s+(\d{4}-\d{2}-\d{2})$"
)
FOOTER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})$")
PAGE_RE = re.compile(r"^Strona\s*\d+\s*/\s*\d+$")
LABEL_RE = re.compile(r"^(1|X|2|1X|X2|12|-|\+|TAK|NIE|[12X]/[12X]|\d+:\d+)$")
SPORT_SIZE = 14.0
LEAGUE_SIZE = 12.0
CODE_ZONE = 18.0  # code column width from the column's left edge
TIME_ZONE = 240.0  # start-time column begins this far from the left edge


def looks_like_sts(document: Document) -> bool:
    for tokens, width in zip(document.pages[:6], document.page_widths[:6], strict=False):
        texts = [r.text for r in rows(tokens, (width / 2,))]
        if TITLE in texts and any(DAY_RE.match(t) for t in texts):
            return True
    return False


@dataclass
class _Market:
    header: str
    labels: tuple[str, ...]
    centers: tuple[float, ...]  # label centres relative to the column's left edge
    specs: list[MarketSpec] | None


@dataclass
class _Pending:
    page: int
    column: int
    y: float
    code: str
    name: str
    tokens_text: str
    sport_raw: str
    sport: str
    league_raw: str
    offer_date: date | None
    start: time | None
    market: _Market
    assigned: dict[str, tuple[str, Decimal]]
    issues: list[str] = field(default_factory=list)


def parse(document: Document, *, file_name: str | None = None) -> ParseResult:
    pending: list[_Pending] = []
    review: list[ReviewItem] = []
    warnings: list[str] = []
    dates: list[date] = []
    footer_at: datetime | None = None
    state = {"date": None, "sport_raw": "", "sport": "", "league": "", "market": None}
    for page_tokens, width in zip(document.pages, document.page_widths, strict=True):
        if not page_tokens:
            continue
        mid = width / 2
        lefts = _left_edges(page_tokens, mid)
        last_event: _Pending | None = None
        last_league: Row | None = None
        for row in rows(page_tokens, (mid,)):
            left = lefts[row.column]
            text = row.text
            if text == TITLE or PAGE_RE.match(text):
                continue
            wrapped = last_league is not None and last_league.column == row.column
            last_league, previous_league = None, last_league if wrapped else None
            if footer := FOOTER_RE.match(text):
                day, hour, minute = footer.groups()
                footer_at = datetime.combine(
                    date.fromisoformat(day), time(int(hour), int(minute)), WARSAW
                )
                continue
            if day := DAY_RE.match(text):
                state.update(date=date.fromisoformat(day.group(2)), market=None)
                dates.append(state["date"])
                continue
            if row.max_size >= SPORT_SIZE:
                key, _known = sport_key(text)
                state.update(sport_raw=text, sport=key, league="", market=None)
                continue
            if row.max_size >= LEAGUE_SIZE:
                if previous_league is not None and row.y - previous_league.y < 18:
                    text = f"{state['league']} {text}"  # long league name wrapped to two rows
                state.update(league=text, market=None)
                last_league = row
                continue
            first = row.tokens[0]
            if first.text.isdigit() and first.x0 - left < CODE_ZONE:
                event = _event_row(row, left, state, review)
                if event is not None:
                    pending.append(event)
                    last_event = event
                continue
            if TIME_RE.match(text) and last_event is not None and last_event.start is None:
                if 0 <= row.y - last_event.y < 12 and row.column == last_event.column:
                    last_event.start = _time(text)
                    last_event.issues.remove("missing_time")
                    continue
            market = _market_row(row, left, state["sport"])
            if market is None:
                review.append(_review(row, "unclassified_row", state))
                continue
            state["market"] = market
            if market.specs is None:
                warnings.append(f"Nieskatalogowany rynek: {market.header!r}")
    result_rows = _resolve(pending)
    generated = document.created_at or footer_at
    return ParseResult(
        bookmaker="sts",
        parser=PARSER,
        parser_version=VERSION,
        parser_verified=True,
        offer_kind=_offer_kind(file_name, dates, generated),
        rows=tuple(result_rows),
        review=tuple(review),
        pages_total=len(document.pages),
        pages_with_text=sum(1 for p in document.pages if p),
        document_generated_at=generated,
        offer_dates=tuple(sorted(set(dates))),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _left_edges(tokens: tuple[Token, ...], mid: float) -> dict[int, float]:
    edges = {0: 25.5, 1: mid + 2.9}
    for column in (0, 1):
        codes = [
            t.x0
            for t in tokens
            if t.text.isdigit() and (t.xc > mid) == bool(column) and t.size < LEAGUE_SIZE
        ]
        if codes:
            edges[column] = min(codes)
    return edges


def _time(text: str) -> time:
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


def _review(row: Row, reason: str, state: dict) -> ReviewItem:
    context = " / ".join(
        str(v) for v in (state.get("date"), state.get("sport_raw"), state.get("league")) if v
    )
    return ReviewItem(
        page=row.page, column=row.column, source_text=row.text, reason=reason, context=context
    )


def _market_row(row: Row, left: float, sport: str) -> _Market | None:
    tokens = list(row.tokens)
    labels: list[Token] = []
    while tokens and LABEL_RE.match(tokens[-1].text):
        labels.insert(0, tokens.pop())
    if not tokens or not labels:
        return None
    if labels[0].x0 - tokens[-1].x1 < 4:
        return None
    header = " ".join(t.text for t in tokens)
    names = tuple(t.text for t in labels)
    centers = tuple(t.xc - left for t in labels)
    return _Market(header, names, centers, sts_market(header, names, sport))


def _event_row(row: Row, left: float, state: dict, review: list) -> _Pending | None:
    market: _Market | None = state["market"]
    if market is None:
        review.append(_review(row, "no_market_header", state))
        return None
    tokens = list(row.tokens[1:])
    issues: list[str] = []
    start = None
    if tokens and TIME_RE.match(tokens[-1].text) and tokens[-1].x0 - left > TIME_ZONE:
        start = _time(tokens.pop().text)
    else:
        issues.append("missing_time")
    centers = list(zip(market.labels, market.centers, strict=True))
    gaps = [b - a for a, b in zip(market.centers, market.centers[1:], strict=False)]
    tolerance = max(3.0, min([8.0, *(g / 2 for g in gaps)]))
    zone_start = market.centers[0] - tolerance
    name_tokens: list[Token] = []
    assigned: dict[str, tuple[str, Decimal]] = {}
    for token in tokens:
        odds = parse_odds(token.text)
        position = token.xc - left
        if position < zone_start:
            if odds is not None and name_tokens and token.x0 - name_tokens[-1].x1 > 6:
                issues.append("unassigned_odds")  # a price printed outside every column
            else:
                if token.x1 - left > zone_start + 1:
                    issues.append("unexpected_token")  # name runs into the odds columns
                name_tokens.append(token)
            continue
        if odds is None:
            odds = parse_odds(token.text, allow_whole=True)
            if odds is None:
                issues.append("unexpected_token")
                continue
        label, center = min(centers, key=lambda c: abs(c[1] - position))
        if abs(center - position) > tolerance:
            issues.append("unassigned_odds")
        elif label in assigned:
            issues.append("duplicate_column")
        elif odds <= 1:
            issues.append("invalid_odds")
        else:
            assigned[label] = (token.text, odds)
    if state["date"] is None:
        issues.append("missing_date")
    if not state["sport"]:
        issues.append("missing_sport")
    elif state["sport"].startswith("other:"):
        issues.append("unknown_sport")
    return _Pending(
        page=row.page,
        column=row.column,
        y=row.y,
        code=row.tokens[0].text,
        name=" ".join(t.text for t in name_tokens),
        tokens_text=row.text,
        sport_raw=state["sport_raw"],
        sport=state["sport"],
        league_raw=state["league"],
        offer_date=state["date"],
        start=start,
        market=market,
        assigned=assigned,
        issues=issues,
    )


def _start(p: _Pending) -> datetime | None:
    if p.offer_date is None or p.start is None:
        return None
    return datetime.combine(p.offer_date, p.start, WARSAW)


def _resolve(pending: list[_Pending]) -> list[OfferRow]:
    """Second pass: complete truncated names and attach team totals to their events."""
    events: dict[tuple, set[tuple[str, str]]] = {}
    for p in pending:
        name, _line = split_line(p.name)
        parts = split_participants(name) if not name.endswith("...") else None
        if parts:
            key = (p.offer_date, p.start, p.sport, p.league_raw)
            events.setdefault(key, set()).add(parts)
    out: list[OfferRow] = []
    for p in pending:
        key = (p.offer_date, p.start, p.sport, p.league_raw)
        name, line = split_line(p.name)
        issues = list(p.issues)
        specs = p.market.specs
        if specs is None:
            issues.append("unknown_market")
            specs = [MarketSpec("unknown", "unknown", None, {t: t for t in p.market.labels})]
        team_market = specs[0].market_key in {"team_total_home", "team_total_away"}
        participants: tuple[str, ...] = ()
        if name.endswith("..."):
            prefix = name[:-3].rstrip()
            found = [e for e in events.get(key, ()) if " - ".join(e).startswith(prefix)]
            if len(found) == 1:
                participants = found[0]
                issues.append("name_completed_from_other_row")
            else:
                issues.append("name_truncated")
        elif team_market:
            side = 0 if specs[0].market_key == "team_total_home" else 1
            found = [e for e in events.get(key, ()) if e[side] == name]
            if len(found) == 1:
                participants = found[0]
            else:
                issues.append("unresolved_team_total")
                participants = (name,)
        else:
            participants = split_participants(name) or ()
            if not participants:
                issues.append("participants_unparsed")
        for spec in specs:
            row_issues = list(issues)
            if spec.line_kind and line is None:
                row_issues.append("line_missing")
            if not spec.line_kind and line is not None:
                row_issues.append("line_unexpected")
            selections = []
            for label, key_ in spec.selections.items():
                if label in p.assigned:
                    text, odds = p.assigned[label]
                    selections.append(
                        OfferSelection(label=label, key=key_, odds=odds, odds_text=text)
                    )
                elif spec.complete:
                    row_issues.append("missing_selection")
            if not selections:
                if spec.complete or len(specs) == 1:
                    row_issues.append("no_odds")
                else:
                    continue  # e.g. double chance column block not printed for this event
            row_issues.append("rules_unverified")
            out.append(
                OfferRow(
                    bookmaker="sts",
                    offer_code=p.code,
                    sport_raw=p.sport_raw,
                    sport=p.sport,
                    league_raw=p.league_raw,
                    country=p.league_raw.split(" - ")[0] if " - " in p.league_raw else None,
                    event_name_raw=p.name,
                    participants=participants,
                    start_local=_start(p),
                    offer_date=p.offer_date,
                    market_raw=p.market.header,
                    market_key=spec.market_key,
                    period=spec.period,
                    includes_overtime=spec.includes_overtime,
                    line=line if spec.line_kind else None,
                    selections=tuple(selections),
                    page=p.page,
                    column=p.column,
                    y=round(p.y, 1),
                    source_text=p.tokens_text,
                    issues=tuple(dict.fromkeys(row_issues)),
                    rules_ref=f"sts:pdf:{spec.market_key}:{spec.period}:unverified",
                )
            )
    return out


def _offer_kind(file_name: str | None, dates: list[date], generated: datetime | None) -> OfferKind:
    name = (file_name or "").lower()
    if "dzisiaj" in name:
        return OfferKind.TODAY
    if "jutro" in name:
        return OfferKind.TOMORROW
    if "nastepne-dni" in name or "tydzien" in name or "tydzień" in name:
        return OfferKind.WEEK
    unique = sorted(set(dates))
    if len(unique) > 1:
        return OfferKind.WEEK
    if unique and generated is not None:
        local = generated.astimezone(WARSAW).date()
        if unique[0] == local:
            return OfferKind.TODAY
        if unique[0] == local + timedelta(days=1):
            return OfferKind.TOMORROW
    return OfferKind.UNKNOWN
