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
- kreator kuponów w czterech profilach (Wysoka trafność, Standardowy, Wyższa wypłata,
  Tasiemka) i generator „kupon na kurs”, dla dziś / jutro / 3 / 7 dni;
- **Mój kupon**: własny kupon z oferty STS z walidacją i oceną modelami („OCEŃ MÓJ KUPON AI”),
  alternatywami i pytaniami; opcjonalnie objaśnienia lokalnego, bezpłatnego modelu AI
  (Ollama + polski Bielik 11B, domyślnie wyłączone, dane nie opuszczają komputera);
- symulator budżetu (Monte Carlo) i symulator historyczny (dane as-of);
- protokół walidacji v2 (holdout nie decyduje), rozliczanie każdej prognozy, panel
  **Uczenie AI** (champion–challenger, tryb cienia, aktywacja tylko przez użytkownika);
- analiza meczu prostym językiem: tryb przedmeczowy i pomeczowy;
- paper trading, automatyczne rozliczenia i panel skuteczności;
- ocena prospektywna (TASK 011): kalibracja strefy kuponów (szansa ≥ 60%) liczona na meczach,
  porównanie z kursami STS sprzed meczu bez marży, panel decyzji champion–challenger,
  liczbowe „Ryzyko dnia”;
- modele, które się uczą (TASK 012): protokół v3 z oknami ruchomymi względem daty treningu
  (challengery mogą zmienić hiperparametry i kalibratory; aktywacja nadal tylko przez
  użytkownika), walk-forward v3 kontra zamrożony v2, automatyczne prognozy oferty po każdym
  nowym dokumencie STS (bez kuponów), raport tygodniowy kalibracji z alertem, pobrania oferty
  „w dniu meczu” w limitach STS, porównanie z rynkiem według wieku kursu, „Ryzyko dnia” z macierzy
  wyników Dixon–Coles.

Tryb **analityczny i paper trading** — aplikacja nie obstawia i nie łączy się z kontem
bukmachera. Szansa trafienia to oszacowanie modelu, nie gwarancja. Status „zwalidowany
predykcyjnie” nie oznacza przewagi nad STS; analiza EV zostaje jako diagnostyka.

