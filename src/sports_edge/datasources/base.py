"""Common contract of sports results providers (TASK 004).

Every adapter returns standardised ``ResultRecord`` objects; the results engine maps them to
the existing ``historical_results`` / ``results`` tables. Network adapters are opt-in: they
respect offline mode, never call paid endpoints, retry a few times with backoff, honour rate
limits and stop at once on 401/403 (access refused is never worked around). Inactive
adapters say why they are inactive instead of fetching.
"""

import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from sports_edge.offers.sources import USER_AGENT, SourceBlocked

STATUSES = (
    "scheduled",
    "in_play",
    "finished",
    "awarded",
    "postponed",
    "suspended",
    "cancelled",
    "unknown",
)
MAX_BYTES = 10_000_000


@dataclass(frozen=True)
class ResultRecord:
    provider: str
    sport: str
    competition: str  # this application's code, e.g. "en.1"
    season: str | None
    source_event_id: str | None
    home: str  # names exactly as published by the source
    away: str
    start_at: datetime | None
    match_date: str  # local calendar date of the competition (ISO)
    status: str
    home_score: int | None = None
    away_score: int | None = None
    details: dict = field(default_factory=dict)  # half time, periods, statistics, duration
    published_at: datetime | None = None  # timestamp declared by the source, if any
    observed_at: datetime | None = None  # when this application received the record
    license: str = ""

    @property
    def complete(self) -> bool:
        """Final result with both scores; anything else cannot feed models or settlement."""
        return (
            self.status in {"finished", "awarded"}
            and isinstance(self.home_score, int)
            and isinstance(self.away_score, int)
        )


@dataclass(frozen=True)
class ProviderStatus:
    active: bool
    reason: str


@dataclass
class FetchOutcome:
    records: list[ResultRecord]
    payloads: list[tuple[str, bytes]]  # (request URL, raw body) kept for provenance
    requests: int


class SportsResultsProvider(Protocol):
    id: str
    name: str
    license: str
    sports: tuple[str, ...]
    competitions: dict[str, str]  # this application's code -> provider's code
    requires_network: bool
    paid: bool

    def status(self, *, offline: bool) -> ProviderStatus: ...

    def fetch(
        self, competition: str, date_from: date, date_to: date, *, now: datetime
    ) -> FetchOutcome: ...


class RateLimiter:
    """At most ``per_minute`` requests in any sliding 60-second window."""

    def __init__(
        self,
        per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.per_minute = per_minute
        self.clock = clock
        self.sleep = sleep
        self.calls: deque[float] = deque()

    def wait(self) -> None:
        now = self.clock()
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()
        if len(self.calls) >= self.per_minute:
            self.sleep(60 - (now - self.calls[0]) + 0.5)
            self.calls.popleft()
        self.calls.append(self.clock())


def http_get(
    url: str,
    *,
    headers: dict[str, str],
    opener=None,
    limiter: RateLimiter | None = None,
    attempts: int = 3,
    backoff: float = 2.0,
    timeout: float = 30,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bytes, dict]:
    """GET with retries for network errors, 429 and 5xx; 4xx refusals are final."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    open_url = opener or urllib.request.urlopen
    for attempt in range(attempts):
        if limiter:
            limiter.wait()
        try:
            with open_url(request, timeout=timeout) as response:
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise SourceBlocked("Odpowiedź przekracza dopuszczalny rozmiar")
                return data, dict(response.headers.items())
        except urllib.error.HTTPError as error:
            last = attempt == attempts - 1
            if error.code == 429 and not last:
                sleep(_retry_after(error.headers))
                continue
            if 500 <= error.code < 600 and not last:
                sleep(backoff * 2**attempt)
                continue
            if error.code in (401, 403):
                raise SourceBlocked(
                    f"Źródło odmówiło dostępu (HTTP {error.code}) — sprawdź klucz; "
                    "blokad nie obchodzimy"
                ) from error
            raise SourceBlocked(f"Serwer odpowiedział HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == attempts - 1:
                reason = getattr(error, "reason", error)
                raise SourceBlocked(f"Brak połączenia ze źródłem: {reason}") from error
            sleep(backoff * 2**attempt)
    raise SourceBlocked("Nie udało się pobrać danych")  # pragma: no cover - loop always returns


def _retry_after(headers) -> float:
    for name in ("Retry-After", "X-RequestCounter-Reset"):
        value = headers.get(name) if headers else None
        if value and str(value).isdigit():
            return min(float(value) + 1, 65.0)
    return 61.0
