# Raport końcowy TASK 002 — 31.10.2026

Sports Edge AI jest teraz działającą, lokalną aplikacją Windows (Streamlit + SQLite) do
importu i analizy ofert STS i Superbet. **Etap A jest ukończony** i sprawdzony na
autentycznych dokumentach STS. Etapy B–D mają działające moduły, ale ich wyniki zależą od
danych, których jeszcze nie ma (niezależne statystyki, zwalidowany model, źródło Superbet).
Dlatego aplikacja obecnie i zgodnie z zasadami pokazuje
**BRAK POTWIERDZONEJ PRZEWAGI — BRAK KUPONU DNIA**. Nie deklarujemy zysku ani skuteczności.

W trakcie TASK 002 nie używano Git ani GitHub: brak commitów, push, PR i zdalnego
repozytorium. Katalog `.git` (utworzony 31.10.2026 o 21:12, przed tą sesją pracy, bez
commitów) oraz plik `.github/workflows/ci.yml` z TASK 001 pozostawiono nietknięte — zgodnie
z zakazem usuwania plików.

**Aktualizacja 31.10.2026 (po raporcie):** na wyraźne polecenie użytkownika kod
opublikowano w prywatnym repozytorium
[raiderosos555/SportAI](https://github.com/raiderosos555/SportAI) (gałąź `main`, razem z
workflow CI). Dane lokalne (`data/`, `backups/`, `.env`, próbki PDF STS) pozostają poza
repozytorium.

## 1. Zrealizowane funkcje

| Etap | Funkcja | Status |
|---|---|---|
| A | Lokalna aplikacja Windows: `start.bat`, `start.ps1` (sprawdzenie konfiguracji, start 127.0.0.1, otwarcie przeglądarki, bez sieci przy starcie) | **Gotowe** |
| A | Interfejs Streamlit, ciemny motyw, 8 widoków: Dashboard, STS, Superbet, Porównanie, Analiza AI, Kupon dnia, Historia, Ustawienia; tryb DEMO w osobnej bazie | **Gotowe** |
| A | SQLite tylko-dopisywanie (triggery blokują UPDATE/DELETE), 23 tabele, idempotentne importy | **Gotowe** |
| A | Ręczny import PDF STS — parser układu na współrzędnych (PyMuPDF) | **Gotowe, zweryfikowane na 3 autentycznych PDF** |
| A | Ręczny import PDF Superbet — parser ogólny tabel | Działa na fixture; **niezweryfikowany** (brak autentycznego PDF) |
| A | Normalizacja: sporty, rynki (31 kombinacji rynek/okres), linie, przecinki dziesiętne, polskie znaki, czas Europe/Warsaw → UTC | **Gotowe** |
| A | Tabela wydarzeń i kursów z filtrami, statusem jakości i aktualności | **Gotowe** |
| A | Dopasowanie wydarzeń STS↔Superbet i porównanie identycznych selekcji (kurs, kurs netto, różnica, świeżość) | **Gotowe** (sprawdzone na fixture; brak prawdziwych danych Superbet) |
| B | Oficjalne PDF STS (dziś/jutro/tydzień) — pobranie na żądanie, opt-in, limit 1/10 min | Gotowe, **domyślnie wyłączone** |
| B | Adapter ArbiScan `/odds` + konwersja schematu; PropLine/ArbPlay opisane | Adapter nieprzetestowany na żywo (brak klucza, płatne) |
| B | Niezależne dane: import CSV wyników z wymaganą notą licencyjną, reguła as-of | Gotowe (brak załadowanych danych) |
| B | Kontrola aktualności kursów: `fresh / stale / expired / unknown` | **Gotowe** |
| C | Baseline Elo z marginesem remisu, walk-forward, log-loss, Brier, ECE, krzywa niezawodności | Kod i testy gotowe; **brak treningu na danych rzeczywistych** |
| C | EV brutto, EV netto (profil STS / Superbet), marża, P rynkowe bez marży, niepewność | **Gotowe** |
| C | Ranking kandydatów z bramkami polityki i uzasadnieniem (co wykrył model, dane, nieefektywność, niepewność, walidacja, decyzja) | **Gotowe** |
| D | Kupon dnia SOLO (jedna selekcja na wydarzenie, maks. 5) i analiza AKO (tylko z jawnym modelem łącznym) | **Gotowe** |
| D | Paper trading: zapis przed startem, tryby rozdzielone, ręczne wyniki, rozliczenie z podatkami, korekty przez nowy rekord, ROI, CLV | **Gotowe** (wyniki wprowadzane ręcznie) |
| D | Historia analiz (niezmienne snapshoty), eksport JSON/CSV, kopie zapasowe `backups/YYYY-MM-DD_HH-MM-SS/` | **Gotowe** |

