# Raport końcowy TASK 003 — 01.11.2026

**Wynik w jednym zdaniu:** aplikacja działa wyłącznie z ofertą STS, migracja nie straciła
danych, przecieki czasowe w prognozach usunięto, a pierwszy eksperyment na rzeczywistych
danych (Elo, 1X2, 5 lig, 24 330 meczów) jest wyraźnie lepszy od naiwnego benchmarku, ale
**nie ma dowodu przewagi nad rynkiem**. Brakuje legalnych historycznych kursów tych samych
meczów, a niezależne wyniki kończą się 20.09.2026. Dlatego dziś: **BRAK POTWIERDZONEJ
PRZEWAGI — BRAK KUPONU DNIA**.

Zasady pracy: w trakcie TASK 003 bez commitów, push i publikacji (zmiany tylko w plikach
roboczych). Po zakończeniu, na wyraźne polecenie użytkownika, zmiany zapisano jednym commitem
i wypchnięto do prywatnego repozytorium. Nie użyto płatnych usług. Przed migracją wykonano kopie
`backups/2026-11-01_07-29-11/` (pełna) i `backups/pre-migration-v3_2026-11-01_06-46-58.db`
(automatyczna kopia bazy).

Dane w raporcie są rozdzielone na trzy rodzaje:
- **[H] historyczne rzeczywiste** — openfootball;
- **[P] prospektywne rzeczywiste** — PDF STS z 31.10 i 01.11.2026;
- **[S] syntetyczne** — wyłącznie testy.

## A. Konsolidacja STS ONLY

**Wyłączone lub usunięte elementy Superbet**

| Element | Zmiana |
|---|---|
| Zakładki „Superbet” i „Porównanie STS ↔ Superbet” | Usunięte z interfejsu |
| `pdf/generic.py` (parser Superbet), słownik Superbet w `offers/catalog.py`, `analysis/comparison.py`, dopasowanie dwóch bukmacherów w `matching.py`, Superbet w DEMO | Usunięte po analizie zależności; testy dostosowane |
| `pdf/engine.py` | Przyjmuje wyłącznie „OFERTA KURSOWA” STS; inne PDF kończą się błędem „Aplikacja obsługuje wyłącznie STS” |
| `repository.import_offer` | Odrzuca każdy wynik spoza STS i nieaktywne źródło (`superbet_pdf_manual`, `generic_pdf_manual`) |
| `config/bookmakers/superbet.toml`, `config/tax/pl_superbet_app.toml` | Przeniesione do `config/archive/` (nie są wczytywane) |
| Kupony | `create_coupon` przyjmuje tylko profil STS |

Zachowano architekturę wielu dyscyplin, uniwersalne silniki prawdopodobieństw, EV i rozliczeń,
tabelę `benchmark_odds` dla kursów innych operatorów (jako badawczy benchmark, nigdy jako oferta
STS) oraz adapter ArbiScan (płatny, domyślnie zablokowany).

**Interfejs: 7 zakładek po polsku** — Dashboard, Oferta STS (filtry dyscyplin, lig i rynków,
historia kursów, status „wycofany”), Analiza AI, Kupon dnia (kandydaci zwalidowani oddzielnie
od eksperymentalnych), Historia i wyniki (ROI, maks. obsunięcie, CLV), **Modele i eksperymenty**
(nowa: dane, licencje, walidacja, kalibracja, dopasowanie nazw), Ustawienia.

**Migracja danych (schemat v2 → v3)**
- Przed migracją `Store` robi kopię bazy przez API backup SQLite.
- Migracja dodaje tabele `archived_entities`, `schema_migrations` i `benchmark_odds` oraz kolumny
  `imports.fetched_at` i `imports.source_url`, a w `historical_results` kolumny
  `available_at_basis`, `start_at` i `details_json`.
- Superbet i źródła ogólne dostają oznaczenie archiwalne zamiast usunięcia.
- Prawdziwa baza po migracji: 3 importy STS, 1026 wydarzeń, 4383 rynki, 5335 migawek i 13 594
  kursy (identycznie jak przed migracją). Rekordów Superbet było 0.
- Funkcja kopii zapasowej czyta bazę tylko do odczytu, więc nie uruchamia migracji.

**Testy:** zob. sekcja E.

## B. Dane

**Źródło użyte: openfootball/football.json [H]**

