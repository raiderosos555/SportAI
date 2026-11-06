# Raport TASK 011 — wiarygodność prognoz: ocena prospektywna, rynek STS, strefa kuponów

Data pracy: 06.11.2026. Wszystkie pomiary na prawdziwych danych wykonano na **kopii** prywatnej
bazy (`storage.snapshot_file`, tryb tylko do odczytu, katalog tymczasowy, po pomiarach kopia
usunięta). W raporcie są wyłącznie liczby zbiorcze. Raport nie zawiera rekomendacji zysku ani
stawek.

## 0. Najważniejsze w skrócie

- **Kalibracja strefy kuponów liczona na meczach.** Nowy moduł `prospective_eval.py`: jednostką jest
  mecz, nie selekcja; przedziały Wilsona na meczach; flagi „zbyt optymistyczna / zbyt ostrożna”
  dopiero od 30 meczów (`config/prospective_policy.toml`). Tabela w **Modele → Uczenie AI**
  (i na stronie Uczenie AI), notka przy nodze w Kuponie dnia, Moim kuponie, Kuponie na kurs i
  Tasiemce, CLI `sports-edge calibration`.
- **Na kopii z 06.11 żaden przedział nie ma flagi.** Wczesny sygnał 80–90% (85,2% przewidywane,
  75,6% trafione) dotyczy 37 meczów, a nie 323–472 niezależnych zdarzeń. Górna granica 95% (86,6%)
  jest powyżej średniej szansy, więc dane nie pokazują nadmiernej pewności. Nie pokazują też, że
  jej nie ma.
- **Porównanie z rynkiem STS liczone prospektywnie.** Brany jest kurs z ostatniego dokumentu STS
  sprzed meczu, marża usuwana metodą „power”, przedział bootstrap po meczach. Wyniki trafiają do
  nowej tabeli append-only `market_benchmarks` (schemat v9). Brak danych to „nieznane”, nigdy
  „false”. Na kopii jest 36–38 wspólnych meczów na rynek, więc wszędzie stan to „za mało (n < 300)”.
- **Szybsze zbieranie dowodów nie jest możliwe przez mecze spoza oferty STS.** W 01–05.11
  wszystkie 59 meczów 12 lig ze źródeł były w ofercie STS. Dodatkowych meczów było 0, więc
  prognoz cienia nie zaimplementowano (§4.3).
- **Challengery przestały „wisieć”.** Pojawił się panel decyzji (stan „czeka na dowody (n/300)”,
  „dowody wystarczające — decyzja należy do Ciebie”, „identyczny z championem w oknie selekcji”).
  Challenger wygasa (nowy wpis `expired`), gdy zastąpi go nowszy albo gdy po 14 dniach nie ma
  wspólnych rozliczonych meczów. System nie trenuje challengera bez nowych meczów, chyba że
  użytkownik to wymusi. Statusy Elo są wreszcie widoczne. Aktywacja zawsze należy do użytkownika.
- **Ryzyko dnia jako liczba:** szansa przegranej co najmniej dwóch kuponów dnia i oczekiwana
  liczba przegranych kuponów, liczone na szansach ostrożnych, ze wspólnymi selekcjami liczonymi raz.

## 1. Diagnoza (stan z 06.11.2026, kopia bazy)

### 1.1 Walidacja historyczna (bez zmian)

Wersje aktywne są te same co w specyfikacji: Elo core holdout n = 3936, log loss 0,988 wobec
klimatologii 1,073; Elo extended n = 5563, 1,020 wobec 1,075. Holdout jest tylko raportowany
(protokół v2). Statusy aktywnych wersji po poprawce odczytu (§5.1):

| Zakres | Elo (z raportu Dixon–Coles, który je zmierzył) | Dixon–Coles |
|---|---|---|
| 5 czołowych lig | 1X2 i podwójna szansa: zwalidowane | 1X2, podwójna szansa, O/U 1,5, O/U 2,5: zwalidowane; BTTS, O/U 3,5: eksperymentalne |
| 7 lig | 1X2: zwalidowany; podwójna szansa: eksperymentalna | O/U 2,5: zwalidowany; pozostałe eksperymentalne |

Do TASK 011 panel nie pokazywał żadnego statusu Elo (`retraining._decision` czytał klucz
`predictive`, którego raport Elo v2 nie ma).

