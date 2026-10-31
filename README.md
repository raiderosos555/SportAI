# Sports Edge AI

Lokalna aplikacja Windows do analizy ofert **STS** i **Superbet**: import PDF, normalizacja,
porównanie identycznych selekcji, kontrola jakości odczytu, EV brutto/netto z profilami
podatkowymi, kupon dnia (SOLO/AKO) z bramkami dowodowymi, paper trading i rozliczenia.
Tryb **analityczny i paper trading** — aplikacja nie obstawia i nie łączy się z kontem
bukmachera. Poprawnym wynikiem bez zwalidowanego modelu jest komunikat
**BRAK POTWIERDZONEJ PRZEWAGI — BRAK KUPONU DNIA**.

Projekt jest wyłącznie lokalny: bez publikacji, bez push, bez zdalnego repozytorium.
Stan i ograniczenia: [raport TASK 002](docs/task_002_report.md).

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
| Import PDF STS / Superbet | zakładki **STS**, **Superbet** lub **Ustawienia → Import PDF** |
| Oficjalne PDF STS (dziś / jutro / tydzień) | zakładka **STS** — po wyłączeniu trybu offline i zgodzie w Ustawieniach |
| Porównanie kursów i dopasowanie wydarzeń | **Porównanie** |
| Szczegóły wydarzenia, prawdopodobieństwa, źródła | **Analiza AI** |
| Analiza dnia, SOLO/AKO, uzasadnienia | **Kupon dnia** → „Uruchom analizę” |
| Wirtualne kupony, wyniki, ROI, CLV, eksport | **Historia** |
| Dane DEMO (fikcyjne, oddzielna baza) | przełącznik w pasku bocznym |

CLI (te same funkcje bez interfejsu):

```powershell
.\.venv\Scripts\sports-edge check
.\.venv\Scripts\sports-edge import-pdf data\samples\sts\2026-10-31\oferta-jutro-www.pdf --bookmaker sts
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
| `data/exports/` | eksporty JSON/CSV |
| `backups/YYYY-MM-DD_HH-MM-SS/` | kopie zapasowe: config, baza (snapshot SQLite), ustawienia, manifest SHA-256 |
| `config/` | profile bukmacherów i podatków, polityka kuponu, aliasy nazw, rynki |

## Testy i jakość

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
```

133 testy (69 z TASK 001 + 64 z TASK 002). Testy parserów używają oznaczonych fixture
syntetycznych; test autentycznych PDF STS uruchamia się, gdy istnieje `data/samples/sts`.
Dokładne wersje pakietów: `requirements-dev.lock` (TASK 001) i `requirements-app.lock`.

## Struktura

```text
src/sports_edge/
  pdf/            layout (współrzędne słów), sts (oferta drukowana STS), generic (Superbet/inne), engine
  offers/         model wiersza oferty, katalog rynków, normalizacja tekstu, źródła (PDF, STS, API)
  analysis/       edge_engine (EV, bramki), comparison (STS↔Superbet), coupon (SOLO/AKO), policy
  models/         registry (wersje, prognozy), elo (baseline), calibration (log-loss, Brier, ECE)
  app/            interfejs Streamlit (8 widoków)
  storage.py      schemat SQLite tylko-dopisywanie;  repository.py  importy i widoki danych
  matching.py     dopasowanie wydarzeń i zgodność rynków;  paper.py  kupony, rozliczenia, ROI, CLV
  sportsdata.py   niezależne wyniki historyczne (CSV z licencją), trening i prognozy baseline
  bookmakers.py   profile STS/Superbet;  backup.py, export.py, services.py, settings.py, cli.py
  domain.py, settlement.py, edge.py, probability.py, temporal.py, markets.py  (fundamenty TASK 001)
```

## Dokumentacja

| Dokument | Zawartość |
|---|---|
| [Raport TASK 002](docs/task_002_report.md) | Aplikacja, integracje STS/Superbet, API, testy, ograniczenia, TASK 003 |
| [Architektura](docs/architecture.md) | Moduły, przepływ, dane, LLM |
| [Źródła danych](docs/data_sources.md) | Pokrycie, ceny, prawa; sekcja ofert STS/Superbet |
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