| Cecha | Wartość |
|---|---|
| Licencja | CC0-1.0 (domena publiczna), bez klucza API |
| Koszt | 0 zł |
| Pobrano | 72 pliki, 7,4 MB, manifest z SHA-256 w `data/sources/openfootball/manifest.json` |
| Zakres | Premier League, Bundesliga, LaLiga, Serie A, Ligue 1; sezony 2012-13 – 2026-27 (bez Serie A 2012-13 i Ligue 1 2012-13/2013-14 — brak w źródle) |
| Liczba meczów | **24 330**: en.1 5370, es.1 5379, it.1 4980, de.1 4320, fr.1 4281 |
| Okres | 18.08.2012 – **20.09.2026** |
| Pola | data, godzina lokalna, drużyny, wynik końcowy i do przerwy, kolejka |
| Rynki możliwe do oceny | 1X2 i podwójna szansa (czas regulaminowy), sumy goli, obie strzelą, połowy |
| Znaczniki czasu | Brak czasu publikacji → `available_at = start + 36 h` z jawnym `available_at_basis = assumed_lag_36h` (założenie, nie potwierdzony czas) |
| Aktualizacja | Repozytorium ostatnio zaktualizowano 22.09.2026 — **bieżący sezon urywa się na 20.09.2026** |

**Ranking obszarów badawczych** (z pominięciem historycznego ROI)

| # | Obszar | Dane | STS | Kursy historyczne | Decyzja |
|---|---|---|---|---|---|
| 1 | Piłka nożna, 1X2, 5 czołowych lig | CC0, 14 sezonów, godziny meczów | co tydzień, 40 wydarzeń w ofercie 1–7.11 | brak legalnych bezpłatnych | **Wybrany** |
| 2 | Inne ligi openfootball (np. 2. ligi, Eredivisie) | CC0, mniej dopasowań | częściowo | brak | później |
| 3 | Tenis ATP/WTA | repozytoria J. Sackmanna usunięte (404, 01.11.2026); tennis-data.co.uk: „private individuals only”, zakaz „data training… AI” | tak | tylko tennis-data (zastrzeżenia) | zablokowane |
| 4 | Ekstraklasa | brak w openfootball; football-data.org wymaga klucza (rejestracja użytkownika) | tak | brak | wymaga źródła |
| 5 | Koszykówka, hokej | brak zweryfikowanego legalnego źródła zbiorczego | tak | brak | wymaga źródła |

**Braki:** brak legalnych historycznych kursów (football-data.co.uk wyklucza trening AI;
The Odds API, PropLine i ArbiScan są płatne), brak aktualnych wyników po 20.09.2026, brak
xG, składów, kontuzji i absencji, Ekstraklasy, tenisa i koszykówki. Kursy STS gromadzimy
prospektywnie od 31.10.2026.

**Zweryfikowane regulaminy STS** (`config/sts_rules.toml`):
- **Dokumenty:** „Regulamin Internetowych Zakładów Bukmacherskich” (plik 2025-07-08) oraz
  „Zasady dot. zakładów na poszczególne sporty” (plik 2026-09-08).
- **Podatek i wypłaty:** wygrana = współczynnik stawki × kurs (R 17.1; przykład 0,88, wiążący jest
  współczynnik z kuponu), maksymalna wygrana 1 000 000 zł (R 17.2), 10% PIT od wygranej
  powyżej limitu według odrębnych przepisów (R 17.5).
- **Zwroty i przerwania:** zwrot przy przełożeniu lub przerwaniu meczu o ponad 24 h
  (R 15.3.c, Z 15.1.3); anulowana noga AKO liczona z kursem 1,00 (R 15.7).
- **Rynki:** piłka rozliczana po regulaminowym czasie z doliczonym czasem (Z 15.1.1), połowy
  (Z 15.2.1), zakład bez remisu (Z 1.8), handicap europejski (Z 1.7.4). W koszykówce „mecz” bez
  dogrywki (Z 13.2.1), „zwycięzca” z dogrywką (Z 13.2.2), punkty według opisu (Z 13.2.4).
  W hokeju gole w regulaminowym czasie (Z 11.1.1).
- **Niezweryfikowane:** handicap bez znaku, „połowa lub wynik”, czerwona kartka, tenis (krecz),
  hokej 1X2 oraz wszystkie rynki spoza listy.

## C. Modele

| Element | Wynik [H] |
|---|---|
| Algorytm | Elo z marginesem remisu (uporządkowany model 1/X/2), osobne pule ratingów dla lig |
| Wersja | `elo-1x2-en.1-de.1-es.1-it.1-fr.1-202611010647`, status **experimental** |
| Podział czasowy | trening do 30.06.2022 (17 019), walidacja 07.2022–06.2024 (3578), **holdout od 01.07.2024 (3733)** |
| Dobór parametrów | siatka K ∈ {15, 20, 30} × przewaga gospodarza ∈ {40, 60, 80}, tylko na walidacji → K=20, HFA=60, margines remisu 0,26 (z danych sprzed holdoutu) |
| Holdout | **log loss 0,9914, Brier 0,5915, ECE 0,0063** |
| Benchmark klimatologiczny | log loss 1,0746, Brier 0,6504 |
| Różnica log loss (model − klimatologia) | **−0,0832, 95% CI [−0,0947; −0,0708]** (bootstrap blokowy po 392 dniach) |
| Walk-forward (log loss) | 2022-23 0,988 · 2023-24 0,983 · 2024-25 0,989 · 2025-26 0,997 · 2026-27 (n=250) 0,970 |
| Holdout per liga | LaLiga 0,974 · Serie A 0,987 · Ligue 1 0,991 · Bundesliga 0,994 · Premier League 1,011 |
| Kalibracja | krzywa niezawodności w 10 koszykach, odchylenia ≤ 0,022 w koszykach z n ≥ 300 |
| Benchmark rynku | **brak** — nie ma legalnych kursów tych samych meczów; `beats_market` liczone wyłącznie z danych, więc status `validated` jest niemożliwy |
| Eksperyment | zarejestrowany (`experiments`): backtest, status `inconclusive_no_market` |