### 1.2 Porównanie z rynkiem przed zmianami

`beats_market = false` we wszystkich wersjach przy `market_benchmark = null`. To była wartość
domyślna, nie pomiar. Teraz tekst diagnostyczny kandydata (`edge_engine`) brzmi „rynek: nieznane
(brak porównania na tych samych meczach)”. W widoku badawczym jest napisane wprost, że
`beats_market = false` to wartość domyślna.

### 1.3 Dowody prospektywne

- Na kopii jest 3145 rozliczonych prognoz z 42 meczów (01.11 13:00 – 04.11 20:00). Wszystkie
  rozpoczęte mecze z prognozą są rozliczone.
- Po zostawieniu jednej prognozy na (wersja, mecz, rynek z linią) — najnowszej sprzed startu —
  zostaje **1454 prognoz rynków z 38 meczów**. Podwójna szansa jest tu osobnym rynkiem:
  Dixon–Coles zapisuje ją sam, dla Elo wynika z tego samego 1X2.
- 4 mecze miały prognozy wyłącznie wersji bez zapisanej roli: badawczego eksperymentu 12 lig
  sprzed rozdzielenia zakresów i wersji sprzed zapisu ról. Te mecze pominięto, bo nie wiadomo, czy
  prognozy były serwowane.
- Wersje aktywne mają 1212 prognoz rynków (Elo 112, Dixon–Coles 1100). Challengery w trybie
  cienia mają 242 prognozy (11 meczów).
- Wczesny sygnał 80–90% policzony starą metodą (selekcje, wersje serwowane) daje 323 selekcje z
  37 meczów: przewidywane 85,1%, trafione 74,9%. Ta sama grupa na meczach (Dixon–Coles, wszystkie
  rynki razem) to n = 37 meczów: 85,2% wobec 75,6%, 95% przedział 59,8–86,6%. Przedział obejmuje
  średnią szansę, więc flagi nie ma. Próba jest za mała na wniosek w obie strony.

### 1.4 Challengery w chwili diagnozy

- Core: czekały **dwie** pary challengerów — z 02.11 13:31 i z 06.11 10:43. Starsza para nie była
  już prognozowana w cieniu, a i tak blokowała zakres.
- Extended: czekała jedna para z 06.11 10:40.
- Wszystkie są **identyczne z championem w oknie selekcji**. Mają te same hiperparametry (core Elo:
  k = 15, przewaga gospodarza 40, margines remisu 0,25; extended: k = 30, 40, 0,24) i te same miary
  okna selekcji (log loss 0,98091 = 0,98091; Dixon–Coles 0,98175; extended 1,00056 i 1,02015).
- Przyczyna jest strukturalna. W protokole v2 okna (walidacja, kalibracja, selekcja 2023/24) są
  stałe, więc nowe mecze po holdoucie nie zmieniają wyboru hiperparametrów ani kalibracji.
  Prognoza Elo i tak przechodzi as-of przez wszystkie wyniki do chwili prognozy. Champion i
  challenger z tymi samymi hiperparametrami dają więc **te same prognozy**, a ich prospektywna
  różnica wynosi dokładnie 0.
- Retrening „na nowych meczach” w obecnym protokole nie może dać innego modelu (propozycja w §10).

### 1.5 Kupony

- Kopia ma 20 kuponów, w tym 9 oficjalnych.
- Rozliczone oficjalne według profilu (część ze starych profili sprzed TASK 007):
  - Wyższa wypłata: 3 (2 trafione);
  - Standardowy: 1 (trafiony);
  - Bezpieczny, Zrównoważony i Agresywny: po 1 (trafione).
- To nadal za mało na jakąkolwiek ocenę trafności kuponów.

## 2. Kalibracja prospektywna w strefie kuponów (P0)

**Dane.** Rozliczone prognozy prospektywne (status `settled`, prognoza sprzed startu). Na każdy
(wersja, mecz, rynek z linią) zostaje najnowsza prognoza sprzed meczu. Każda prognoza ma rolę:

- **aktywna wersja** — wersja była serwowana w chwili prognozy;
- **challenger (tryb cienia)** — wersja z wpisem `challenger_ready`, która nie była serwowana.