## 2. Struktura aplikacji

Wykorzystano kod TASK 001 (`settlement.settle`, `edge.net_ev`, `probability`, `temporal`,
`domain`, `config`) oraz niedokończony szkic `storage.py` i `ingestion/types.py`; szkic
rozbudowano do schematu v2 zamiast przepisywać projekt.

```text
start.bat / start.ps1           uruchamianie (check → streamlit na 127.0.0.1:8501)
.streamlit/config.toml          ciemny motyw, bez telemetrii, tylko localhost
config/app.toml                 domyślne ustawienia (tryb offline, limity)
config/bookmakers/*.toml        profile STS i Superbet: podatki, promocje, limity, regulaminy, opłaty
config/tax/pl_*_app.toml        datowane profile kosztów aplikacji (ważne do 30.11.2026)
config/coupon_policy.toml       bramki kuponu dnia (wersjonowane)
config/aliases.toml             zweryfikowane aliasy językowe (Madryt→madrid itd.)
src/sports_edge/pdf/            layout.py (tokeny + współrzędne), sts.py, generic.py, engine.py
src/sports_edge/offers/         model.py, catalog.py, text.py, sources.py
src/sports_edge/analysis/       edge_engine.py, comparison.py, coupon.py, policy.py
src/sports_edge/models/         registry.py, elo.py, calibration.py
src/sports_edge/app/            main.py + strony (offers, analysis, history, settings)
src/sports_edge/                storage, repository, matching, paper, sportsdata, bookmakers,
                                export, backup, services, settings, demo, cli
tests/                          133 testy; offer_fixtures.py (oznaczone fixture syntetyczne)
```

Przepływ: **PDF → tokeny ze współrzędnymi → wiersze i kolumny → nagłówek rynku → przypisanie
kursu po pozycji etykiety → ocena jakości → SQLite (hash, import, snapshot) → dopasowanie
wydarzeń → zgodność rynków → EV i bramki → kupon dnia albo brak przewagi → paper → wynik →
rozliczenie → ROI/CLV**.

## 3. Sposób uruchomienia (Windows)

1. Jednorazowo: `powershell -ExecutionPolicy Bypass -File start.ps1 -Install`
   (tworzy `.venv`, instaluje `.[app,dev]`; jedyny krok wymagający sieci).
2. Codziennie: dwuklik `start.bat`. Skrypt uruchamia `sports-edge check` (pliki konfiguracji,
   profile podatkowe i ich ważność, polityka, pakiety, baza), następnie Streamlit na
   `127.0.0.1:8501`; przeglądarka otwiera się automatycznie.
3. W aplikacji: import PDF w zakładce STS/Superbet → **Kupon dnia → Uruchom analizę**.
4. Tryb offline jest domyślny. Pobieranie oficjalnych PDF STS wymaga wyłączenia offline
   i zaznaczenia zgody w **Ustawienia → Źródła i API**.

Zweryfikowano lokalnie: `sports-edge check` (wszystko OK), start serwera Streamlit, render
wszystkich 8 widoków (AppTest, tryb produkcyjny i DEMO) oraz ręczny podgląd w przeglądarce:
Dashboard z danymi STS, widok oferty STS, uruchomienie analizy w Kuponie dnia.

## 4. Stan integracji STS