Pozostałe modele (Poisson, Dixon–Coles, Glicko, LightGBM, CatBoost, bayesowskie, ensemble)
**nie zostały wdrożone**. Bez kursów historycznych i aktualnych wyników nie da się wykazać,
że dają dodatkową wartość — to zgodne z zasadą „każdy model wymaga uzasadnienia”.

**Ograniczenia:** brak porównania z rynkiem; dostępność wyników jest założeniem; ratingi
z danymi do 20.09.2026 są nieaktualne. Dlatego prognozy dla oferty 1–7.11 zostały wstrzymane
(próg 21 dni). Wszystkie 40 meczów z 5 lig w ofercie STS ma zweryfikowane dopasowanie obu
drużyn (dokładne klucze lub 15 aliasów w `config/team_aliases.toml`).

**Audyt przecieku danych i poprawki**

| Luka | Poprawka |
|---|---|
| A. `predict_events` mogło prognozować rozpoczęte mecze | `predict_offer` pomija rozpoczęte mecze; `save_prediction` odrzuca prognozę „prospective” w chwili startu lub później i przy braku godziny |
| B. `latest_predictions` | Zwraca tylko prognozy utworzone przed `as_of` i ściśle przed startem wydarzenia |
| C. Trening | Walk-forward grupuje mecze po dniach: prognozy dnia D używają wyłącznie wyników dni < D (test wyklucza użycie wyniku z tego samego dnia) |
| D. Heurystyka 36 h | Zapisywana jako `assumed_lag_36h`, oddzielnie od `source_timestamp` (CSV z kolumną `available_at`); import i `HistoricalMatch` odrzucają wynik „dostępny” przed dniem meczu |
| E. Walidacja | Wyłącznie podział chronologiczny, dobór parametrów na walidacji, holdout użyty raz (test: zmiana wyników holdoutu nie zmienia doboru) |
| F. Wersjonowanie | Każda prognoza ma: wersję modelu, czas, `info_cutoff` (≤ czas prognozy), `data_snapshot` (SHA-256 wierszy), `code_state` (SHA-256 kodu + HEAD git), walidację w wersji modelu |
| `validated` | `validation_status` sam wylicza „lepszy od rynku” z porównania na tych samych meczach; flaga podana przez wywołującego jest ignorowana |

## D. Wykrywanie przewagi [P]

| Miara | 01.11.2026 | 02.11.2026 |
|---|---:|---:|
| Przeanalizowane selekcje STS | 6903 | 899 |
| Z prognozą modelu | 0 | 0 |
| Kandydaci z EV+ | 0 | 0 |
| Kandydaci eksperymentalni | 0 | 0 |
| Kandydaci zwalidowani | **0** | **0** |
| Odrzucone | 6903 | 899 |

Główne powody odrzuceń dla 01.11:
- brak niezależnej prognozy: 6903;
- kurs nieaktualny (> 180 min): 6238;
- niezweryfikowane reguły rynku: 1355;
- kurs wygasł (mecz rozpoczęty): 665;
- kurs powyżej limitu polityki 10,0: kilkadziesiąt.

**ROI:** brak — nie ma rozliczonych kuponów rzeczywistych ani prospektywnych.

**CLV:** niedostępne. Wymaga notowania STS w ostatnich 30 min przed startem, a raport braków
dla 02.11 pokazuje dla 370 rynków: 24 obserwacje w oknie 24 h i 0 w pozostałych momentach.

**Niepewność:** dla przewagi nad rynkiem nie da się jej oszacować bez kursów. Dla jakości
prognoz zob. przedział ufności w sekcji C.

**Obserwacje z prawdziwych danych:**
- Plik STS „na dziś” pobrany 01.11 o 07:48 był pusty (tylko nagłówek, dzień i stopka). Pusty
  dokument jest teraz oznaczany i nie „wycofuje” rynków z innych plików.
- Wycofanie rynku wykrywamy tylko między dokumentami tego samego rodzaju (dziś/jutro/tydzień),
  bo plik tygodniowy zawiera mniej meczów danego dnia niż plik „na jutro”.

