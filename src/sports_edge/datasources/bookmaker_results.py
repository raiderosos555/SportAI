"""Bookmaker websites as results sources — both adapters are INACTIVE (checked 2026-11-01).

STS (results and settlement of STS markets):
* Regulamin Internetowych Zakładów Bukmacherskich (2025-07-08), R 14.5: STS publishes the
  results of finished events on its website and archives corrected results; R 14.6: in
  disputes the data provider decides, then official organisers (e.g. UEFA, FIFA), then
  leading agencies and portals, then video evidence.
* No documented results API, export or results file (unlike the printed offer PDF).
  www.sts.pl/robots.txt disallows ``*resultDate*`` (results by date); sts.pl/wyniki redirects
  to the home page; "Statystyki" (stats.sts.pl) redirects to Sportradar's statshub, whose
  robots.txt disallows everything for all crawlers.
* Conclusion: no permitted automated access; STS results are entered manually.

Superbet (auxiliary results only — never odds, coupons, tax profiles or a tab):
* www.superbet.pl/robots.txt disallows ``/wyniki``, ``/tabela``, ``/statystyki-graczy`` and every
  URL with a query string for all crawlers; no documented results API; the site terms on
  automated reads were not found. Conclusion: adapter stays inactive.

The classes exist so that the engine, the source ranking and the interface treat these
sources explicitly instead of silently ignoring them.
"""

from datetime import date, datetime

from sports_edge.datasources.base import FetchOutcome, ProviderStatus
from sports_edge.offers.sources import SourceBlocked

CHECKED_ON = "2026-11-01"


class _InactiveProvider:
    id = ""
    name = ""
    license = "brak licencji na automatyczne pobieranie"
    sports: tuple[str, ...] = ()
    competitions: dict[str, str] = {}
    requires_network = True
    paid = False
    reason = ""

    def status(self, *, offline: bool) -> ProviderStatus:
        return ProviderStatus(False, self.reason)

    def fetch(
        self, competition: str, date_from: date, date_to: date, *, now: datetime
    ) -> FetchOutcome:
        raise SourceBlocked(self.reason)


class StsResultsProvider(_InactiveProvider):
    id = "sts_results"
    name = "STS — wyniki na stronie operatora"
    reason = (
        "STS publikuje wyniki na stronie (Regulamin R 14.5), ale nie udostępnia API ani "
        "eksportu; robots.txt blokuje adresy z parametrem resultDate, a stats.sts.pl "
        "(Sportradar) zabrania robotów. Wyniki STS wyłącznie ręcznie."
    )


class SuperbetResultsProvider(_InactiveProvider):
    id = "superbet_results"
    name = "Superbet — pomocnicze źródło wyników (nieaktywne)"
    reason = (
        "robots.txt Superbet blokuje /wyniki, /tabela, /statystyki-graczy i adresy z "
        "parametrami dla wszystkich robotów; brak udokumentowanego API wyników — "
        "adapter nieaktywny, bez obchodzenia zabezpieczeń."
    )
