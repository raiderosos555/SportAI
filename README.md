# Sports Edge AI

Lokalna aplikacja Windows do analizy oferty **STS** (od TASK 003 jedyny obsługiwany
bukmacher). Od TASK 005 jej głównym zadaniem jest **tworzenie kuponów STS o możliwie
najwyższej, rzetelnie oszacowanej szansie trafienia** przy realnym zysku po podatku.

Funkcje:
- import PDF i historia kursów, jawny status każdego pobrania oferty STS;
- aktualne wyniki (football-data.org, PitchAPI, openfootball, historia z API-Sports);
- statystyki meczowe i składy (PitchAPI), cechy przedmeczowe as-of;
- modele Elo (1X2, podwójna szansa) i Dixon–Coles (sumy goli, BTTS) z kalibracją, walidacją
  predykcyjną i bramką jakości każdej ligi — **12 lig piłkarskich** (5 czołowych +
  Championship, Eredivisie, Liga Portugal, Pro League, 2. Bundesliga, Süper Lig, Ekstraklasa);
- kreator kuponów w trzech profilach dla dziś / jutro / 3 / 7 dni, z wyjaśnieniem braku kuponu;
- analiza meczu prostym językiem: tryb przedmeczowy i pomeczowy;
- paper trading, automatyczne rozliczenia i panel skuteczności.

Tryb **analityczny i paper trading** — aplikacja nie obstawia i nie łączy się z kontem
bukmachera. Szansa trafienia to oszacowanie modelu, nie gwarancja. Status „zwalidowany
predykcyjnie” nie oznacza przewagi nad STS; analiza EV zostaje jako diagnostyka.