- **Oficjalne źródło**: strona [sts.pl/oferta-pdf](https://www.sts.pl/oferta-pdf) („OFERTA I
  WYNIKI W PDF”) linkuje trzy pliki: `retail-api.sts.pl/printed-offer?day=today`,
  `?day=tomorrow`, `?day=week`. Sprawdzenie 31.10.2026 ok. 22:18: HTTP 200,
  `application/pdf`, nazwy `oferta-dzisiaj-www.pdf`, `oferta-jutro-www.pdf`,
  `oferta-nastepne-dni-www.pdf`, `Cache-Control: private, max-age=300` (plik odświeżany
  co kilka minut). Nieznana wartość `day` zwraca ofertę na dziś.
- **Dopuszczalność automatycznych odczytów**: `retail-api.sts.pl/robots.txt` → 404 (brak reguł
  dla tego hosta); `www.sts.pl/robots.txt` blokuje robotom adresy z `day=` na hoście www.
  Regulaminu STS nie przeanalizowano pod kątem automatycznego pobierania. Decyzja: pobieranie
  tylko po kliknięciu, opt-in, limit 1 raz / 10 min / plik, brak harmonogramu.
- **Parser** (`sts-printed-offer@1.0.0`): dwie kolumny A4, rozpoznawanie nagłówków po
  rozmiarze czcionki (sport 15 pt, liga 13 pt), etykiety kolumn (1, X, 2, 1X, X2, 12, −, +,
  TAK/NIE, 1/1…2/2, wyniki setów), kursy przypisywane wyłącznie po pozycji x etykiety
  (tolerancja ≤ 8 pt), kontynuacja kolumn i stron, uzupełnianie uciętych nazw tylko z
  jednoznacznego wiersza tej samej ligi i godziny, sumy drużyn wiązane z wydarzeniem.
- **Profil podatkowy**: 12% od stawki, 10% od wygranej powyżej 2280 zł (źródła z TASK 001),
  granica równości zablokowana; ważny 31.10–30.11.2026, potem wymaga ponownej weryfikacji.
- **Reguły rynków**: wywnioskowane z etykiet PDF (np. „z dogrywką”) i oznaczone
  `rules_unverified`; pełne „Zasady dot. zakładów na poszczególne sporty” nieporównane.
  Rynek „1. połowa - handicap” ma linię bez znaku — wyłączony z porównań i analiz.

## 5. Stan integracji Superbet

- Na superbet.pl 31.10.2026 **nie znaleziono oferty PDF**: brak linków w stronie głównej,
  `sitemap.xml` ani w mapie wiki (5206 adresów). Strony trzecie są sprzeczne (jedna opisuje
  linki „Oferta dzienna/tygodniowa” w stopce, inna podaje brak PDF). PDF-y „Oferta zilnică”
  dotyczą Superbet Rumunia i nie są ofertą polską.
- `superbet.pl/robots.txt` blokuje adresy z parametrami i wiele ścieżek oraz w całości
  agentów AI (m.in. Operator, NovaAct). Nie scrapowano strony i nie użyto wewnętrznych API.
- Dostępny jest **ręczny import PDF dostarczonego przez użytkownika** (np. wydruk do PDF).
  Parser ogólny (`generic-table@0.9.0`) obsługuje przecinki dziesiętne, kolumny etykiet i
  linie, ale każdy wiersz ma `layout_unverified` (jakość ≤ 0,7) i nie przechodzi bramek kuponu.
- Profil podatkowy Superbet to **założenie** (ustawowe 12% i 10%) — oznaczone w UI i w EV;
  regulamin z 03.06.2026 i komunikaty dyscyplin nie zostały przeanalizowane.

## 6. Wyniki weryfikacji API

Nie aktywowano żadnej usługi i nie wykonano żadnego zapytania do API (także bezpłatnego
`/health` ArbiScan). Dane z dokumentacji publicznej z 31.10.2026.

