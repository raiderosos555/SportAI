# Sports Edge AI

Lokalna aplikacja Windows do analizy oferty **STS** (od TASK 003 jedyny obsługiwany
bukmacher): import PDF, historia kursów, aktualne wyniki sportowe (football-data.org +
openfootball), prognozy przedmeczowe (baseline Elo), EV brutto/netto ze zweryfikowanym profilem
podatkowym i regułami rynków STS, analiza dnia jednym przyciskiem, kupon dnia (SOLO/AKO)
z bramkami dowodowymi, paper trading i automatyczne rozliczenia.
Tryb **analityczny i paper trading** — aplikacja nie obstawia i nie łączy się z kontem
bukmachera. Poprawnym wynikiem bez zwalidowanego modelu jest komunikat
**BRAK POTWIERDZONEJ PRZEWAGI — BRAK KUPONU DNIA**.

Kod jest w **prywatnym** repozytorium GitHub
[raiderosos555/SportAI](https://github.com/raiderosos555/SportAI). Aplikacja i jej dane
działają lokalnie: baza, oferty PDF, eksporty, kopie zapasowe i `.env` nie trafiają do
repozytorium (`.gitignore`). Commity i push wyłącznie na wyraźne polecenie.
Stan i ograniczenia: [raport TASK 004](docs/task_004_report.md).

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
| Import PDF STS, historia i zmiany kursów | **Oferta STS** (lub **Ustawienia → Import PDF**) |
| Oficjalne PDF STS (dziś / jutro / tydzień) | **Oferta STS** — po wyłączeniu trybu offline i zgodzie w Ustawieniach |
| Statystyki, prognoza, P rynku, EV, uzasadnienie | **Analiza AI** |
| Analiza dnia (8 etapów: oferta → wyniki → prognozy → EV → walidacja → zapis) | **Kupon dnia** → „Uruchom analizę” |
| Wirtualne kupony, wyniki, ROI, obsunięcie, CLV | **Historia i wyniki** |
| Wyniki sportowe (źródła, świeżość lig, aktualizacja, konflikty), eksperyment, kalibracja | **Modele i eksperymenty** |
| Profil podatkowy i reguły STS, baza, archiwum, eksport, kopie | **Ustawienia** |
| Dane DEMO (fikcyjne, oddzielna baza) | przełącznik w pasku bocznym |

Aktualne wyniki (bezpłatnie): zarejestruj własny klucz na
[football-data.org](https://www.football-data.org/client/register) i dopisz do pliku `.env`
w katalogu projektu linię `FOOTBALL_DATA_API_KEY=…` (plik jest w `.gitignore`; możesz też użyć
zmiennej środowiskowej). Bez klucza aplikacja działa na danych historycznych i wyjaśnia, dlaczego
prognozy są wstrzymane.

API-Sports (opcjonalnie): klucz z dashboard.api-football.com jako `API_SPORTS_KEY=…`
w `.env`. Uwaga: darmowy plan obejmuje tylko sezony 2022–2024 (sprawdzono 01.11.2026);
bieżące wyniki Ekstraklasy, NBA czy NHL wymagają płatnego planu.

CLI (te same funkcje bez interfejsu):

```powershell
.\.venv\Scripts\sports-edge check
.\.venv\Scripts\sports-edge import-pdf data\samples\sts\2026-10-31\oferta-jutro-www.pdf
.\.venv\Scripts\sports-edge fetch-sts tomorrow          # oficjalny PDF STS (wymaga wyłączenia offline)
.\.venv\Scripts\sports-edge research fetch-data         # openfootball CC0 (wymaga wyłączenia offline)
.\.venv\Scripts\sports-edge research import-data
.\.venv\Scripts\sports-edge research update-results     # brakujące wyniki (football-data.org)
.\.venv\Scripts\sports-edge analyse 2026-11-02 --refresh-offer   # analiza dnia jednym poleceniem
.\.venv\Scripts\sports-edge research experiment         # Elo: trening / walidacja / holdout
.\.venv\Scripts\sports-edge research predict            # prognozy przedmeczowe (blokady czasu i danych)
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
| `.env` | lokalne klucze API (np. `FOOTBALL_DATA_API_KEY`) — nigdy w repozytorium |
| `data/exports/` | eksporty JSON/CSV |
| `backups/YYYY-MM-DD_HH-MM-SS/` | kopie zapasowe: config, baza (snapshot SQLite), ustawienia, manifest SHA-256 |
| `config/` | profil STS i podatki, `sts_rules.toml` (zweryfikowane reguły), polityka kuponu, aliasy; `config/archive/` — wycofany Superbet |

## Testy i jakość

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
```

171 testów (69 z TASK 001 + 102 z TASK 002–004). Testy parserów używają oznaczonych fixture
syntetycznych; test autentycznych PDF STS uruchamia się, gdy istnieje `data/samples/sts`
(katalog nie jest w repozytorium, więc na GitHubie ten test jest pomijany). Te same kontrole
uruchamia GitHub Actions (`.github/workflows/ci.yml`) po każdym pushu.
Dokładne wersje pakietów: `requirements-dev.lock` (TASK 001) i `requirements-app.lock`.

## Struktura

```text
src/sports_edge/
  pdf/            layout (współrzędne słów), sts (oferta drukowana STS), engine (tylko STS)
  offers/         model wiersza oferty, katalog rynków, normalizacja tekstu, źródła (PDF, API)
  analysis/       edge_engine (EV, statusy, poziomy kandydatów), coupon (SOLO/AKO), policy
  models/         registry (wersje, prognozy, bramki walidacji), elo, calibration
  datasources/    kontrakt dostawców wyników, rejestr rozgrywek, football-data.org, API-Sports,
                  openfootball, STS/Superbet (nieaktywne)
  app/            interfejs Streamlit (7 widoków)
  storage.py      SQLite tylko-dopisywanie, migracja v3 z kopią;  repository.py  importy i widoki
  history.py      historia kursów STS, zmiany, momenty przed meczem, CLV, braki
  results_engine.py  aktualizacja wyników: brakujący okres, duplikaty, korekty, konflikty, hierarchia
  matching.py     dopasowanie STS ↔ dane niezależne, zgodność rynków;  paper.py  kupony, ROI, CLV
  sportsdata.py   dane niezależne (as-of), eksperyment Elo, prognozy przedmeczowe
  provenance.py   stan kodu i snapshot danych;  bookmakers.py  profil STS i reguły rynków
  backup.py, export.py, services.py, settings.py, cli.py
  domain.py, settlement.py, edge.py, probability.py, temporal.py, markets.py  (fundamenty TASK 001)
```

## Dokumentacja

| Dokument | Zawartość |
|---|---|
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