Kod jest w **prywatnym** repozytorium GitHub
[raiderosos555/SportAI](https://github.com/raiderosos555/SportAI). Aplikacja i jej dane
działają lokalnie: baza, oferty PDF, eksporty, kopie zapasowe i `.env` nie trafiają do
repozytorium (`.gitignore`). Commity i push wyłącznie na wyraźne polecenie.
Stan i ograniczenia: [raport TASK 006](docs/task_006_report.md).

## Uruchomienie na Windows

Jednorazowo (wymaga sieci do PyPI):

```powershell
powershell -ExecutionPolicy Bypass -File start.ps1 -Install
```

Na co dzień — dwuklik `start.bat` (albo `.\start.ps1`). Skrypt sprawdza konfigurację
(`sports-edge check`), uruchamia interfejs na `127.0.0.1:8501` i otwiera przeglądarkę.
Start nie pobiera niczego z sieci; tryb offline jest domyślnie włączony.

| Krok w aplikacji | Gdzie |
|---|---|
| Przegląd dnia: liczby, najlepsze prognozy, kupony, status danych | **Dashboard** |
| Kupony 3 profili (dziś / jutro / 3 / 7 dni), powody braku kuponu, typy do rozważenia A/B/C | **Kupon dnia** → „ANALIZUJ MECZE I GENERUJ KUPONY” |
| Ocena modelu, najbardziej prawdopodobny typ, uzasadnienie, forma, statystyki, składy, tryb po meczu | **Analiza meczów** |
| Terminarz, zakończone mecze z wynikami, pełna oferta STS i historia kursów, import PDF | **Wydarzenia i wyniki** |
| Kupony, trafność, ROI, wykres w czasie, rozliczenia, CLV, analizy | **Historia i skuteczność** |
| Modele prostym językiem, pokrycie oferty, metryki, bramki lig, wersje, eksperymenty | **Modele i statystyki** |
| Źródła danych, oficjalne pliki STS, ustawienia, podatki i reguły, kopie, diagnostyka | **Źródła i ustawienia** |
| Dane DEMO (fikcyjne, oddzielna baza) | przełącznik w pasku bocznym |

Aktualne wyniki (bezpłatnie): zarejestruj własny klucz na
[football-data.org](https://www.football-data.org/client/register) i dopisz do pliku `.env`
w katalogu projektu linię `FOOTBALL_DATA_API_KEY=…` (plik jest w `.gitignore`; możesz też użyć
zmiennej środowiskowej). Bez klucza aplikacja działa na danych historycznych i wyjaśnia, dlaczego
prognozy są wstrzymane.

API-Sports (opcjonalnie): klucz z dashboard.api-football.com jako `API_SPORTS_KEY=…`
w `.env`. Uwaga: darmowy plan obejmuje tylko sezony 2022–2024 (sprawdzono 01.11.2026);
bieżące wyniki Ekstraklasy, NBA czy NHL wymagają płatnego planu.

PitchAPI (zalecane): darmowy klucz z pitchapi.dev jako `PITCHAPI_KEY=…` w `.env`.
- Daje wyniki 12 lig z modelem (top 5, Championship, Eredivisie, Liga Portugal, Pro League,
  2. Bundesliga, Süper Lig, Ekstraklasa), statystyki meczowe (xG, strzały, rożne, kartki) i
  składy; historia od sezonu 2021/22 (`sports-edge research backfill`).
- Regulamin nie jest opublikowany, więc dane służą tylko do prywatnej analizy, bez
  redystrybucji.
- Limit to 60 zapytań o statystyki na jedno generowanie kuponów (Ustawienia).

Profile kuponów i progi walidacji: `config/probability_policy.toml` (wersjonowane).

CLI (te same funkcje bez interfejsu):

```powershell
.\.venv\Scripts\sports-edge check
.\.venv\Scripts\sports-edge import-pdf data\samples\sts\2026-10-31\oferta-jutro-www.pdf
.\.venv\Scripts\sports-edge fetch-sts tomorrow          # oficjalny PDF STS (wymaga wyłączenia offline)
.\.venv\Scripts\sports-edge research fetch-data         # openfootball CC0 (wymaga wyłączenia offline)
.\.venv\Scripts\sports-edge research import-data
.\.venv\Scripts\sports-edge research update-results     # brakujące wyniki (football-data.org)
.\.venv\Scripts\sports-edge analyse 2026-11-02 --refresh-offer   # GENERUJ KUPONY DNIA bez interfejsu
.\.venv\Scripts\sports-edge research experiment         # Elo: trening / walidacja / holdout
.\.venv\Scripts\sports-edge research predict            # prognozy przedmeczowe (blokady czasu i danych)
.\.venv\Scripts\sports-edge research goals-experiment   # Dixon–Coles + kalibracja + walidacja (--scope extended)
.\.venv\Scripts\sports-edge research goals-predict      # prognozy rynków bramkowych (wszystkie zakresy)
.\.venv\Scripts\sports-edge research backfill           # historia lig z planu HISTORY_PLAN (bezpłatne źródła)
.\.venv\Scripts\sports-edge research results            # wyniki z danych niezależnych (pewne dopasowanie)
.\.venv\Scripts\sports-edge export json
.\.venv\Scripts\sports-edge backup
.\.venv\Scripts\sports-edge demo
```

## Dane lokalne

| Ścieżka | Zawartość |
|---|---|
| `data/sports_edge.db` | SQLite (append-only): importy, oferty, kursy, wydarzenia, rynki, prognozy, modele, analizy, kupony, wyniki, rozliczenia |
| `data/demo/demo.db` | wyłącznie dane DEMO |
| `data/raw/` | kopie PDF — tylko gdy włączono w Ustawieniach (domyślnie wyłączone) |
| `data/samples/sts/` | autentyczne PDF STS z 31.10.2026 do testów regresji parsera (prywatna kopia) |
| `data/sources/openfootball/` | wyniki historyczne CC0 + manifest (URL, SHA-256, czas pobrania) |
| `data/sources/results/` | surowe odpowiedzi dostawców wyników (pochodzenie każdej aktualizacji) |
| `.env` | lokalne klucze API (`FOOTBALL_DATA_API_KEY`, `PITCHAPI_KEY`, …) — nigdy w repozytorium |
| `data/exports/` | eksporty JSON/CSV |
| `backups/YYYY-MM-DD_HH-MM-SS/` | kopie zapasowe: config, baza (snapshot SQLite), ustawienia, manifest SHA-256 |
| `config/` | profil STS i podatki, `sts_rules.toml` (zweryfikowane reguły), polityka kuponu, aliasy; `config/archive/` — wycofany Superbet |

## Testy i jakość

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
```

223 testy (69 z TASK 001 + 102 z TASK 002–004 + 25 z TASK 005 + 27 z TASK 006: m.in. test
integracyjny od PDF STS do raportu skuteczności, statusy pobierania STS, przypadki braku kuponu,
analiza pomeczowa i scenariusze interfejsu). Testy parserów używają oznaczonych fixture
syntetycznych; test autentycznych PDF STS uruchamia się, gdy istnieje `data/samples/sts`
(katalog nie jest w repozytorium, więc na GitHubie ten test jest pomijany). Te same kontrole
uruchamia GitHub Actions (`.github/workflows/ci.yml`) po każdym pushu.
Dokładne wersje pakietów: `requirements-dev.lock` (TASK 001) i `requirements-app.lock`.

## Struktura

```text
src/sports_edge/
  pdf/            layout (współrzędne słów), sts (oferta drukowana STS), engine (tylko STS)
  offers/         model wiersza oferty, katalog rynków, normalizacja tekstu, źródła (PDF, API)
  analysis/       edge_engine (EV, statusy, poziomy kandydatów), coupon (SOLO/AKO), policy,
                  coupon_builder (kreator kuponów: profile, szansa kuponu, wypłata po podatku)
  models/         registry (wersje, prognozy, bramki walidacji), elo, goals (Dixon–Coles),
                  calibration (izotoniczna, Wilson, przedziały)
  datasources/    kontrakt dostawców wyników, rejestr rozgrywek, football-data.org, PitchAPI,
                  API-Sports, openfootball, STS/Superbet (nieaktywne)
  app/            interfejs Streamlit: main (nawigacja, pasek boczny), ui (design system),
                  page_dashboard, page_coupon, page_match, page_events, pages_history,
                  page_models, page_sources, diagnostics (analiza EV), common (cache, formaty)
  storage.py      SQLite tylko-dopisywanie, migracje do v5 z kopią;  repository.py  importy i widoki
  history.py      historia kursów STS, zmiany, momenty przed meczem, CLV, braki
  results_engine.py  aktualizacja wyników: brakujący okres, duplikaty, korekty, konflikty, hierarchia
  matching.py     dopasowanie STS ↔ dane niezależne, zgodność rynków;  paper.py  kupony, ROI, CLV
  sportsdata.py   dane niezależne (as-of), eksperyment Elo, prognozy przedmeczowe
  probability_lab.py  Dixon–Coles walk-forward, kalibracja, walidacja predykcyjna, prognozy
  sports_intel.py statystyki meczowe, składy, cechy przedmeczowe as-of (migawki)
  performance.py  skuteczność kuponów i selekcji kreatora
  insights.py     warstwa prostego języka: wiarygodność, grupy typów A/B/C, powody braku kuponu
  coverage.py     audyt pokrycia oferty STS (wydarzenia, rynki, selekcje, powody braku prognozy)
  provenance.py   stan kodu i snapshot danych;  bookmakers.py  profil STS i reguły rynków
  backup.py, export.py, services.py, settings.py, cli.py
  domain.py, settlement.py, edge.py, probability.py, temporal.py, markets.py  (fundamenty TASK 001)
```

## Dokumentacja

| Dokument | Zawartość |
|---|---|
| [Raport TASK 006](docs/task_006_report.md) | Redesign, Kupon dnia, analiza meczu, pokrycie 12 lig, zakresy modeli, TASK 007 |
| [Raport TASK 005](docs/task_005_report.md) | Źródła statystyk, Sports Intelligence, modele i kalibracja, kreator kuponów, TASK 006 |
| [Raport TASK 004](docs/task_004_report.md) | Diagnoza, źródła wyników, aktualizacja, prognozy, analiza dnia, TASK 005 |
| [Raport TASK 003](docs/task_003_report.md) | STS ONLY, dane, Elo, walidacja, leakage, wyniki |
| [Raport TASK 002](docs/task_002_report.md) | Aplikacja, parser STS, API, testy (stan sprzed STS ONLY) |
| [Architektura](docs/architecture.md) | Moduły, przepływ, dane, LLM |
| [Źródła danych](docs/data_sources.md) | Pokrycie, ceny, prawa; oferty STS; dane niezależne TASK 003 |
| [Podatki i rozliczenia](docs/tax_and_settlement.md) | Wzory i profile kosztów |
| [Ochrona przed leakage](docs/leakage_prevention.md) | Czas, cechy, podziały, LLM |
| [Metodologia](docs/research_methodology.md), [Protokół](docs/experiment_protocol.md) | Kryteria przewagi, rejestr eksperymentów |
| [Sporty i rynki](docs/sports_and_markets.md), [Wybór rynków](docs/market_selection.md) | Zakres i rozszerzanie |
| [Ryzyka](docs/risks_and_limitations.md), [Roadmapa](docs/roadmap.md), [Raport TASK 001](docs/task_001_report.md) | Granice wniosków i plan |

## Katalog tymczasowy w sandboxie

Jeżeli instalacja odmawia zapisu do systemowego katalogu tymczasowego, ustaw lokalny katalog
tylko dla bieżącej sesji PowerShell i ponów instalację:

```powershell
New-Item -ItemType Directory -Path .tmp -Force | Out-Null
$env:TEMP = (Resolve-Path .tmp).Path
$env:TMP = $env:TEMP
```

## Zasady

Bez fikcyjnych danych w widoku produkcyjnym (przykłady tylko w trybie DEMO), bez zgadywania
kursów (niejednoznaczne wiersze trafiają do ręcznej weryfikacji), bez omijania zabezpieczeń
źródeł, bez płatnych zapytań bez zgody, bez modyfikacji historycznych prognoz. Dodatnie EV
z niezwalidowanego modelu nie jest przewagą. Klucze API wyłącznie w zmiennych środowiskowych.