| Źródło | Ustalenia | Decyzja |
|---|---|---|
| ArbiScan (`api.arbiscan.pl/v1`) | Agregator, nie API bukmachera. Klucz w `X-API-Key`; `/odds` z filtrami `sport`, `league`, `bookmakers` (nazwy z `/bookmakers`, np. `Sts`, `Superbet`), `markets` (np. `h2h`, `Total|2.5`), `commence_from/to`, `max_age_seconds` 60–900, `limit` ≤ 500, kursor 240 s. Każdy kurs ma `last_update`. Tylko prematch, bez SLA świeżości. Historia wyłączona (retencja 48 h, gdy włączona). Brak zapisów o licencji/przechowywaniu w dokumentacji | Adapter `/odds` i konwerter schematu gotowe (test na przykładzie z dokumentacji), domyślnie zablokowane; przed zakupem przejrzeć regulamin |
| PropLine | Format zgodny z the-odds-api, `apiKey` lub `X-API-Key`. Katalog wymienia „Superbet PL”, STS tylko jako niezmapowaną etykietę; pełna dokumentacja nie podaje kluczy STS/Superbet ani Ekstraklasy | Nie zintegrowano — pokrycie niepotwierdzone |
| JoJ API / ArbPlay | JoJ to marketplace sprzedający feedy ArbPlay (0,01 USD za wywołanie). ArbPlay: surowy JSON 7 bukmacherów greckich, w tym Superbet; brak STS; zgodność z ofertą PL niepotwierdzona | Nie dotyczy polskiej oferty |
| Inne (Odds-API.io, OddsPapi) | Deklarują Superbet (OddsPapi ma slug `superbet.pl`, free 250 zapytań/mies.) | Nieweryfikowane, do oceny w TASK 003 |
| Oficjalne API STS / Superbet | Nie znaleziono publicznie dokumentowanych API dla zewnętrznych deweloperów | Wewnętrzne endpointy aplikacji nie są używane |

## 7. Koszty potencjalnych źródeł

| Źródło | Cena (publiczny cennik) | Limity |
|---|---|---|
| STS — oficjalne PDF | 0 zł | Grzecznościowo 1 pobranie / 10 min / plik |
| ArbiScan API | Starter **349 zł/mies.** (oznaczony „przygotowywany do uruchomienia”), Data **499 zł**, Data Plus **799 zł**; ceny potwierdzone przez dostawcę 23.09.2026; skaner (Pro 179,99 zł, Ultra 229,99 zł) nie obejmuje API | 1 000 / 10 000 / 100 000 udanych zapytań dziennie; 15 / 30 / 180 na minutę |
| PropLine | Free 0 USD; Hobby 9 USD; Pro 19 USD; Streaming Lite 39 USD; Streaming 79 USD; Enterprise indywidualnie | 1 000 – 1 000 000 zapytań dziennie; historia 30–365 dni od Hobby |
| ArbPlay | RapidAPI 29–399 USD/mies.; JoJ 0,01 USD/wywołanie; RapidAPI Basic 100 zapytań/mies. | 15 zapytań/min na plik (klucz testowy) |
| The Odds API (TASK 001) | 30–249 USD/mies., free 500 kredytów | STS niepotwierdzony |

Żaden plan nie został kupiony. Zakup wymaga Twojej zgody na konkretną kwotę i okres.

## 8. Status automatycznego importu PDF

- **STS**: technicznie działa (moduł `StsOfficialPdf`, test z atrapą sieci, ręczny przycisk
  w UI, CLI `sports-edge fetch-sts today|tomorrow|week`). Domyślnie wyłączone; harmonogram
  nie istnieje, bo nie potwierdzono dopuszczalności cyklicznych odczytów w regulaminie STS.
  Trzy autentyczne pliki pobrano jednorazowo w ramach weryfikacji endpointu.
- **Superbet**: brak oficjalnego PDF — automatyczny import niemożliwy, tylko ręczny.

## 9. Wyniki testów parserów

Autentyczne PDF STS z 31.10.2026 (prywatna kopia w `data/samples/sts/2026-10-31`, SHA-256
w `README.txt` obok plików):

