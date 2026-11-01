# Sports Edge AI

Lokalna aplikacja Windows do analizy oferty **STS** (od TASK 003 jedyny obsługiwany
bukmacher): import PDF, historia kursów, niezależne dane sportowe, prognozy przedmeczowe
(baseline Elo), EV brutto/netto ze zweryfikowanym profilem podatkowym i regułami rynków STS,
kupon dnia (SOLO/AKO) z bramkami dowodowymi, paper trading i rozliczenia.
Tryb **analityczny i paper trading** — aplikacja nie obstawia i nie łączy się z kontem
bukmachera. Poprawnym wynikiem bez zwalidowanego modelu jest komunikat
**BRAK POTWIERDZONEJ PRZEWAGI — BRAK KUPONU DNIA**.

Kod jest w **prywatnym** repozytorium GitHub
[raiderosos555/SportAI](https://github.com/raiderosos555/SportAI). Aplikacja i jej dane
działają lokalnie: baza, oferty PDF, eksporty, kopie zapasowe i `.env` nie trafiają do
repozytorium (`.gitignore`). Commity i push wyłącznie na wyraźne polecenie.
Stan i ograniczenia: [raport TASK 003](docs/task_003_report.md).

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
| Analiza dnia: zwalidowani vs eksperymentalni, AKO | **Kupon dnia** → „Uruchom analizę” |
| Wirtualne kupony, wyniki, ROI, obsunięcie, CLV | **Historia i wyniki** |
| Dane niezależne, eksperyment, kalibracja, dopasowanie nazw | **Modele i eksperymenty** |
| Profil podatkowy i reguły STS, baza, archiwum, eksport, kopie | **Ustawienia** |
| Dane DEMO (fikcyjne, oddzielna baza) | przełącznik w pasku bocznym |

CLI (te same funkcje bez interfejsu):

```powershell
.\.venv\Scripts\sports-edge check
.\.venv\Scripts\sports-edge import-pdf data\samples\sts\2026-10-31\oferta-jutro-www.pdf
.\.venv\Scripts\sports-edge fetch-sts tomorrow          # oficjalny PDF STS (wymaga wyłączenia offline)
.\.venv\Scripts\sports-edge research fetch-data         # openfootball CC0 (wymaga wyłączenia offline)
.\.venv\Scripts\sports-edge research import-data
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
| `data/exports/` | eksporty JSON/CSV |
| `backups/YYYY-MM-DD_HH-MM-SS/` | kopie zapasowe: config, baza (snapshot SQLite), ustawienia, manifest SHA-256 |
| `config/` | profil STS i podatki, `sts_rules.toml` (zweryfikowane reguły), polityka kuponu, aliasy; `config/archive/` — wycofany Superbet |

## Testy i jakość

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
```

147 testów (69 z TASK 001 + 78 z TASK 002/003). Testy parserów używają oznaczonych fixture
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
  datasources/    openfootball (CC0)
  app/            interfejs Streamlit (7 widoków)
  storage.py      SQLite tylko-dopisywanie, migracja v3 z kopią;  repository.py  importy i widoki
  history.py      historia kursów STS, zmiany, momenty przed meczem, CLV, braki
  matching.py     dopasowanie STS ↔ dane niezależne, zgodność rynków;  paper.py  kupony, ROI, CLV
  sportsdata.py   dane niezależne (as-of), eksperyment Elo, prognozy przedmeczowe
  provenance.py   stan kodu i snapshot danych;  bookmakers.py  profil STS i reguły rynków
  backup.py, export.py, services.py, settings.py, cli.py
  domain.py, settlement.py, edge.py, probability.py, temporal.py, markets.py  (fundamenty TASK 001)
```

## Dokumentacja

| Dokument | Zawartość |
|---|---|
| [Raport TASK 003](docs/task_003_report.md) | STS ONLY, dane, Elo, walidacja, leakage, wyniki, TASK 004 |
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