Kod jest w repozytorium GitHub
[raiderosos555/SportAI](https://github.com/raiderosos555/SportAI), które jest **publiczne**
(stan z 06.11.2026). Aplikacja i jej dane działają lokalnie: baza, oferty PDF, eksporty,
kopie zapasowe i `.env` nie trafiają do repozytorium (`.gitignore`, sprawdzane testem
`tests/test_task010.py`). Commity i push wyłącznie na wyraźne polecenie.
Stan i ograniczenia: [raport TASK 012](docs/task_012_report.md).

## Uruchomienie na Windows

Jednorazowo (wymaga sieci do PyPI):

```powershell
powershell -ExecutionPolicy Bypass -File start.ps1 -Install
```

Na co dzień — dwuklik `start.bat` (albo `.\start.ps1`). Skrypt sprawdza konfigurację
(`sports-edge check`), uruchamia interfejs na `127.0.0.1:8501` i otwiera przeglądarkę.
Start nie pobiera niczego z sieci; tryb offline jest domyślnie włączony.

Automatyzacją steruje się wyłącznie ze strony **Automatyzacja AI**. **URUCHOM AUTOMATYZACJĘ**
startuje osobny proces workera: wyniki, rozliczenia, składy przed meczem, kontrola kuponów
i przeglądy modeli. Worker działa także po zamknięciu karty przeglądarki, dopóki działa serwer
aplikacji. Po ponownym `start.bat` wraca sam, jeśli włączono „Automatycznie wznów po starcie
serwera”. Harmonogram zadań Windows nie jest potrzebny.

| Krok w aplikacji | Gdzie |
|---|---|
| Przegląd dnia: liczby, najlepsze prognozy, kupony, status danych | **Dashboard** |
| Kupony 4 profili (dziś / jutro / 3 / 7 dni), powody braku kuponu, typy do rozważenia A/B/C | **Kupon dnia** → „ANALIZUJ MECZE I GENERUJ KUPONY” |
| Własny kupon z oferty, ocena AI, najsłabsze ogniwo, alternatywy, pytania | **Mój kupon** |
| Kupon jak najbliżej wybranego kursu; długi kupon 8–12 | **Kupon na kurs**, **Tasiemka** |
| Ryzyko serii kuponów (Monte Carlo); odtworzenie przeszłego dnia as-of | **Symulator budżetu**, **Symulator historyczny** |
| Champion i challenger, aktywacja nowego modelu, przegląd, jakość prognoz, eksperyment cech (model rozszerzony) | **Uczenie AI** |
| Worker automatyzacji w zakładkach: przegląd, harmonogram (kalendarz, stałe godziny i dni, zadania jednorazowe, kontrole przed meczem, kursy STS w dniu meczu), procesy (także automatyczne prognozy i raport tygodniowy), historia, alerty | **Automatyzacja AI** |
| Ocena modelu, najbardziej prawdopodobny typ, uzasadnienie, forma, statystyki, składy, tryb po meczu | **Analiza meczów** |
| Terminarz, zakończone mecze z wynikami, pełna oferta STS i historia kursów, import PDF | **Wydarzenia i wyniki** |
| Kupony, trafność, ROI, wykres w czasie, rozliczenia, CLV, analizy | **Historia i skuteczność** |
| Modele prostym językiem, pokrycie oferty, metryki, bramki lig, wersje, eksperymenty | **Modele i statystyki** |
| Źródła danych, oficjalne pliki STS, ustawienia (także lokalny model AI), monitoring, kopie, diagnostyka | **Źródła i ustawienia** |
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

Profile kuponów i progi walidacji: `config/probability_policy.toml` (wersjonowane). Ocena
prospektywna (próg 30 meczów dla flag, 300 meczów dla werdyktu, wygasanie challengerów po 14
dniach, przedziały wieku kursu): `config/prospective_policy.toml` — nie zmienia progów profili
ani statusów. Okna protokołu v3 (12 + 12 miesięcy, półsezony przy krótszej historii):
`config/protocol_v3.toml`.

Lokalny model AI do objaśnień kuponu (opcjonalnie, bezpłatnie): zainstaluj Ollamę z
[ollama.com](https://ollama.com/download/windows), pobierz model jednym poleceniem i włącz go w
Źródła i ustawienia → Ustawienia (przycisk „Sprawdź lokalny model”):

```powershell
ollama pull hf.co/speakleash/Bielik-11B-v2.3-Instruct-GGUF:Q4_K_M
```

Bielik to polski model SpeakLeash (≈ 6,7 GB; licencja Apache 2.0 + warunki bielik.ai/terms).
Aplikacja łączy się wyłącznie z Ollamą na tym komputerze (127.0.0.1).

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
.\.venv\Scripts\sports-edge research protocol-v2 --scope core   # nowe modele (challengery) w protokole v2
.\.venv\Scripts\sports-edge research protocol-v3 --scope core   # TASK 012: challengery z oknami ruchomymi
.\.venv\Scripts\sports-edge research walk-forward-v3 --scope core   # TASK 012: retrening co miesiąc vs v2
.\.venv\Scripts\sports-edge research ablation --scope core      # ablacja A Elo / B DC / C DC+cechy / D ML
.\.venv\Scripts\sports-edge learning review             # przegląd modeli na żądanie (automatycznie: panel Automatyzacja AI)
.\.venv\Scripts\sports-edge learning catch-up           # uzupełnienie po przerwie (rozliczenia, wyniki)
.\.venv\Scripts\sports-edge learning status             # kompletność danych i statusy modeli
.\.venv\Scripts\sports-edge lineage                    # TASK 010: kupon oficjalny / duplikaty / wersje (backfill)
.\.venv\Scripts\sports-edge diversity --time 10:00      # TASK 010: replay as-of z limitem różnicowania 0 / 1 / 2
.\.venv\Scripts\sports-edge calibration --scope core    # TASK 011: kalibracja strefy kuponów na meczach (JSON)
.\.venv\Scripts\sports-edge market-benchmark            # TASK 011: zapis porównania z rynkiem STS (append-only)
.\.venv\Scripts\sports-edge forecasts                   # TASK 012: prognozy oferty dziś i jutro, bez kuponów
.\.venv\Scripts\sports-edge calibration-report          # TASK 012: raport tygodniowy kalibracji i alerty
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
| `data/drafts/` | szkic Mojego kuponu (sam koszyk, bez kluczy) |
| `artifacts/qa/` | raport QA interfejsu z `python -m sports_edge.qa` (dane syntetyczne) |
| `backups/YYYY-MM-DD_HH-MM-SS/` | kopie zapasowe: config, baza (snapshot SQLite), ustawienia, manifest SHA-256 |
| `config/` | profil STS i podatki, `sts_rules.toml` (zweryfikowane reguły), polityka kuponu, aliasy; `config/archive/` — wycofany Superbet |

## Europejskie puchary i reprezentacje (TASK 009)

Liga Mistrzów, Liga Europy i Liga Konferencji (openfootball/champions-league CC0 od 2011/12,
football-data.org CL od 2023/24, PitchAPI LE/LK od 2021/22) oraz reprezentacje (Liga Narodów,
eliminacje i turnieje EURO i MŚ: openfootball/internationals CC0, football-data.org EURO 2024 i
MŚ 2026). Wynik meczu w bazie to zawsze wynik po 90 minutach — dogrywka, karne, dwumecz i awans
są zapisywane osobno; wynik źródła, który może zawierać gole z dogrywki, nie trafia do modeli ani
rozliczeń. Import: przycisk w **Źródła → Puchary i reprezentacje** albo zadania harmonogramu
(„Wyniki i terminarz pucharów UEFA”, „Wyniki reprezentacji”). Statusy modeli: **Modele → Puchary
i reprezentacje**; rynki pucharowe trafiają do generatora dopiero po walidacji i decyzji
użytkownika (do tego czasu: „Wydarzenie dostępne — model predykcyjny jeszcze niezwalidowany”).

## Testy i jakość

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
```

388 testów (268 z TASK 001–007 + 31 z TASK 008: worker automatyzacji, składy i absencje,
kontrola kuponów, Advanced Football Prediction Engine, prognozy challengera w trybie cienia;
9 z TASK 009: harmonogram przez WWW, typy kuponów w historii, komponenty interfejsu, checker
wizualny; 17 z TASK 009 International: puchary UEFA, dwumecze, dogrywki i karne, tożsamość
klubów, reprezentacje, bramka generatora, harmonogram, kategorie interfejsu; 13 z HOTFIX 009.1:
własne pule profili generatora, wybór widocznego przycisku i diagnostyka w Playwright;
18 z TASK 010: tożsamość kuponu, kupon oficjalny dnia, rewizje i aktualizacje kursów,
statystyki i alerty liczone raz, podwójne kliknięcie, migracja v8, `.gitignore`; 15 z TASK 011:
kalibracja na meczach i flagi, kurs STS sprzed meczu i usuwanie marży, stany porównania bez
wartości domyślnych, wygasanie challengerów i statusy Elo, brak treningu na tych samych danych,
Ryzyko dnia, migracja v9, panel Modeli; 17 z TASK 012: okna v3 i dostępność wyników,
challenger v3 a champion v2 i brak automatycznej aktywacji, walk-forward bez przecieku na tych
samych meczach, automatyczne prognozy bez kuponów i bez duplikatów, wyzwalacz po nowym
dokumencie STS, raport tygodniowy z jednym alertem na fakt, plan pobrań w dniu meczu z limitami
i zgodą, wiek kursu w porównaniu z rynkiem, Ryzyko dnia z macierzy wyników i oznaczony fallback,
stare nogi kuponów bez identyfikatora meczu,
migracja v10, `conftest` importujący własny kod, panel Automatyzacji).
Ścieżki interfejsu: `python -m sports_edge.qa` (29 ścieżek). Zrzuty ekranu:
`python -m sports_edge.qa_browser` (Playwright). Testy wizualne TASK 009:
`python -m sports_edge.qa_visual <katalog>` — 14 stron w 5 rozdzielczościach (1920, 1366, 1024,
768, 390 px) z asercjami układu (brak poziomego przewijania, niełamane kursy, procenty i daty,
czytelne szerokości kart, widoczne przyciski, działające zakładki, długie nazwy) i scenariuszami;
lokalnie wymaga `pip install playwright` i `python -m playwright install chromium`. Artefakty QA w GitHub Actions
(`qa-report-Windows`, `qa-report-Linux`) zawierają raport ścieżek UI, wersje bibliotek,
syntetyczny PDF i zrzuty ekranu z danych DEMO — nigdy bazy ani kluczy. Testy parserów używają oznaczonych fixture
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
                  page_models, page_sources, diagnostics (analiza EV), common (cache, formaty),
                  page_my_coupon + cart (Mój kupon), page_generator, page_simulators,
                  page_learning (Uczenie AI)
  storage.py      SQLite tylko-dopisywanie, migracje do v10 z kopią;  repository.py  importy i widoki
  my_coupon.py, advisor.py, explainer.py, llm.py   Mój kupon, ocena AI, pytania, lokalny model (Ollama)
  analysis/generator.py  kupon na kurs;  simulation.py  symulator budżetu i historyczny
  learning.py, retraining.py, ablation.py   historia modeli, przegląd, champion–challenger, ablacja
  collection.py   rozliczanie prognoz, zadania, kompletność;  forecast_quality.py  jakość prognoz
  maintenance.py  rozmiar, przyrost, integralność, test odtworzenia;  qa.py  ścieżki UI i artefakt
  history.py      historia kursów STS, zmiany, momenty przed meczem, CLV, braki
  results_engine.py  aktualizacja wyników: brakujący okres, duplikaty, korekty, konflikty, hierarchia
  matching.py     dopasowanie STS ↔ dane niezależne, zgodność rynków;  paper.py  kupony, ROI, CLV
  sportsdata.py   dane niezależne (as-of), eksperyment Elo, prognozy przedmeczowe
  probability_lab.py  Dixon–Coles walk-forward, kalibracja, walidacja predykcyjna, prognozy
  sports_intel.py statystyki meczowe, składy, cechy przedmeczowe as-of (migawki)
  performance.py  skuteczność kuponów i selekcji kreatora
  prospective_eval.py  TASK 011: kalibracja strefy kuponów na meczach, porównanie z rynkiem STS
                  (TASK 012: według wieku kursu), dowody champion–challenger, raport tygodniowy
  rolling.py      TASK 012: protokół v3 (okna ruchome od daty treningu), walk-forward v3 vs v2
  insights.py     warstwa prostego języka: wiarygodność, grupy typów A/B/C, powody braku kuponu
  coverage.py     audyt pokrycia oferty STS (wydarzenia, rynki, selekcje, powody braku prognozy)
  provenance.py   stan kodu i snapshot danych;  bookmakers.py  profil STS i reguły rynków
  backup.py, export.py, services.py, settings.py, cli.py
  domain.py, settlement.py, edge.py, probability.py, temporal.py, markets.py  (fundamenty TASK 001)
```

## Dokumentacja

| Dokument | Zawartość |
|---|---|
| [Raport TASK 012](docs/task_012_report.md) | Modele, które się uczą: protokół v3 z oknami ruchomymi i jego bramka decyzji, walk-forward v3 vs v2, automatyczne prognozy oferty, raport tygodniowy kalibracji, kursy STS w dniu meczu, Ryzyko dnia z macierzy wyników |
| [Raport TASK 011](docs/task_011_report.md) | Wiarygodność prognoz: kalibracja strefy kuponów na meczach, porównanie z rynkiem STS, wykonalność szybszego zbierania dowodów, proces challengerów, Ryzyko dnia jako liczba |
| [Raport TASK 010](docs/task_010_report.md) | Jeden kupon, jedna historia: tożsamość kuponu, kupon oficjalny dnia, rewizje i aktualizacje kursów, statystyki bez duplikatów, alerty raz na zmianę, ryzyko dnia |
| [Raport HOTFIX 009.1](docs/task_009_1_report.md) | Playwright: widoczny przycisk i diagnostyka, pełny cykl workera, odbiór generatora kuponów, CI |
| [Raport TASK 009](docs/task_009_report.md) | Przebudowa interfejsu (Sports Edge AI 3.0): system projektowy, trzy poziomy informacji, harmonogram przez WWW, testy wizualne Playwright |
| [Raport TASK 008](docs/task_008_report.md) | Automatyzacja AI (worker), składy przed meczem, absencje, kontrola kuponów, Advanced Football Prediction Engine, QA, TASK 009 |
| [Raport TASK 007](docs/task_007_report.md) | P0 (przedziały, protokół v2, zależność par), Mój kupon, profile, symulatory, zbieranie danych, uczenie, TASK 008 |
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