## E. Aplikacja

**Działa:**
- import i oficjalne pobieranie PDF STS (na żądanie, limit 1 na 10 min, bez harmonogramu);
- historia kursów z wykrywaniem zmian, momentami 24/12/6/3/1 h, oknem zamknięcia, raportem
  braków i wykrywaniem wycofanych rynków;
- dane niezależne: openfootball oraz CSV z wymaganą licencją;
- eksperyment Elo, kalibracja i walk-forward;
- prognozy przedmeczowe z blokadami czasu i aktualności danych oraz dopasowanie nazw
  (aliasy zweryfikowane i potwierdzane w aplikacji);
- EV brutto i netto ze statusami danych, modelu, reguł, podatku i aktualności;
- kupon dnia: SOLO, AKO tylko z jawnym modelem łącznym, kandydaci eksperymentalni osobno;
- paper trading: limit wygranej 1 mln zł, rozliczenia ze źródłem wyniku, automatyczne wyniki
  tylko przy pewnym dopasowaniu, ROI, maks. obsunięcie, CLV;
- eksport JSON/CSV i kopie zapasowe.

**Komendy:**

```powershell
.\start.bat                                       # interfejs (127.0.0.1:8501)
.\.venv\Scripts\sports-edge check                 # konfiguracja + migracja z kopią
.\.venv\Scripts\sports-edge import-pdf <plik.pdf> # oferta STS
.\.venv\Scripts\sports-edge research fetch-data   # openfootball (wymaga wyłączenia offline)
.\.venv\Scripts\sports-edge research import-data
.\.venv\Scripts\sports-edge research experiment
.\.venv\Scripts\sports-edge research predict
.\.venv\Scripts\sports-edge research results
```

**Testy:** `pytest` → **147 passed**. W tym 69 testów z TASK 001 (bez zmian) oraz 78 testów
z TASK 002/003. Ruff (`check` i `format --check`) bez uwag.

Testy TASK 002 dotyczące Superbet i porównania dwóch bukmacherów zastąpiono testami odmowy
importu innych operatorów oraz dopasowania STS ↔ dane. Nie usunięto żadnego testu bez
zastąpienia go testem nowego wymagania.

Nowe testy obejmują:
- migrację z archiwizacją;
- historię, zmiany i wycofanie kursów;
- pusty dokument;
- prognozy tylko przed startem;
- leakage (ten sam dzień, dostępność przed meczem);
- chronologiczny walk-forward i nietknięty holdout;
- walidację i benchmark rynku;
- odtwarzalność;
- niezweryfikowane reguły i podatek;
- kopię bez migracji;
- pełny przepływ PDF → raport na oznaczonych danych syntetycznych [S] — test działania, nie
  skuteczności.

Interfejs sprawdzono testem renderowania wszystkich stron (AppTest) oraz podglądem
Dashboardu i zakładki „Modele i eksperymenty”.

## F. Dalsze działania

**Najważniejsze braki** (od najbardziej blokującego):
1. Brak aktualnych wyników — bez nich nie ma prognoz.
2. Brak historycznych kursów tych samych meczów — bez nich nie ma walidacji przewagi.
3. Brak gęstych migawek STS przed meczem — bez nich nie ma CLV.

**Rekomendowany następny eksperyment:** prospektywny test Elo przeciwko kursom STS.
- Kursy: oferta STS pobierana (na żądanie) co najmniej 24 h i 1 h przed meczami 5 lig.
- Wyniki: aktualne źródło po decyzji użytkownika.
- Ocena: log loss modelu vs log loss rynku STS bez marży na tych samych meczach.
- Zasady: zarejestrowany przed startem i zamrożony próg; minimum 300 meczów.

**Propozycja TASK 004:**
1. **Aktualne wyniki** (decyzja użytkownika): bezpłatny klucz football-data.org lub inne
   legalne źródło. Adapter z czasem publikacji zamiast heurystyki 36 h.
2. **Kursy do walidacji:** zapytanie do Tennis-Data/football-data o licencję do celów
   niekomercyjnych albo — po Twojej zgodzie na koszt — próbka płatnej historii (np. The Odds
   API) do importu do `benchmark_odds`.
3. **Prospektywny eksperyment Elo vs STS** z zamrożonym protokołem (przy cyklicznym
   pobieraniu tylko po potwierdzeniu warunków STS).
4. **Dopiero po pkt 1–3:** Dixon–Coles / Poisson (sumy goli) jako kandydat do lepszej
   kalibracji, porównany z Elo i rynkiem.

**Uzasadnienie priorytetów:** każdy model bez aktualnych danych i bez porównania z rynkiem
pozostaje eksperymentalny. Dlatego najpierw dane i protokół, a dopiero potem złożoność modeli.
