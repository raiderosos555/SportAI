"""Offer sources behind one interface. Network sources are opt-in and user-triggered.

Status 2026-10-31:
* STS publishes official PDF links at https://www.sts.pl/oferta-pdf (today/tomorrow/week).
  retail-api.sts.pl has no robots.txt; the operator's terms were not reviewed for automated
  reads, so downloads happen only on an explicit click, at most once per 10 minutes per kind.
* Superbet PL: no PDF offer found on superbet.pl; manual import of a user-supplied PDF only.
* ArbiScan / PropLine: paid third-party aggregators (not official bookmaker APIs). Keys come
  from environment variables; data calls are blocked unless the user enables the adapter.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from sports_edge.offers.model import OfferKind, OfferRow, OfferSelection, ParseResult
from sports_edge.offers.text import parse_line

USER_AGENT = "SportsEdgeAI/0.2 (local analysis tool; manual trigger)"
MAX_BYTES = 25 * 1024 * 1024
STS_URLS = {
    "today": "https://retail-api.sts.pl/printed-offer?day=today",
    "tomorrow": "https://retail-api.sts.pl/printed-offer?day=tomorrow",
    "week": "https://retail-api.sts.pl/printed-offer?day=week",
}
MIN_INTERVAL_SECONDS = 600


class SourceBlocked(RuntimeError):
    """The source is disabled, offline mode is on, or the call would cost money."""


@dataclass(frozen=True)
class FetchedDocument:
    data: bytes
    file_name: str
    url: str
    fetched_at: datetime
    headers: dict


class OfferSource(Protocol):
    id: str
    bookmaker: str
    requires_network: bool
    paid: bool


@dataclass(frozen=True)
class ManualPdfSource:
    bookmaker: str
    id: str = "manual_pdf"
    requires_network: bool = False
    paid: bool = False


class StsOfficialPdf:
    """Downloads one of the three PDF links that STS lists on sts.pl/oferta-pdf."""

    id = "sts_pdf_official"
    bookmaker = "sts"
    requires_network = True
    paid = False

    def __init__(self, log_path: Path):
        self.log_path = log_path

    def _log(self) -> dict:
        try:
            return json.loads(self.log_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def fetch(self, day: str, *, offline: bool, enabled: bool, opener=None) -> FetchedDocument:
        if offline:
            raise SourceBlocked("Tryb offline jest włączony")
        if not enabled:
            raise SourceBlocked("Pobieranie oficjalnych PDF STS jest wyłączone w ustawieniach")
        if day not in STS_URLS:
            raise ValueError("day must be today, tomorrow or week")
        log = self._log()
        last = log.get(day)
        if last and time.time() - last < MIN_INTERVAL_SECONDS:
            raise SourceBlocked(
                "Ten plik pobrano przed chwilą; odczekaj 10 minut (limit grzeczności)"
            )
        request = urllib.request.Request(STS_URLS[day], headers={"User-Agent": USER_AGENT})
        open_url = opener or urllib.request.urlopen
        try:
            with open_url(request, timeout=60) as response:
                headers = dict(response.headers.items())
                data = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise SourceBlocked(f"Serwer odmówił: HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise SourceBlocked(f"Brak połączenia: {error.reason}") from error
        if len(data) > MAX_BYTES or not data.startswith(b"%PDF"):
            raise SourceBlocked("Odpowiedź nie jest dokumentem PDF w dopuszczalnym rozmiarze")
        log[day] = time.time()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(json.dumps(log), encoding="utf-8")
        disposition = headers.get("Content-Disposition", "")
        name = disposition.split("filename=")[-1].strip('"; ') if "filename=" in disposition else ""
        return FetchedDocument(
            data=data,
            file_name=name or f"sts-{day}.pdf",
            url=STS_URLS[day],
            fetched_at=datetime.now(UTC),
            headers={k: v for k, v in headers.items() if k.lower() != "set-cookie"},
        )


class ArbiScanAdapter:
    """ArbiScan REST v1 (https://arbiscan.pl/api-dane/dokumentacja, contract read 2026-10-31).

    Paid plans (Starter 349 zł, Data 499 zł, Data Plus 799 zł per month on 23.09.2026).
    ``/health`` is free and keyless; ``/odds`` consumes the paid quota and is blocked unless
    the adapter is enabled and a key exists in ARBISCAN_API_KEY.
    """

    id = "arbiscan"
    base_url = "https://api.arbiscan.pl/v1"
    requires_network = True
    paid = True

    def __init__(self, key: str | None = None):
        self.key = key if key is not None else os.environ.get("ARBISCAN_API_KEY")

    def health(self, *, offline: bool, opener=None) -> dict:
        if offline:
            raise SourceBlocked("Tryb offline jest włączony")
        request = urllib.request.Request(
            f"{self.base_url}/health", headers={"User-Agent": USER_AGENT}
        )
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            return json.loads(response.read(1_000_000))

    def odds(self, *, offline: bool, enabled: bool, params: dict, opener=None) -> dict:
        if offline or not enabled:
            raise SourceBlocked("Adapter ArbiScan wyłączony lub tryb offline")
        if not self.key:
            raise SourceBlocked("Brak klucza ARBISCAN_API_KEY (płatny plan, wymaga zgody)")
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{self.base_url}/odds?{query}",
            headers={"User-Agent": USER_AGENT, "X-API-Key": self.key},
        )
        with (opener or urllib.request.urlopen)(request, timeout=30) as response:
            return json.loads(response.read(MAX_BYTES))


ARBISCAN_BOOKMAKERS = {"sts": "sts", "superbet": "superbet"}


def arbiscan_to_result(payload: dict, *, fetched_at: datetime) -> ParseResult:
    """Convert the documented /odds schema; only h2h and Total|line are mapped.

    Not tested against a live response (no paid key). h2h is mapped to a two-way winner
    only when exactly the two team names are present; three outcomes map to 1X2 with the
    API's regular-time semantics unverified, so rows carry ``rules_unverified``.
    """
    rows: list[OfferRow] = []
    warsaw = ZoneInfo("Europe/Warsaw")
    for event in payload.get("events", []):
        home, away = event.get("home_team"), event.get("away_team")
        start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        for book in event.get("bookmakers", []):
            bookmaker = ARBISCAN_BOOKMAKERS.get(str(book.get("key", "")).lower())
            if bookmaker is None:
                continue
            for market in book.get("markets", []):
                family, _, raw_line = market["key"].partition("|")
                outcomes = {o["name"]: Decimal(str(o["price"])) for o in market["outcomes"]}
                if family == "h2h":
                    mapping = {home: "home", away: "away", "Draw": "draw", "X": "draw"}
                    keys = {mapping.get(name) for name in outcomes}
                    if None in keys:
                        continue
                    market_key = "1x2" if "draw" in keys else "match_winner"
                    period, overtime, line = "FT" if "draw" in keys else "MATCH", False, None
                elif family == "Total" and parse_line(raw_line):
                    mapping = {"Over": "over", "Under": "under"}
                    if set(outcomes) - set(mapping):
                        continue
                    market_key, period, overtime, line = "total", "FT", False, parse_line(raw_line)
                else:
                    continue
                selections = tuple(
                    OfferSelection(label=name, key=mapping[name], odds=price, odds_text=str(price))
                    for name, price in outcomes.items()
                    if price > 1
                )
                rows.append(
                    OfferRow(
                        bookmaker=bookmaker,
                        sport_raw=event.get("sport", ""),
                        sport=str(event.get("sport", "")).lower().replace(" ", "_"),
                        league_raw=event.get("league", ""),
                        event_name_raw=f"{home} - {away}",
                        participants=(home, away),
                        market_raw=market["key"],
                        market_key=market_key,
                        period=period,
                        includes_overtime=overtime,
                        line=line,
                        selections=selections,
                        page=0,
                        column=0,
                        source_text=json.dumps(market, ensure_ascii=False)[:500],
                        start_local=start.astimezone(warsaw),
                        offer_date=start.astimezone(warsaw).date(),
                        issues=("rules_unverified", "layout_unverified"),
                        rules_ref=f"arbiscan:{market['key']}:unverified",
                    )
                )
    return ParseResult(
        bookmaker="mixed",
        parser="arbiscan-odds-v1",
        parser_version="0.1.0",
        parser_verified=False,
        offer_kind=OfferKind.UNKNOWN,
        rows=tuple(rows),
        review=(),
        pages_total=0,
        pages_with_text=0,
        document_generated_at=fetched_at,
    )


API_SOURCES = (
    {
        "name": "ArbiScan API",
        "url": "https://arbiscan.pl/api-dane/dokumentacja",
        "kind": "Agregator (nie oficjalne API bukmachera)",
        "sts_superbet": "Tak, deklarowane (Sts, Superbet); pokrycie zależne od rynku",
        "price": "Starter 349 zł, Data 499 zł, Data Plus 799 zł / mies. (23.09.2026)",
        "limits": "1 000 / 10 000 / 100 000 zapytań dziennie; 15/30/180 na minutę",
        "history": "Wyłączona (retencja 48 h, gdy dostępna)",
        "terms": "Brak zapisów o licencji danych w dokumentacji; regulamin do przeglądu",
        "status": "Adapter /odds gotowy (nieprzetestowany na żywo), /health bezpłatny",
        "env": "ARBISCAN_API_KEY",
    },
    {
        "name": "PropLine Odds API",
        "url": "https://prop-line.com",
        "kind": "Agregator, format zgodny z the-odds-api",
        "sts_superbet": "Superbet wymieniony w katalogu; STS tylko jako niezmapowana etykieta",
        "price": "Free 1 000/dzień; Hobby 9 USD; Pro 19 USD; Streaming 39-79 USD / mies.",
        "limits": "Kwoty dzienne wg planu; maks. 20 zapytań równolegle",
        "history": "30-365 dni zależnie od planu (free: zredagowana)",
        "terms": "Brak warunków przechowywania w dokumentacji llms-full.txt",
        "status": "Nie zintegrowano — brak potwierdzonych kluczy STS/Superbet PL",
        "env": "PROPLINE_API_KEY",
    },
    {
        "name": "ArbPlay API (via RapidAPI / Zyla / JoJ API)",
        "url": "https://sportsapis.dev/apis/arbplay",
        "kind": "Surowe JSON operatorów, rynek grecki",
        "sts_superbet": "Superbet wśród 7 bukmacherów greckich; brak STS; PL niepotwierdzone",
        "price": "RapidAPI 29-399 USD / mies.; JoJ API 0,01 USD za wywołanie",
        "limits": "15 zapytań / min na plik (klucz testowy)",
        "history": "Brak",
        "terms": "Nieopisane",
        "status": "Nie zintegrowano — nie dotyczy polskiej oferty",
        "env": "-",
    },
)