Te dwie grupy liczone są osobno. W obrębie roli zostaje jedna prognoza na (rodzina, mecz, rynek).

**Jednostka = mecz.** Selekcje grupowane są według roli, zakresu lig (wszystkie / core /
extended), rodziny modelu, rynku z linią (także „wszystkie rynki razem”) i przedziału szansy
50–60%, 60–70%, 70–80%, 80–90% i 90–100%. Do tego dochodzi cała strefa kuponów ≥ 60%.

- W grupie każdy mecz wnosi **jedną parę**: średnią szansę i średni wynik swoich selekcji z tego
  przedziału. Dwa skorelowane rynki jednego meczu (np. „gospodarz strzeli” i „gospodarz powyżej
  0,5”) dają więc n = 1 (test 1).
- Przedział to Wilson 95% z n = liczba meczów i sukcesami = suma średnich wyników meczów.
- Gdy selekcje meczu są w pełni skorelowane, średnia wynosi 0 albo 1 i przedział jest dokładny.
  Przy słabszej korelacji prawdziwa wariancja jest mniejsza, więc przedział jest konserwatywny
  (szerszy).

**Flagi** (`min_matches = 30` w `config/prospective_policy.toml`):

| Warunek | Flaga |
|---|---|
| n < 30 meczów | „za mało meczów” |
| n ≥ 30 i górna granica przedziału < średnia szansa | „zbyt optymistyczna” |
| n ≥ 30 i dolna granica przedziału > średnia szansa | „zbyt ostrożna” |
| pozostałe | „zgodna z prognozą” |

Flaga nie zmienia prognoz, progów profili ani statusów — test 3 sprawdza liczby wierszy i treść
wszystkich tabel. Przy nodze kuponu, której rynek i przedział mają flagę, pojawia się notka
„prospektywnie ten rynek w tym przedziale trafia rzadziej niż przewiduje model (n=… meczów)”
(przy fladze „zbyt ostrożna”: „częściej”).

**Interfejs i CLI:**
- **Modele → Uczenie AI** i strona **Uczenie AI**, sekcja „Strefa kuponów — kalibracja
  prospektywna”: rynki, które mogą trafić do kuponu (z przedziałem ostrożnym), osobno pozostałe;
  wybór zakresu lig i ról.
- `sports-edge calibration [--scope core|extended] [--min-prob 0.6] [--market]` — wynik w JSON.

**Wyniki z kopii (aktywne wersje, wszystkie ligi, strefa ≥ 60%):**

| Model | Rynek | Mecze | Średnia szansa | Trafność | 95% (mecze) | Ocena |
|---|---|---|---|---|---|---|
| Dixon–Coles | wszystkie rynki | 37 | 81,5% | 81,9% | 66,7–91,1% | zgodna |
| Dixon–Coles | 80–90% (wszystkie rynki) | 37 | 85,2% | 75,6% | 59,8–86,6% | zgodna |
| Dixon–Coles | podwójna szansa | 37 | 73,0% | 78,4% | 62,8–88,6% | zgodna |
| Dixon–Coles | O/U 1,5 | 37 | 75,8% | 78,4% | 62,8–88,6% | zgodna |
| Dixon–Coles | O/U 3,5 | 37 | 70,1% | 67,6% | 51,5–80,4% | zgodna |
| Dixon–Coles | O/U 2,5 | 5 | 64,8% | 60,0% | 23,1–88,2% | za mało meczów |
| Dixon–Coles | 1X2 | 5 | 70,5% | 60,0% | 23,1–88,2% | za mało meczów |
| Elo | wszystkie rynki (1X2 + podwójna szansa) | 38 | 72,7% | 75,0% | 59,4–86,0% | zgodna |
| Elo | podwójna szansa 70–80% | 34 | 73,3% | 86,8% | 71,6–94,5% | zgodna |
| Elo | podwójna szansa 60–70% | 14 | 66,2% | 46,4% | 24,0–70,4% | za mało meczów |

- Osobno dla zakresów strefa ma: core 16 meczów (Dixon–Coles i Elo), extended 21–22 mecze —
  wszędzie „za mało meczów”.
- Challengery w cieniu mają 11 meczów w strefie — również „za mało”.
- Na kopii **0 flag**.

## 3. Porównanie z rynkiem STS (P0)