| Dokument | Strony (z tekstem) | Wiersze | OK | Do weryfikacji | Linie poza rynkiem | Jakość |
|---|---:|---:|---:|---:|---:|---:|
| dziś (`oferta-dzisiaj-www.pdf`, wyg. 22:10) | 4 (2) | 119 | 117 | 2 | 0 | 0,982 |
| jutro (`oferta-jutro-www.pdf`, wyg. 22:15) | 23 (23) | 2 695 | 2 692 | 3 | 0 | 0,999 |
| tydzień (`oferta-nastepne-dni-www.pdf`, 1–7.11) | 24 (22) | 2 521 | 2 518 | 3 | 0 | 0,999 |

Wiersze „do weryfikacji” to prawidłowe odmowy: nazwa ucięta („…”) bez drugiego wiersza do
uzupełnienia, kurs drukowany jako „1.00” (nieważny). Kontrole poprawności:

- Wizualne porównanie z renderem strony: wiersz 4202 (pusta kolumna „12” pozostaje pusta,
  nazwa uzupełniona z wiersza 50530) oraz 9-kolumnowe „1. połowa / wynik końcowy” (52493).
- Wszystkie **4 080** kompletnych rynków (1X2, 2-drogowe, sumy, HT/FT) z trzech dokumentów
  mają sumę 1/kurs w przedziale 0,98–1,40 — przesunięcie kolumn dałoby wartości odstające.
- Parser odmawia kursów poza kolumną, nachodzących na siebie i nazw wchodzących w kolumny
  kursów (przypadek wykryty na fixture i dodany jako reguła).
- Superbet: parser ogólny przetestowany wyłącznie na oznaczonym fixture syntetycznym.
  **Nie twierdzimy, że działa na produkcyjnej ofercie Superbet.**

## 10. Liczba rzeczywiście odczytanych wydarzeń

Z autentycznych dokumentów STS: **42** (dziś), **763** (jutro), **341** (tydzień, 1–7.11).
Po imporcie do bazy (dokumenty jutro/tydzień częściowo się pokrywają): **1 026 unikalnych
wydarzeń** w 15 dyscyplinach (piłka nożna 669, koszykówka 143, hokej 86, futbol amerykański
39, siatkówka 25, pozostałe 64). Superbet: 0 rzeczywistych wydarzeń (brak źródła).

## 11. Liczba odczytanych rynków

Kursy w zaakceptowanych wierszach: 294 (dziś), 7 006 (jutro), 6 278 (tydzień). W bazie:
**4 383 rynki**, 5 335 snapshotów rynku (5 327 OK, 8 do weryfikacji), **13 594 kursy**,
31 kombinacji typ rynku/okres (1X2, podwójna szansa, zwycięzca, bez remisu, sumy meczu i
drużyn, połowy, obie strzelą, strzeli gola, czerwona kartka, handicap 1X2, HT/FT,
„połowa lub wynik”, sety, dokładny wynik setów). Nieskatalogowane nagłówki trafiają do
ręcznej weryfikacji.

## 12. Wyniki testów jednostkowych

`python -m pytest -q` → **133 passed** (69 z TASK 001 bez zmian + 64 nowe);
`ruff check .` i `ruff format --check .` → czysto. Pokrycie wymaganych testów:

| Wymagany test | Plik |
|---|---|
| Import STS PDF, odczyt wydarzeń, odczyt kursów | `test_pdf_parsers.py`, `test_real_sts_samples.py` |
| Import Superbet PDF | `test_pdf_parsers.py::test_superbet_import_generic_parser_is_marked_unverified` |
| Dopasowanie wydarzeń, zgodność rynków | `test_matching.py` |
| Rozpoznawanie wygasłych kursów | `test_repository.py::test_expired_and_stale_odds_are_recognised`, `test_edge_coupon.py` |
| Wielokrotny import | `test_repository.py::test_repeated_import_is_detected` |
| EV brutto, EV netto, profile podatkowe | `test_edge_coupon.py` |
| Ranking SOLO, obliczanie AKO | `test_edge_coupon.py` |
| Odrzucenie przy niepewnych danych | `test_edge_coupon.py::test_uncertain_or_unverified_data_is_rejected` |
| Lokalna baza, zapis prognoz | `test_repository.py`, `test_paper.py::test_prediction_history_is_immutable` |
| Rozliczenie kuponu | `test_paper.py` |
| Eksport JSON | `test_export_backup.py` |
| Dodatkowo: kopie zapasowe, źródła/API bez zgody, Elo bez leakage, render UI | `test_export_backup.py`, `test_sources_app.py`, `test_models_data.py` |

