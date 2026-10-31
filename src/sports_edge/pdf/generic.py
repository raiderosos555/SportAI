"""Layout-agnostic table parser for text PDFs (Superbet, printed web pages, other operators).

No authentic Superbet PDF was available on 2026-10-31, so every row is marked
``layout_unverified`` and the result has ``parser_verified=False``. Odds are assigned only
when they sit under a recognised column label; otherwise the line goes to manual review.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sports_edge.offers.catalog import GENERIC_LABELS, MarketSpec, generic_market, sport_key
from sports_edge.offers.model import OfferKind, OfferRow, OfferSelection, ParseResult, ReviewItem
from sports_edge.offers.text import (
    TIME_RE,
    fold,
    parse_odds,
    split_line,
    split_participants,
)
from sports_edge.pdf.layout import Document, Row, Token, rows, split_phrase

PARSER = "generic-table"
VERSION = "0.9.0"
WARSAW = ZoneInfo("Europe/Warsaw")
DATE_PATTERNS = (
    re.compile(r"(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})"),
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"),
)


@dataclass
class _Header:
    text: str
    labels: tuple[str, ...]
    centers: tuple[float, ...]
    specs: list[MarketSpec] | None


def _date(text: str) -> date | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return date(int(match["y"]), int(match["m"]), int(match["d"]))
            except ValueError:
                return None
    return None


def _columns(tokens: tuple[Token, ...], width: float) -> tuple[float, ...]:
    """Two page columns when many visual rows carry two start times side by side."""
    full = rows(tokens, ())
    timed = [r for r in full if any(TIME_RE.match(t.text) for t in r.tokens)]
    double = [r for r in timed if sum(bool(TIME_RE.match(t.text)) for t in r.tokens) >= 2]
    return (width / 2,) if timed and len(double) / len(timed) > 0.3 else ()


def _label_row(row: Row, left: float, sport: str) -> _Header | None:
    tokens = list(row.tokens)
    labels: list[Token] = []
    while tokens and fold(tokens[-1].text) in GENERIC_LABELS:
        labels.insert(0, tokens.pop())
    if len(labels) < 2:
        return None
    phrase = " ".join(t.text for t in tokens if fold(t.text) not in {"kod", "godz.", "godzina"})
    names = tuple(t.text for t in labels)
    centers = tuple(t.xc - left for t in labels)
    return _Header(phrase, names, centers, generic_market(phrase, names, sport))


def parse(
    document: Document, *, bookmaker: str = "unknown", file_name: str | None = None
) -> ParseResult:
    out: list[OfferRow] = []
    review: list[ReviewItem] = []
    dates: list[date] = []
    state: dict = {"date": None, "sport_raw": "", "sport": "", "league": "", "header": None}
    for tokens, width in zip(document.pages, document.page_widths, strict=True):
        if not tokens:
            continue
        edges = _columns(tokens, width)
        lefts = {0: 0.0, 1: edges[0] if edges else 0.0}
        for row in rows(tokens, edges):
            left = lefts[row.column]
            text = row.text
            has_time = any(TIME_RE.match(t.text) for t in row.tokens)
            has_odds = sum(parse_odds(t.text) is not None for t in row.tokens) >= 2
            if not has_odds and not has_time and (found := _date(text)):
                state.update(date=found)
                dates.append(found)
                continue
            key, known = sport_key(text)
            if known and not has_odds:
                state.update(sport_raw=text, sport=key, league="", header=None)
                continue
            header = _label_row(row, left, state["sport"])
            if header is not None and not has_odds:
                state["header"] = header
                continue
            if has_odds and has_time:
                out.extend(_event(row, left, state, review, bookmaker))
                continue
            if not has_odds and not has_time and len(text) > 3:
                state.update(league=text)  # a table header may cover several leagues
                continue
            review.append(_review(row, "unclassified_row", state))
    unique = sorted(set(dates))
    kind = OfferKind.WEEK if len(unique) > 1 else OfferKind.UNKNOWN
    return ParseResult(
        bookmaker=bookmaker,
        parser=PARSER,
        parser_version=VERSION,
        parser_verified=False,
        offer_kind=kind,
        rows=tuple(out),
        review=tuple(review),
        pages_total=len(document.pages),
        pages_with_text=sum(1 for p in document.pages if p),
        document_generated_at=document.created_at,
        offer_dates=tuple(unique),
        warnings=(
            "Układ niezweryfikowany na autentycznym dokumencie operatora — "
            "wszystkie wiersze wymagają kontroli przed użyciem w analizie.",
        ),
    )


def _review(row: Row, reason: str, state: dict) -> ReviewItem:
    context = " / ".join(
        str(v) for v in (state.get("date"), state.get("sport_raw"), state.get("league")) if v
    )
    return ReviewItem(
        page=row.page, column=row.column, source_text=row.text, reason=reason, context=context
    )


def _event(row: Row, left: float, state: dict, review: list, bookmaker: str) -> list[OfferRow]:
    header: _Header | None = state["header"]
    if header is None or header.specs is None:
        review.append(_review(row, "no_market_header", state))
        return []
    tokens = list(row.tokens)
    issues = ["layout_unverified", "rules_unverified"]
    code = tokens.pop(0).text if tokens[0].text.isdigit() and len(tokens[0].text) >= 3 else None
    start = None
    for token in list(tokens):
        if TIME_RE.match(token.text):
            hour, minute = token.text.split(":")
            start = time(int(hour), int(minute))
            tokens.remove(token)
            break
    gaps = [b - a for a, b in zip(header.centers, header.centers[1:], strict=False)]
    tolerance = max(3.0, min([10.0, *(g / 2 for g in gaps)]))
    zone_start = header.centers[0] - tolerance
    name_part, _rest = split_phrase(tuple(t for t in tokens if t.xc - left < zone_start), 8.0)
    if any(t.x1 - left > zone_start + 1 for t in name_part):
        issues.append("unexpected_token")  # name overlaps the odds columns
    name = " ".join(t.text for t in name_part)
    assigned: dict[str, tuple[str, Decimal]] = {}
    for token in tokens:
        if token in name_part:
            continue
        odds = parse_odds(token.text)
        position = token.xc - left
        if odds is None:
            issues.append("unexpected_token")
            continue
        label, center = min(
            zip(header.labels, header.centers, strict=True), key=lambda c: abs(c[1] - position)
        )
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
    if start is None:
        issues.append("missing_time")
    clean, line = split_line(name)
    participants = split_participants(clean) or ()
    if not participants:
        issues.append("participants_unparsed")
    if clean.endswith("..."):
        issues.append("name_truncated")
    start_local = (
        datetime.combine(state["date"], start, WARSAW) if state["date"] and start else None
    )
    result = []
    for spec in header.specs:
        row_issues = list(issues)
        if spec.line_kind and line is None:
            row_issues.append("line_missing")
        selections = [
            OfferSelection(
                label=label, key=key, odds=assigned[label][1], odds_text=assigned[label][0]
            )
            for label, key in spec.selections.items()
            if label in assigned
        ]
        if spec.complete and len(selections) < len(spec.selections):
            row_issues.append("missing_selection")
        if not selections:
            if not spec.complete and len(header.specs) > 1:
                continue  # optional column block (e.g. double chance) not printed for this event
            row_issues.append("no_odds")
        result.append(
            OfferRow(
                bookmaker=bookmaker,
                offer_code=code,
                sport_raw=state["sport_raw"],
                sport=state["sport"],
                league_raw=state["league"],
                country=state["league"].split(" - ")[0] if " - " in state["league"] else None,
                event_name_raw=name,
                participants=participants,
                start_local=start_local,
                offer_date=state["date"],
                market_raw=header.text or "/".join(header.labels),
                market_key=spec.market_key,
                period=spec.period,
                includes_overtime=spec.includes_overtime,
                line=line if spec.line_kind else None,
                selections=tuple(selections),
                page=row.page,
                column=row.column,
                y=round(row.y, 1),
                source_text=row.text,
                issues=tuple(dict.fromkeys(row_issues)),
                rules_ref=f"{bookmaker}:pdf-generic:{spec.market_key}:{spec.period}:unverified",
            )
        )
    return result