**Reguła wyboru dokumentu** (`prospective_eval.pre_match_quotes`, testy 4–5):

- brany jest ostatni dokument STS, którego czas z dokumentu (`quote_sets.valid_at`, a gdy go brak
  — czas importu) jest **ściśle przed** `events.start_at` meczu;
- tylko wiersze o statusie `ok`, tylko importy STS `prospective` / `archive`, okres FT, ten sam
  rynek i linia;
- dokument wygenerowany po rozpoczęciu (np. kursy na żywo) nigdy nie jest używany.

Ostatni dokument przed meczem to najlepiej poinformowany kurs przedmeczowy, czyli wzorzec
najostrzejszy dla modelu. Mediana wyprzedzenia na kopii to 4,25 h przed meczem (core 19 h — tam
ostatni dokument był z dnia poprzedniego).

**Usuwanie marży: metoda „power”** — p_i = (1/o_i)^k, a k dobrane bisekcją tak, by suma była
równa 1.

- Metoda proporcjonalna przydziela marżę proporcjonalnie do szansy, więc zaniża szanse faworytów
  (efekt favourite–longshot). Właśnie w strefie kuponów rynek wyglądałby wtedy gorzej, niż jest,
  a model lepiej — „power” jest więc uczciwszym, ostrzejszym wzorcem.
- Wynik metody proporcjonalnej jest zapisywany jako kontrola wrażliwości (kolumna
  `market_log_loss_other_devig`). Na kopii różnice między metodami są rzędu 0,005 log loss.
- Podwójna szansa rynku wynika z oczyszczonego 1X2 STS — tak samo jak w `edge_engine`.

**Miary.**
- Log loss rynku wykluczającego to −ln p(wynik), a Brier to suma kwadratów błędów po wynikach.
  Dla podwójnej szansy (rynek niewykluczający) brana jest średnia z ocen binarnych.
- Model i rynek liczone są na **tych samych prognozach**. Różnica (model − rynek) jest uśredniana
  po meczu, a 95% przedział daje bootstrap po meczach (1000 losowań, ziarno 7).
- W puli „wszystkie rynki” podwójna szansa jest pomijana, bo to ta sama informacja co 1X2.

**Stany** (bez wartości domyślnych):

| Warunek | Stan |
|---|---|
| brak wspólnych meczów | „nieznane (brak wspólnych meczów)” |
| mniej niż 300 meczów | „za mało (n meczów < 300)” |
| górna granica < 0 | „model lepszy” |
| dolna granica > 0 | „model gorszy” |
| pozostałe | „brak istotnej różnicy” |

**Zapis.** Nowa tabela append-only `market_benchmarks` (schemat v9). Identyfikator zależy od
danych i wersji polityki, więc ten sam stan danych nie dodaje drugiego wiersza.
- Wpisy dodaje zadanie automatyzacji „Analiza jakości modeli” i `sports-edge market-benchmark`.
- `validation_json` wersji pozostaje bez zmian.
- Na kopii: 71 grup zapisanych, przy powtórzeniu 0.

**Interfejs.**
- Modele → Podstawowy: kolumna „Porównanie z rynkiem STS” zamiast gołego `beats_market: false`.
- Uczenie AI → „Porównanie z rynkiem STS”: pełna tabela.
- Kurs jest wyłącznie benchmarkiem ex post. Żadna funkcja modelu go nie czyta
  (`docs/leakage_prevention.md`).

**Wyniki z kopii** (aktywne wersje; różnica = log loss modelu − rynku, ujemna = model lepszy):