Testy sprawdzają poprawność kodu, nie skuteczność typowania.

## 13. Ograniczenia danych

- Brak niezależnych danych statystycznych w bazie → brak prognoz modelu → brak EV wobec
  modelu i brak kuponu. Kurs bukmachera (także bez marży) jest tylko benchmarkiem rynku.
- Baseline Elo nie może dziś uzyskać statusu „validated”: brak licencjonowanych kursów
  historycznych do porównania z rynkiem na tych samych meczach.
- Oferta PDF to migawka z chwili wygenerowania; dostępność kursu, limity i odrzucenia
  u operatora są nieznane. Domyślny maks. wiek kursu: 180 min.
- Reguły rozliczeń wywnioskowane z etykiet; regulaminy STS/Superbet nieporównane.
  Profil Superbet jest założeniem. Profile aplikacji wygasają 30.11.2026.
- Superbet: brak autentycznego dokumentu i oficjalnego źródła.
- Wyniki do rozliczeń wprowadzane ręcznie (brak zweryfikowanego źródła wyników).
- Dane jednego dnia (31.10.2026); zmiana układu PDF przez STS wymaga ponownej weryfikacji.
- Kopie PDF STS w `data/samples` są zachowane lokalnie do testów regresji parsera
  (decyzja użytkownika z 31.10.2026); nie są nigdzie przekazywane. Importowanych PDF
  aplikacja domyślnie nie kopiuje — zmienia to przełącznik w Ustawieniach.

## 14. Brakujące funkcje (nie oznaczamy jako gotowe)

- Automatyczny harmonogram pobierania (zablokowany do potwierdzenia warunków źródeł).
- Integracja z prawdziwym źródłem Superbet (PDF, API) i weryfikacja parsera Superbet.
- Adaptery oficjalnych źródeł wyników i statystyk; automatyczne rozliczanie.
- Modele Glicko, Poisson/Dixon–Coles, LightGBM, CatBoost, bayesowskie, ensemble, LLM/Astra
  (architektura `models/registry` je przyjmie; adapter LLM nieskonfigurowany).
- Formularz ręcznej korekty wierszy z kwarantanny (tabela `review_decisions` przygotowana).
- Model łączny dla AKO inny niż jawne założenie niezależności różnych wydarzeń (domyślnie
  wyłączone).

## 15. Proponowany zakres TASK 003

1. **Weryfikacja warunków**: potwierdzić u STS dopuszczalność cyklicznego pobierania PDF;
   wtedy włączyć harmonogram (np. co 60 min) i budować historię kursów do CLV.
2. **Źródło Superbet**: zapytać Superbet o oficjalny PDF/feed; równolegle — po Twojej zgodzie
   na koszt — miesiąc ArbiScan Starter/Data (349–499 zł) do pomiaru pokrycia STS i Superbet.
3. **Dane niezależne**: licencjonowane wyniki (np. football-data.org) i kursy historyczne
   dla jednej ligi (kandydat: Ekstraklasa lub EPL), trening Elo walk-forward, porównanie z
   rynkiem, rejestracja eksperymentu — dopiero wtedy możliwy status „validated”.
4. **Reguły**: zestawić semantykę 10 głównych rynków z regulaminami STS i Superbet,
   zdjąć `rules_unverified`, ustalić znak handicapów.
5. **Wyniki**: adapter oficjalnego źródła wyników z czasem publikacji i automatyczne
   rozliczenia paper; prospektywny protokół paper trading z CI dla ROI i CLV.
6. **UI**: korekta wierszy z kwarantanny, zarządzanie aliasami, przegląd decyzji dopasowań.