| Zakres | Model | Rynek | Wspólne mecze | Log loss model / rynek | Różnica (95%) | Stan |
|---|---|---|---|---|---|---|
| wszystkie | Elo | 1X2 | 38 | 0,969 / 0,937 | +0,032 (−0,021…0,081) | za mało (38 meczów < 300) |
| wszystkie | Elo | podwójna szansa | 38 | 0,567 / 0,549 | +0,018 (−0,010…0,044) | za mało |
| wszystkie | Dixon–Coles | 1X2 | 37 | 0,966 / 0,939 | +0,027 (−0,025…0,078) | za mało |
| wszystkie | Dixon–Coles | O/U 1,5 | 36 | 0,530 / 0,535 | −0,005 (−0,063…0,035) | za mało |
| wszystkie | Dixon–Coles | O/U 2,5 | 36 | 0,689 / 0,693 | −0,004 (−0,061…0,047) | za mało |
| wszystkie | Dixon–Coles | O/U 3,5 | 36 | 0,588 / 0,607 | −0,019 (−0,063…0,022) | za mało |
| wszystkie | Dixon–Coles | BTTS | 36 | 0,684 / 0,688 | −0,004 (−0,045…0,039) | za mało |
| wszystkie | Dixon–Coles | wszystkie rynki | 37 | 0,517 / 0,514 | +0,006 (−0,017…0,028) | za mało |
| core | Elo | 1X2 | 16 | 0,987 / 0,914 | +0,073 (+0,028…0,122) | za mało (16 meczów < 300) |
| extended | Elo | 1X2 | 22 | 0,955 / 0,954 | +0,002 (−0,078…0,078) | za mało (22 mecze < 300) |

Żaden rynek nie ma 300 wspólnych meczów, więc nie ma werdyktu. Jedyny przedział w całości powyżej
zera — Elo 1X2 w 5 czołowych ligach na 16 meczach, gorzej od rynku — to sygnał do obserwacji, nie
wniosek.

## 4. Szybsze zbieranie dowodów (P1) — wykonalność

**Pomiar na kopii (01–05.11, jedyny okres z archiwum ofert STS sprzed meczów):**

| | Mecze 12 lig |
|---|---|
| w bezpłatnych źródłach (football-data.org, PitchAPI, openfootball; wynik po meczu) | 59 |
| w ofercie STS (ta sama liga i dzień) | 59 (100%) |
| z prognozą zapisaną przed meczem | 42 |
| bez prognozy | 17 — wszystkie z 01.11, przed startem prognoz (core od 11:37, extended od 17:37) |
| **w źródłach, a nie w ofercie STS** | **0** |

- Na weekend 07–09.11 oferta STS z 06.11 ma 102 mecze 12 lig: core 45, extended 57. To pełne
  kolejki bez meczów piątkowych i poniedziałkowych.
- Koszt zapytań byłby niewielki — terminarz to 1 zapytanie na ligę i okres. Darmowy plan
  football-data.org daje 10 zapytań na minutę, a PitchAPI w aplikacji ma limit 60 zapytań
  statystyk na generowanie.
- Ale zysk wynosi **0 dodatkowych meczów na tydzień**: STS wystawia praktycznie każdy mecz
  obsługiwanych lig.

**Decyzja (§4.3):** prognoz cienia spoza oferty nie zaimplementowano. Wąskim gardłem jest czas
zbierania, nie pokrycie: około 100–115 meczów tygodniowo w 12 ligach (core około 45, extended
około 60), a w przerwie reprezentacyjnej (prawdopodobnie 09–17.11) prawie nic. Szacunkowo:

- 300 wspólnych meczów dla wszystkich lig razem to około 3 tygodnie grania;
- dla samego zakresu core — około 7 tygodni;
- dla rynków o niskiej liczności (np. O/U 2,5 w strefie ≥ 60%: 5 na 36 meczów) — miesiące.

Propozycja na TASK 012 jest w §10.

## 5. Proces challengerów (P1)

- **Panel decyzji** (Modele → Uczenie AI i strona Uczenie AI, per zakres): champion i challenger
  obok siebie.
  - Okno selekcji (n, log loss, ECE), holdout (tylko raport) i statusy rynków — także Elo, po
    poprawce `_decision` (`retraining.elo_reports`).
  - Liczba wspólnych rozliczonych **meczów** i różnica log loss z przedziałem bootstrap po meczach.
  - Stan: „czeka na dowody (n/300)”, „dowody wystarczające — decyzja należy do Ciebie” albo
    „identyczny z championem w oknie selekcji”.
  - Werdykt w opisie dopiero od 300 wspólnych meczów (`decision_matches` w polityce). Wcześniej
    próg wynosił 100 selekcji.
  - Lista wersji pokazuje rolę „wygasła (brak dowodów)” i zwalidowane rynki Elo.
- **Wygasanie** (`retraining.expire_challengers`, wywoływane przez przegląd):
  - challenger zastąpiony nowszym z tego samego zakresu i rodziny (nie dostaje już prognoz cienia)
    albo starszy niż 14 dni bez żadnego wspólnego rozliczonego meczu dostaje nowy wpis
    `model_events` „Wygasły (brak dowodów)”;
  - stare wpisy zostają bez zmian; zapis jest idempotentny;
  - wygasły challenger nie blokuje treningu i **nigdy nie jest aktywowany**;
  - challenger, który zbiera dowody, nie wygasa po 14 dniach — inaczej przy około 45 meczach
    tygodniowo w core nigdy nie doszedłby do 300.
- **Brak treningu na tych samych danych.** Gdy aktywna wersja jest z protokołu v2, a od jej
  treningu nie ma nowego meczu, `run_protocol_v2` / „Trenuj teraz” odmawia.
  - Trening możliwy jest tylko po zaznaczeniu „Wymuś trening…”, w CLI przez `research protocol-v2
    --force` albo `learning review --force`.
  - Wymuszony challenger ma w zdarzeniu `forced: true`, `new_matches: 0` i powód „WYMUSZONY
    TRENING…”. Liczba nowych meczów jest zapisywana zawsze.

**Stan na kopii po zmianach** (przegląd uruchomiony na kopii, trening wyłączony):

- Core:
  - para challengerów z 02.11 (Elo i Dixon–Coles) wygasła jako zastąpiona;
  - para z 06.11 10:43 czeka w stanie „identyczny z championem w oknie selekcji (wspólne mecze:
    0)”;
  - status zakresu: „Challenger gotowy”;
  - jeśli do 20.11 nie będzie żadnego wspólnego rozliczonego meczu, para wygaśnie.
- Extended: para z 06.11 10:40 jest w tym samym stanie.
- Trening blokują też pozostałe warunki: 9 / 20 nowych meczów < 150 oraz „ostatni trening mniej
  niż 7 dni temu”.

Ponieważ te challengery są identyczne z championami, ich prognozy cienia będą takie same, a różnica
wyniesie 0. Decyzja („POZOSTAW POPRZEDNI MODEL” albo czekanie) należy do użytkownika — system jej
nie podejmuje.

## 6. Ryzyko dnia jako liczba (P2)

Blok „Ryzyko dnia” w Kuponie dnia podaje teraz trzy liczby:

- szansę, że przegrają **co najmniej dwa** kupony dnia;
- oczekiwaną liczbę przegranych kuponów;
- tę samą szansę przy założeniu niezależnych kuponów — dla porównania, gdy kupony mają wspólne
  selekcje.

**Metoda** (`insights.day_risk`):
- Liczone dokładnie, przez włączanie-wyłączanie po kuponach. P(zbiór kuponów wygrywa) to iloczyn
  szans ostrożnych po **sumie** ich selekcji, więc wspólna selekcja to jedno zdarzenie.
- Jawne założenia: różne selekcje są niezależne. Dla różnych meczów wynika to z założenia modelu,
  a dla różnych typów jednego meczu to przybliżenie — wtedy blok o tym mówi.
- Bez szansy ostrożnej używana jest szansa modelu, co blok podaje. Brak rekomendacji stawek.

**Przykłady:**
- Testy: kupon A = {m1 80%}, B = {m1 80%, m2 90%}. P(≥ 2 przegrane) = 20,0% (a nie 5,6% przy
  niezależności), oczekiwana liczba przegranych 0,48. Drugi test sprawdza wzór pełnym
  wyliczeniem 2⁴ wyników.
- Kopia, 02.11: 5 kuponów z 3 selekcjami (wspólne 2). P(≥ 2 przegrane) = 42,9% wobec 57,9% przy
  niezależności, oczekiwana liczba przegranych 1,75 z 5.
- Kopia, 06.11: 2 kupony bez wspólnych selekcji — 31,7%, oczekiwana liczba 1,13.

## 7. Zmiany w kodzie i danych

- `src/sports_edge/prospective_eval.py` (nowy): prognozy rozliczone z rolą, niezawodność na
  meczach, flagi, notka przy nodze, ceny STS sprzed meczu, „power” i proporcjonalne usuwanie marży,
  porównanie z rynkiem, zapis do `market_benchmarks`, porównanie champion–challenger na meczach.
- `config/prospective_policy.toml` (nowy, wersja `2026-11-06.e1`): N = 30, strefa 0,6, przedziały,
  próg 300 meczów, devig, bootstrap, 14 dni. `probability_policy.toml` jest **bez zmian** — progi
  profili, polityka walidacji i statusy nietknięte.
- `storage.py`: `SCHEMA_VERSION = 9` i `MIGRATIONS[9]` — jedna nowa tabela append-only
  `market_benchmarks` z triggerami `immutable_*`. Migracja z kopią `pre-migration-v9_*.db`.
- `learning.py`: rodzaj zdarzenia `expired` (zamyka challengera, nie jest rolą).
- `retraining.py`: `elo_reports`, poprawiony `_decision`, `identical_in_selection`,
  `evidence_state`, `expiring` / `expire_challengers`, porównanie na meczach. `scope_status` bierze
  pod uwagę wygasłych.
- `services.py`: blokada treningu bez nowych meczów (`force`), rola „wygasła”, zwalidowane rynki
  Elo w liście wersji.
- `insights.day_risk`.
- `edge_engine`: zamiast „rynek: False” pokazuje „nieznane”.
- Interfejs: `page_learning`, `page_models`, `pages_models`, `coupon_view`, `page_my_coupon`,
  `page_generator`, `page_coupon`, `common` (cache oceny).
- Starsza tabela „Kalibracja wg przedziału szansy” w Jakości prognoz jest teraz opisana jako
  liczona na selekcjach i nie pokazuje flag. Wcześniej oznaczała 80–90% jako „zbyt pewny” na 323
  zależnych selekcjach, a ocena na meczach tego nie potwierdza.
- `cli.py`: polecenia `calibration` i `market-benchmark`, `research --force`.
- `automation.job_quality` zapisuje porównanie z rynkiem (`write_runtime` nietknięty).
- `qa.py`: nowa ścieżka „modele: strefa kuponów i rynek STS”.

**Migracja na kopii prawdziwej bazy:**
- przejście v8 → v9 utworzyło kopię `pre-migration-v9_…db`;
- liczby wierszy wszystkich 44 tabel są bez zmian;
- żaden istniejący rekord nie został zmieniony ani usunięty (triggery `immutable_*`, test 3).

## 8. Testy i kontrole

- `tests/test_task011.py` — 15 testów:
  - mecze zamiast selekcji;
  - flagi i notka;
  - flaga niczego nie zmienia;
  - kurs tylko sprzed meczu, szanse bez marży sumują się do 1, „nieznane” zamiast „false”;
  - próg 300 meczów;
  - wygasły challenger nie blokuje, brak automatycznej aktywacji, statusy Elo, challenger
    identyczny i zastąpiony, dowody w meczach (n/300);
  - brak treningu na tych samych danych bez wymuszenia;
  - Ryzyko dnia (wspólna selekcja, pełne wyliczenie, tekst w Kuponie dnia);
  - migracja v8 → v9;
  - AppTest Modeli.
- Testy prognoz cienia spoza oferty pominięto, bo tej funkcji nie zaimplementowano (§4.3).
- `tests/test_task010.py`: test migracji v8 sprawdza teraz `SCHEMA_VERSION >= 8` i nazwę kopii z
  bieżącą wersją. Test nie jest wyłączony.
- `tests/test_ui_paths.py`: oczekuje nowej ścieżki QA.
- Wyniki (06.11.2026; kod z worktree przez absolutny `PYTHONPATH=<worktree>\src`, bo
  edytowalna instalacja w `.venv` wskazuje na główny checkout):

| Kontrola | Wynik |
|---|---|
| `pytest -q` | **367 passed, 4 skipped** (371 testów; pominięte: brak lokalnych autentycznych PDF STS), 255 s |
| `ruff check .` | All checks passed |
| `ruff format --check .` | wszystkie pliki sformatowane |
| `python -m sports_edge.qa` (ścieżki UI, DEMO) | 26 / 26 OK, w tym nowa „modele: strefa kuponów i rynek STS” |
| `python -m sports_edge.qa_visual` (Playwright, DEMO) | 83 kontrole (14 stron × 5 rozdzielczości: 1920, 1366, 1024, 768, 390 px + 13 scenariuszy, w tym „Kupon dnia: Ryzyko dnia”), **0 błędów** |
| `python -m sports_edge.qa_browser` (Playwright, DEMO) | 22 zrzuty, 0 błędów |
| `qa_visual --home <kopia>`: Modele, Kupon dnia, Uczenie AI w 5 rozdzielczościach | 15 kontroli, 0 błędów; po poprawce kolumny rynku powtórka w 1366 i 390 px: 6 kontroli, 0 błędów |

- Zrzuty z kopii prawdziwej bazy obejrzano ręcznie. **Nie dodano ich do repozytorium** i zostały
  usunięte razem z kopią.
- Przegląd ręczny wychwycił błąd: w Modele → Podstawowy wiersz „Podwójna szansa” pokazywał
  „nieznane”, bo klucz `double_chance` był zamieniany na „double chance”. Błąd poprawiono, a test
  UI sprawdza teraz stan każdego wiersza tabeli.
- Na kopii Kupon dnia (06.11, 2 kupony) pokazuje: szansa przegranej co najmniej dwóch kuponów
  31,7%, oczekiwana liczba przegranych 1,13 z 2. Blok dodaje uwagę o przybliżeniu dla dwóch różnych
  typów tego samego meczu.
- Na DEMO „Ryzyko dnia” pokazuje 54,0% (przy niezależności 41,8%) i oczekiwaną liczbę przegranych
  kuponów 1,31 z 2. To zgadza się z rachunkiem ręcznym: wspólny typ ma szansę ostrożną 46%, a drugi
  typ 49%.

## 9. Ograniczenia

- **Mała próba:** 38 meczów prospektywnie, 11 w cieniu. Wszystkie stany rynku to „za mało”,
  prawie wszystkie komórki „za mało meczów”. To wstępny obraz, nie wynik.
- Przedział Wilsona na średnich meczu jest konserwatywny przy słabo skorelowanych rynkach meczu.
  Flaga pojawi się więc później, niż pojawiłaby się przy niezależnych selekcjach (świadomy wybór).
- Mecze różnych dni nie są w pełni niezależne (ta sama kolejka, pogoda, sędziowanie) — bootstrap
  po meczach tego nie uwzględnia.
- Kurs STS z ostatniego dokumentu bywa sprzed wielu godzin (core: mediana 19 h). Rynek tuż przed
  meczem mógł być lepszy. Porównanie jest więc łagodniejsze dla modelu, niż byłoby z kursem
  zamknięcia, którego legalnie nie ma.
- „Ryzyko dnia” traktuje różne typy jednego meczu jak niezależne. To przybliżenie, a blok mówi,
  kiedy ono występuje.
- Pomiar wykonalności §4 obejmuje 5 dni (archiwum STS od 31.10) i dużo meczów w środku tygodnia.
  Weekendowa oferta (102 mecze 12 lig) go potwierdza.

## 10. Proponowany TASK 012

1. **Retrening, który może się zmienić:** ruchome okno selekcji i kalibracji (np. ostatnie 12
   miesięcy przed treningiem) albo rekalibracja na prospektywnych rozliczeniach, gdy jest ich
   ≥ 300 meczów. Bez tego każdy challenger z protokołu v2 jest identyczny z championem (§1.4).
2. **Dowody szybciej, bez nowych źródeł:** ocena „pseudo-prospektywna” kolejki weekendowej —
   prognozy zapisywane automatycznie wcześnie rano w dniu meczu, żeby żaden mecz oferty nie został
   bez prognozy. Do tego raport tygodniowy kalibracji strefy (zadanie automatyzacji z
   powiadomieniem przy pierwszej fladze).
3. **Porównanie z rynkiem bliżej zamknięcia:** więcej pobrań oferty STS w dniu meczu (w limicie
   raz na 10 minut na plik), aby ostatni dokument był z ostatnich 1–3 godzin. Do tego raport
   wrażliwości wyprzedzenia kursu.
4. **Łączny rozkład wyniku dla „Ryzyka dnia”:** dla typów jednego meczu z macierzy wyników
   Dixon–Coles zamiast założenia niezależności.
5. Po 300 meczach: decyzja użytkownika o statusach rynków, którym porównanie z rynkiem przypisze
   „model gorszy” — tylko na wniosek, bez automatycznych zmian.
