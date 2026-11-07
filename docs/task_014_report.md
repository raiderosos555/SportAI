# Raport TASK 014 — trafność kuponów: oczekiwane kontra faktyczne, bezpieczniejszy kupon i mniej wspólnych porażek

Data pracy: 07.11.2026. Kod bazowy: `c3ffb85` (TASK 013, poprawka workera `e60f672`). Wszystkie
pomiary na prawdziwych danych wykonano na **kopii** prywatnej bazy (`storage.snapshot_file`,
katalog tymczasowy, kopia z 07.11 ok. 23:13); po pomiarach kopię usunięto. Prawdziwa baza nie
była otwierana nowym kodem. W raporcie są wyłącznie liczby zbiorcze — bez nazw meczów i bez
nazw z kuponów. Raport nie zawiera rekomendacji zysku ani stawek. Żaden model nie został
aktywowany, bramka v3 nie została zaakceptowana w Twoim imieniu, progi istniejących profili,
polityka walidacji i definicja szansy ostrożnej są bez zmian.

## 0. Najważniejsze w skrócie

- **Pech czy błąd modelu? Pech, w granicach normy.** 7 z 14 rozliczonych kuponów z szansą
  modelu trafiło przy **6,94 oczekiwanych** (ostrożnie 5,90). Taki lub gorszy wynik zdarza się
  w **62%** przypadków, taki lub lepszy w 60% — „zgodne z modelem”. Nogi: 30 różnych selekcji,
  24 trafione (80,0%) przy średniej szansie 78,0%; na meczach 21,5 z 27 przy 20,9 oczekiwanych.
  Każdy z 8 przegranych kuponów odpadł przez **jedną** nogę.
- **Panel „Oczekiwane kontra faktyczne”** w Historii (zakładka „Skuteczność kreatora kuponów”):
  per profil i łącznie, P(wynik ≤ faktyczny) i P(wynik ≥ faktyczny) z rozkładu
  Poissona-dwumianowego, zdanie po ludzku i oznaczenie „gorzej / lepiej niż przewiduje model”
  przy 5% po jednej stronie; osobno kupony bez szansy modelu, tabela nóg według rynku na meczach,
  linia o wspólnych porażkach. Na Dashboardzie jedna linia: „Ostatnie 30 dni: 7 z 14 trafionych,
  model oczekiwał 6,9 — zgodnie z modelem (bez szansy modelu: 1, poza obliczeniem).”
- **Bezpieczniejszy kupon zmierzony na tych samych 24 decyzjach** (31.10–07.11, godz. 10/14/18):
  SOLO z ostrożną szansą ≥ 0,75 (zwalidowany) dał 2 kupony w 8 dni (śr. szansa 84,8%,
  ostrożnie 77,4%, kurs 1,19) — **oba przegrały** (P = 2,3% — dwie obserwacje). Wariant
  bez wymogu walidacji: 4 kupony, 3 trafione (wszystkie eksperymentalne). Obecna polityka:
  7 kuponów, 3 trafione (oczekiwane 3,4).
- **Nowy profil „PEWNY SOLO”** (polityka `2026-11-07.p4`): jedna selekcja, ostrożnie ≥ 75%, kurs
  od 1,15, zysk netto po podatku > 0, walidacja predykcyjna — **domyślnie wyłączony**
  (`enabled = false`). Włączasz go w Źródła i ustawienia → Ustawienia. Budowany po czterech
  profilach — ich kupony i progi się nie zmieniają (test regresji). Karta każdego profilu mówi
  wprost: „Szansa X% — statystycznie przegrywa Y z 10 takich kuponów; wypłata Z zł”.
- **Wspólne porażki:** w 1 z 5 dni z rozliczeniami (07.11) dwie przegrane nogi położyły 4
  kupony. Limit 1 w replayu: tyle samo kuponów (7), średnia szansa ostrożna 41,3% → 41,1%, dni
  z 2+ porażkami od jednej nogi 1 → 0. Domyślne ustawienie bez zmian; „Ryzyko dnia” ma teraz
  bezpośredni odnośnik do limitu, a Mój kupon ostrzega o nodze z kuponu oficjalnego dnia,
  wskazuje najsłabszą nogę i od 4 nóg mówi „przegrywa średnio Y z 10”.
- **Świeże kursy: za mało danych.** Twoja aplikacja i worker działają na kodzie TASK 012/013 od
  07.11 ok. 22:54 (baza v10, pierwsze wpisy `forecasts` i `calibration_report`), ale nie było
  jeszcze żadnego pobrania „w dniu meczu” (0 z 8 dni; potrzeba 3). **Jedna instrukcja:**
  po tej aktualizacji uruchom ponownie aplikację i workera i zostaw je włączone przez co najmniej
  3 dni z meczami.
- **Pułap w niedziele bez zmian (12):** przyczyną utraty pobrania 45 min przed meczem (1 grupa,
  2 mecze w każdą z niedziel 01.11 i 07.11) była etykieta scalonego slotu, nie pułap. Po
  poprawce 0 meczów traci to pobranie.
- **Pokrycie dolnej granicy na historii** (as-of, 07.2024–11.2026, 3941 meczów 5 lig i 5572
  meczów 7 lig): każda definicja „ostrożnie” trzyma się w strefie ≥ 0,75. Dzisiejsza granica Elo
  dla podwójnej szansy (suma dwóch przedziałów) ma zapas +10,3 pkt (5 lig) i +15,2 pkt (7 lig);
  opcja D (jeden przedział) +2,8 i +2,4 pkt (cały przedział 95% nad granicą) i obejmuje o 53%
  (5 lig) i 5,2 razy (7 lig) więcej meczów. Dane do Twojej decyzji — definicja bez zmian.
- **Team totals w nowych wersjach (v3) są kalibrowane** jak pozostałe rynki Dixon–Coles; championy
  i stare wersje bez zmian. W strefie ≥ 60% linia 1,5 nadal jest „zbyt optymistyczna”, ale luka
  maleje o połowę lub więcej (np. gospodarz 1,5 w 7 ligach −3,7 pkt → −1,2 pkt, „zgodna”).

## 1. Diagnoza (07.11.2026, kopia z ok. 23:13, kod `c3ffb85`)

1. **Kupony:** 15 rozliczonych kuponów liczonych w statystykach (oficjalne kupony dnia i Twoje,
   bez duplikatów i wersji), 7 trafionych. 14 ma szansę modelu: oczekiwane trafienia 6,94
   (ostrożnie 5,90), P(≤ 7) = 0,62, P(≥ 7) = 0,60. Jeden kupon użytkownika (02.11) nie ma szansy
   modelu — pokazany osobno, poza obliczeniami.

   | Profil | Rozliczone | Trafione | Oczekiwane (model) | Ocena |
   |---|---|---|---|---|
   | STANDARDOWY | 3 | 1 | 1,78 | zgodne (taki lub gorszy: 36%) |
   | WYŻSZA WYPŁATA | 5 | 3 | 2,29 | zgodne (taki lub lepszy: 42%) |
   | TASIEMKA | 1 | 0 | 0,21 | zgodne (79%) |
   | MÓJ KUPON (z szansą) | 2 | 0 | 0,73 | zgodne (40%) |
   | profile sprzed TASK 007 (3 kupony) | 3 | 3 | 1,93 | zgodne |
   | **Łącznie** | **14** | **7** | **6,94** | **zgodne (62% / 60%)** |

2. **Nogi** (każda selekcja raz, nogi jednego meczu w grupie = jedna obserwacja):

   | Rynek | Nogi | Mecze | Trafione (na meczach) | Oczekiwane |
   |---|---|---|---|---|
   | Gole: powyżej | 11 | 10 | 8,0 | 7,8 |
   | Gole: poniżej | 8 | 8 | 6,0 | 6,1 |
   | Podwójna szansa | 11 | 11 | 9,0 | 8,6 |
   | **Łącznie** | **30** | **27** | **21,5** | **20,9** |

   Diagnoza zadania liczyła nogi z powtórzeniami (45 nóg, 37 trafionych) — ta sama selekcja w
   kilku kuponach to jedna obserwacja, stąd 30.
3. **Porażki z jednej nogi:** 8 z 8 przegranych kuponów przegrało przez dokładnie jedną nogę.
   W 1 z 5 dni z rozliczeniami (07.11) dwie przegrane selekcje były w 2+ kuponach: 4 kupony.
4. **WYSOKA TRAFNOŚĆ** (`simulation.high_hit_diagnosis`, 24 decyzje 31.10–07.11, godz.
   10/14/18): **0 kuponów**. Brak kandydatów 16 (8 — brak typów z prognozą w przedziale kursu:
   31.10 przed pierwszym dokumentem STS, 01.11 o 10 i 14, 05.11; 6 — kurs starszy niż 3 h;
   2 — mecze już trwały), niska szansa 4, wypłata 3, szansa kuponu 1. Zwalidowane typy w przedziale z
   ostrożną ≥ 0,75: 0 (w 0,70–0,75: 6). Opcja D (pomiar): 1 decyzja z kuponem (76,9%) — jak w
   TASK 013 (tam 12 z 20 decyzji bez kandydatów; nowe 4 decyzje to 31.10 i 07.11 18:00).
5. **Kod aplikacji użytkownika:** baza w schemacie v10; w `job_runs` 2 wpisy `forecasts` i 1
   `calibration_report` (07.11 ok. 22:55), worker uruchomiony ponownie 07.11 ok. 22:54. Pobrania
   „w dniu meczu”: 0.
6. **Challenger v3:** w bazie nie ma żadnej wersji v3 (pomiar „w cieniu” niemożliwy — §5.3).
7. **Rozliczone mecze aktywnych wersji** (wariant B, próg 300): **37 (5 lig), 57 (7 lig)** —
   w TASK 013 było 20 i 32.

## 2. Panel „Oczekiwane kontra faktyczne” (P0)

- **Gdzie:** Historia i skuteczność → zakładka „Skuteczność kreatora kuponów”, na górze
  (`pages_history.expected_panel`). Tylko odczyt: nic nie zmienia prognoz, progów ani statusów.
- **Kupony** (`performance.expected_vs_actual`): kupony liczone w statystykach (TASK 010 —
  `builder_coupons`, bez duplikatów i wersji), rozliczone na wygraną lub przegraną. Per profil i
  łącznie: rozliczone, oczekiwane z szansy modelu i z ostrożnej, trafione, P(≤ faktyczny),
  P(≥ faktyczny), zdanie, np. „zgodne z modelem (taki lub gorszy wynik zdarza się w 62%
  przypadków)”.
- **Oznaczenia:** P(≤ faktyczny) < 5% → „gorzej niż przewiduje model”; wynik powyżej 95.
  percentyla (P(wynik < faktyczny) > 95%, tj. P(≥ faktyczny) < 5%) → „lepiej niż przewiduje
  model”. Tak jeden trafiony kupon nigdy nie jest „lepiej” tylko dlatego, że P(≤ 1) = 1.
- **Rozkład:** Poissona-dwumianowy liczony dokładnie (programowanie dynamiczne), kupony jako
  niezależne. Kupony bez szansy modelu są w osobnej linii „Bez szansy modelu” i nie wchodzą do
  obliczeń. Unieważnienia i zwroty nie są ani trafieniem, ani porażką.
- **Nogi** (`performance.legs_expected_vs_actual`): każda selekcja raz; w grupie rynku (gole:
  powyżej/poniżej, podwójna szansa, 1X2, obie strzelą, gole drużyny) nogi jednego meczu to jedna
  obserwacja ze średnią szansą i średnim wynikiem (jak TASK 011). Trafienia ułamkowe
  zaokrąglamy ku środkowi w obu ogonach (P(X ≤ ⌈k⌉), P(X ≥ ⌊k⌋)) — żadne oznaczenie nie
  wynika z zaokrąglenia.
- **Wspólne porażki** (`performance.shared_losses`): jedna linia pod tabelą nóg.
- **Dashboard** (`performance.recent_line`): jedna linia w sekcji „Kupony oczekujące na
  rozliczenie”: ostatnie 30 dni, trafione z liczby z szansą modelu, oczekiwane, ocena.
- **Na kopii:** liczby z §1.1–1.3 (panel pokazuje je 1:1).

## 3. Bezpieczniejszy kupon: pomiar i profil PEWNY SOLO (P0)

### 3.1 Pomiar (`simulation.variant_replay`, CLI `sports-edge hit-rate`)

Replay as-of wszystkich dni archiwum (31.10–07.11; 08.11 jeszcze bez meczów) o 10, 14 i 18
czasu polskiego — **24 decyzje, te same dla każdego wariantu**. W każdej chwili tylko oferta i
kursy dokumentów sprzed decyzji oraz prognozy wersji serwowanych w tej chwili; kreator raz na
wariant; kupon oficjalny = pierwszy kupon wariantu, profilu i dnia (reguła „first”, TASK 010),
późniejsze to wersje. Rozliczenie wynikami zapisanymi później. Nic nie jest zapisywane.

| Wariant | Kupony | Dni z kuponem / bez | Kupony na dzień | Zwalid. | Śr. szansa model / ostrożnie | Trafione kupony (95%) | Trafione nogi (95%) | Śr. kurs | Śr. wypłata (10 zł) | Zysk netto razem* |
|---|---|---|---|---|---|---|---|---|---|---|
| (a) obecna polityka | 7 | 4 / 4 | 0,88 | 3 | 49,2% / 41,3% | 3 z 7 (16–75%), oczek. 3,4 | 14 z 18 (55–91%) | 3,15 | 27,73 zł | −8,87 zł |
| (a) obecna, limit 1 | 7 | 4 / 4 | 0,88 | 3 | 49,0% / 41,1% | 3 z 7 (16–75%) | 15 z 19 (57–91%) | 3,18 | 28,00 zł | −8,87 zł |
| (b) SOLO, ostrożnie ≥ 0,70 | 2 | 2 / 6 | 0,25 | 2 | 84,8% / 77,4% | **0 z 2** (0–66%), oczek. 1,7 | 0 z 2 | 1,19 | 10,47 zł | −20,00 zł |
| (b) SOLO ≥ 0,70, bez wymogu walidacji | 4 | 4 / 4 | 0,50 | 0 | 82,0% / 76,4% | 3 z 4 (30–95%) | 3 z 4 | 1,20 | 10,54 zł | −8,76 zł |
| (b) SOLO, ostrożnie ≥ 0,75 | 2 | 2 / 6 | 0,25 | 2 | 84,8% / 77,4% | **0 z 2** (0–66%) | 0 z 2 | 1,19 | 10,47 zł | −20,00 zł |
| (b) SOLO ≥ 0,75, bez wymogu walidacji | 2 | 2 / 6 | 0,25 | 0 | 84,2% / 79,6% | 2 z 2 (34–100%) | 2 z 2 | 1,19 | 10,47 zł | +0,94 zł |
| (c) do 2 nóg, ostrożnie kuponu ≥ 0,60 | 2 | 2 / 6 | 0,25 | 2 | 84,8% / 77,4% | 0 z 2 | 0 z 2 | 1,19 | 10,47 zł | −20,00 zł |
| (c) bez wymogu walidacji | 4 | 4 / 4 | 0,50 | 0 | 82,0% / 76,4% | 3 z 4 | 3 z 4 | 1,20 | 10,54 zł | −8,76 zł |

\* Zysk netto po współczynniku 0,88 (12% podatku od stawki) — **wyłącznie informacyjnie, bez
rekomendacji**. Przy kursie 1,15–1,25 wygrana daje 0,12–1,00 zł na 10 zł, przegrana zabiera
10 zł: trafność ≠ zysk.

- **Co mówią liczby:** zwalidowane typy SOLO to w tych dniach wyłącznie podwójna szansa Elo
  (5 lig, kurs 1,15 i 1,23, ostrożnie 77–78%). Oba przegrały — P(0 z 2) = 2,3%, czyli formalnie
  „gorzej niż przewiduje model”, ale to dwie obserwacje (na historii ta sama granica trzyma się
  z zapasem, §5.2). Typy eksperymentalne SOLO to gole „powyżej” Dixon–Coles w 7 ligach.
- **(b) 0,70 = 0,75 i (c) = (b):** w każdej decyzji najlepszy zwalidowany typ miał ostrożną szansę
  ≥ 0,75, a połączenie dwóch nóg zawsze miało niższą szansę niż jedna noga.
- **Status walidacji:** w obecnej polityce 3 z 7 kuponów zwalidowane (8 z 18 nóg), w wariantach
  bez wymogu walidacji 0 z 4 (wszystkie nogi eksperymentalne).

### 3.2 Wybór profilu i opcja

- **Wybrany wariant:** (b) SOLO, ostrożna szansa ≥ 0,75, kurs od 1,15, zysk netto > 0,
  **z walidacją predykcyjną** — jako profil `safe_solo` „PEWNY SOLO”.
- **Dlaczego:** na tych samych decyzjach 0,70 i 0,75 dają te same kupony, więc wyższy próg nic
  nie kosztuje i zostawia większy margines (ten sam próg nogi co WYSOKA TRAFNOŚĆ); wariant (c)
  niczego nie dodaje. Walidacja zostaje, bo kandydaci zwalidowani istnieją (2 kupony w 8 dni) —
  warunek „bez walidacji nie ma kandydatów” nie zachodzi. Wynik 0 z 2 jest zbyt małą próbą, by
  cokolwiek zmieniać; profil to opcja, nie rekomendacja.
- **Polityka `2026-11-07.p4`** (`config/probability_policy.toml`): sekcja `[profiles.safe_solo]` z
  `enabled = false`. Loader trzyma profile wyłączone w `ProbabilityPolicy.optional`, więc
  `profiles` to nadal cztery profile p3. `ProbabilityPolicy.with_profiles` i
  `services.coupon_rules` dokładają włączone profile **po** czterech (różnicowanie liczy się w
  kolejności, więc kupony czterech profili są identyczne — test regresji z limitem 0 i 1).
- **Włączenie:** Źródła i ustawienia → Ustawienia → przełącznik „Profil PEWNY SOLO — kupon
  pojedynczy o wysokiej szansie (opcjonalny)” i „Zapisz ustawienia” (`enabled_profiles` w
  `data/settings.json`; brak pola = wyłączony). Kreator, Kupon dnia, Dashboard i replay
  historyczny używają wtedy pięciu profili; „Kupon na kurs” zostaje przy czterech.
- **Uwaga do wersji polityki:** tożsamość kuponu zawiera wersję polityki (TASK 010). Pierwsza
  analiza dnia po aktualizacji z tymi samymi nogami zapisze się jako rewizja kuponu oficjalnego
  (nie liczona w statystykach) — kupon oficjalny dnia się nie zmienia.

### 3.3 Karta profilu

Pod kafelkami każdej karty (`coupon_view.loss_text` → `insights.loss_line`): „Szansa 85%
(ostrożnie 77%) — statystycznie przegrywa 1,5 z 10 takich kuponów (ostrożnie 2,3); wypłata
10,47 zł”. Bez szansy modelu: „Szansa nieznana … nie da się powiedzieć, ile takich kuponów
przegrywa”.

## 4. Mniej wspólnych porażek (P1)

### 4.1 Pomiar

- **Rozliczone dni (kopia):** 1 z 5 dni miał jedną przegraną nogę w 2+ kuponach (07.11: dwie
  takie nogi, 4 kupony).
- **Replay as-of tych samych 24 decyzji z limitem 1** („ta sama selekcja najwyżej w 1 kuponie
  dnia”): kupony 7 → 7, średnia szansa ostrożna 41,3% → 41,1% (model 49,2% → 49,0%), dni z
  2+ porażkami od jednej nogi **1 → 0**, dni ze wspólną selekcją 1 → 0, trafione 3 z 7 w obu.

### 4.2 Ryzyko dnia

Gdy **ta sama selekcja** (nie tylko ten sam mecz) jest w 2+ kuponach dnia, pod blokiem
„Ryzyko dnia” jest odnośnik „Ustaw limit „ta sama selekcja najwyżej w N kuponach dnia” w
Ustawieniach” — otwiera stronę Źródła i ustawienia od razu w widoku „Ustawienia”
(`?widok=Ustawienia`). Ustawienie domyślne się nie zmienia (wyłączone) — decydujesz Ty.

### 4.3 Mój kupon

- Noga, która jest już w kuponie oficjalnym dnia: „<mecz> — <typ>: ta noga jest też w
  STANDARDOWY — jej porażka przegra oba” (przy kilku kuponach: „przegra wszystkie (N)”).
- Od 4 nóg: „Szansa X% (ostrożnie Y%) — przegrywa średnio Z z 10 (ostrożnie W)”.
- „Najsłabsza noga: <mecz> — <typ> (ostrożnie X%)” — najniższa szansa ostrożna (nogi bez szansy
  nie są porównywane). Funkcje w `my_coupon.py` (`shared_with_official`, `weakest_leg`,
  `chance_note`).

## 5. Dane do decyzji o WYSOKIEJ TRAFNOŚCI (TASK 013 §11)

### 5.1 Świeże kursy

- **Stan: za mało danych (0 dni z pobraniami „w dniu meczu” z 8 dni archiwum; potrzeba 3).**
  Aplikacja działa na kodzie TASK 012/013 od 07.11 ok. 22:54 (ślady: schemat v10, wpisy
  `forecasts` i `calibration_report`), a pierwsze sloty dnia meczu wypadają dopiero 08.11.
  Diagnoza na dniach ze świeżymi kursami nie była możliwa (0 takich dni) — powtórzona na
  wszystkich 24 decyzjach w §1.4 (16 z 24 bez kandydatów; w TASK 013 12 z 20).
- **Jedna instrukcja:** po tej aktualizacji (`G:\SportAI` zaktualizowany) **uruchom ponownie
  aplikację i workera i zostaw je włączone przez co najmniej 3 dni z meczami** — raport
  tygodniowy zmierzy wiek kursów sam.
- **Wiek kursów dotąd** (127 meczów 12 lig, 31.10–07.11): mediana 4,58 h, ≤ 3 h: 27,6%;
  pobrania automatyzacji 12 (0 „w dniu meczu”), 0–3 dziennie.
- **Pułap w niedziele** (`automation.matchday_cap_effect`): 01.11 i 07.11 po 11 grup meczów
  (35 i 54 mecze), 21 slotów bez pułapu, 12 z pułapem. Przed poprawką w obie niedziele **1 grupa
  (2 mecze) traciła pobranie 45 min przed startem** — nie przez sam pułap: dwa sloty bliżej niż
  10 min (45 min przed grupą A i 120 min przed grupą B) scalały się w jeden z etykietą „120 min”,
  więc pułap (który zostawia najpierw sloty najbliżej startu) odcinał właśnie ten. Poprawka:
  scalony slot ma odstęp do **swojej** (wcześniejszej) grupy. Po niej: 0 meczów traci pobranie
  45 min przed startem, pułap **12 bez zmian** (podnoszenie nie było potrzebne; limit „raz na
  10 minut na plik” bez zmian).

### 5.2 Pokrycie dolnej granicy na historii (dane do opcji D)

Walk-forward TASK 012/013 (`sports-edge research walk-forward-v3`, retrening v3 co miesiąc na
danych sprzed miesiąca; zamrożony champion v2 na tych samych meczach, siły drużyn as-of):
07.2024–11.2026, **3941 meczów (5 lig) i 5572 (7 lig)**. Granica liczona dokładnie jak w
produkcji (`sportsdata.elo_interval`, `calibrated_market`, `derived_double_chance`); opcja D =
dokładnie „co-jeśli” z `simulation.option_d`. Jednostka: mecz (typy jednego meczu w przedziale =
jedna obserwacja), przedział Wilsona 95% na meczach. Granica „trzyma się”, gdy częstość ≥ średnia
granica. Poniżej wersja serwowana (v2), strefa granicy ≥ 0,75 (próg nogi WYSOKIEJ TRAFNOŚCI):

| Definicja (poziom nominalny) | Zakres | Mecze | Śr. granica | Śr. szansa modelu | Częstość (95%) | Zapas |
|---|---|---|---|---|---|---|
| Elo, podw. szansa — suma dwóch granic, dziś (każda wynik − max(ECE, 1,96·√(p(1−p)/n)), razem ostrożniej niż 97,5%) | 5 lig | 1202 | 0,779 | 0,863 | 0,881 (0,862–0,899) | **+0,103** |
| | 7 lig | 365 | 0,766 | 0,898 | 0,918 (0,885–0,942) | **+0,152** |
| Elo, podw. szansa — jeden przedział, opcja D (jednostronnie 97,5%) | 5 lig | 1834 | 0,796 | 0,820 | 0,824 (0,806–0,841) | **+0,028** |
| | 7 lig | 1916 | 0,795 | 0,830 | 0,818 (0,801–0,835) | **+0,024** |
| Dixon–Coles, podw. szansa (jednostronnie 95%) | 5 lig | 1324 | 0,781 | 0,827 | 0,840 (0,820–0,859) | +0,059 |
| | 7 lig | 1579 | 0,820 | 0,884 | 0,837 (0,818–0,855) | +0,017 |
| Dixon–Coles, rynki kuponowe (jednostronnie 95%) | 5 lig | 2617 | 0,768 | 0,812 | 0,818 (0,802–0,832) | +0,050 |
| | 7 lig | 2339 | 0,804 | 0,865 | 0,827 (0,811–0,842) | +0,023 |

- **We wszystkich przedziałach od 0,50** dzisiejsza granica Elo i opcja D są dotrzymane
  (częstość ≥ granica). Najmniejszy zapas opcji D jest tuż pod progiem: 0,70–0,75 — +0,008
  (5 lig, 2780 meczów) i +0,007 (7 lig, 4254 mecze), w granicach losowości.
- **Co zmieniłaby opcja D:** więcej meczów z granicą ≥ 0,75 (1834 zamiast 1202 w 5 ligach, 1916
  zamiast 365 w 7 ligach) przy węższym zapasie (+2,4–2,8 pkt zamiast +10–15 pkt). W archiwum
  (§3.1): WYSOKA TRAFNOŚĆ z opcją D — 1 kupon (07.11, ostrożnie 76,9%, trafiony); SOLO ≥ 0,75 z
  opcją D — te same 2 kupony co bez niej.
- **Challenger v3 (informacja):** w strefie ≥ 0,75 opcja D 0,804 → 0,818 (5 lig, 2049 meczów,
  przedział obejmuje granicę) i 0,795 → 0,829 (7 lig); suma dwóch granic +0,059 i +0,098.
- Jedyny przedział z „granica przekroczona”: Dixon–Coles 7 lig 90–100% — 3 mecze (szum).
- **Definicja „ostrożnie” w produkcji bez zmian** (test: `derive_from_1x2` nadal sumuje dolne
  granice dwóch wyników). Decyzja o opcji D należy do Ciebie.

### 5.3 Jedna tabela wariantów na tych samych decyzjach

24 decyzje, 31.10–07.11, godz. 10/14/18 (szczegóły wariantów (a)–(c) w §3.1):

| Wariant | Kupony | Dni z kuponem | Śr. ostrożnie | Trafione |
|---|---|---|---|---|
| WYSOKA TRAFNOŚĆ (dziś) | 0 | 0 z 8 | — | — |
| WYSOKA TRAFNOŚĆ + opcja D (pomiar) | 1 | 1 z 8 | 76,9% | 1 z 1 |
| (b) SOLO ≥ 0,75 = PEWNY SOLO | 2 | 2 z 8 | 77,4% | 0 z 2 |
| (b) SOLO ≥ 0,75 + opcja D | 2 | 2 z 8 | 82,3% | 0 z 2 |
| (b) SOLO ≥ 0,70 | 2 | 2 z 8 | 77,4% | 0 z 2 |
| (c) do 2 nóg ≥ 0,60 | 2 | 2 z 8 | 77,4% | 0 z 2 |
| (a) obecna polityka (4 profile) | 7 | 4 z 8 | 41,3% | 3 z 7 |
| challenger v3 w cieniu | — | — | — | brak wersji v3 w bazie — pomiar niemożliwy (TASK 013 na osobnej kopii: 1 kupon) |

### 5.4 Kalibracja team totals

- **Nowe wersje (protokół v3):** mapy izotoniczne team totals (linie 0,5 / 1,5 / 2,5 każdej
  drużyny; „gospodarz/gość strzeli” = powyżej 0,5) na tym samym oknie kalibracji co pozostałe
  rynki (`rolling.fit_team_total_calibrators`), zapisane w osobnym kluczu
  `team_total_calibrators` raportu wersji. Prognozy takiej wersji (`predict_offer_goals`)
  kalibrują team totals i mają przedział Wilsona 90% — jak inne rynki Dixon–Coles; status
  „eksperymentalny” (rodzina nie przechodzi walidacji predykcyjnej), więc po ewentualnej
  aktywacji v3 trafiałyby tylko do profili bez wymogu walidacji.
- **Championy i stare wersje bez zmian:** nie mają klucza `team_total_calibrators` → team totals
  surowe, bez przedziału (test: treść `validation_json` championa identyczna przed i po treningu
  v3; mapy pozostałych rynków bez zmian).
- **Niezawodność w strefie ≥ 60% przed (surowe) i po (mapy v3), walk-forward, na meczach**
  (okres połowicznego zaniku v3 = 480 dni w każdym miesiącu, więc „przed” = champion v2):

  | Rynek | 5 lig: przed → po (luka częstość − prognoza) | 7 lig: przed → po |
  |---|---|---|
  | team totals razem | −0,010 → −0,012 (zgodna → zgodna) | −0,010 → −0,003 (zgodna → zgodna) |
  | gospodarz 1,5 | −0,035 → −0,021 (zbyt optymistyczna → zbyt optymistyczna) | −0,037 → −0,012 (zbyt optymistyczna → **zgodna**) |
  | gość 1,5 | −0,034 → −0,017 (zbyt optymistyczna → zbyt optymistyczna) | −0,033 → −0,017 (zbyt optymistyczna → zbyt optymistyczna) |
  | gospodarz 0,5 | +0,015 → −0,005 (zbyt ostrożna → **zgodna**) | +0,022 → +0,003 (zbyt ostrożna → **zgodna**) |
  | gość 0,5 | +0,007 → −0,007 (zgodna → zgodna) | +0,002 → +0,011 (zgodna → zgodna) |

  Przedziały 70–80% i 80–90% (wszystkie team totals): w 5 ligach „zbyt optymistyczna” →
  „zgodna”; w 7 ligach 80–90% → „zgodna”, 70–80% nadal „zbyt optymistyczna” (−0,019 → −0,017).
  Log loss team totals (v3 skalibrowane − v2 surowe): +0,0008 (95%: −0,0013…+0,0040) i +0,0009
  (−0,0010…+0,0028) — bez istotnej różnicy; w 7 ligach gość 1,5 istotnie lepiej (−0,0029).

### 5.5 Bez zmian

- Bramka v3: Twoja decyzja (na kopii brak wpisu — nic nie zaakceptowano za Ciebie).
- Wariant B (rekalibracja na rozliczeniach): **37/300 (5 lig), 57/300 (7 lig)** rozliczonych
  meczów aktywnych wersji.

## 6. Zmiany w kodzie i danych

- `performance.py`: `poisson_binomial`, `tails`, `verdict`, `judge`, `expected_vs_actual`,
  `legs_expected_vs_actual`, `recent_line`, `shared_losses`, `market_group`; `selection_outcomes`
  dokłada `match`, `market_group`, `match_start` (pola dodane, nic nie usunięte).
- `app/pages_history.py` (`expected_panel`), `app/page_dashboard.py` (linia 30 dni, profil
  opcjonalny po czterech), `app/coupon_view.py` (`loss_text`, opis PEWNY SOLO), `app/ui.py`
  (kolor profilu), `insights.py` (`loss_line`, `losses_of_ten`).
- `config/probability_policy.toml` → `2026-11-07.p4` (`[profiles.safe_solo]`, `enabled = false`);
  `probability_lab.ProbabilityPolicy.optional` / `with_profiles`; `settings.enabled_profiles`;
  `services.coupon_rules` (kreator, Kupon dnia, replay i diagnoza); `app/pages_settings.py`
  (przełącznik); `app/page_sources.py` (`?widok=…`); `app/common.page_link(query_params)`.
- `app/page_coupon.py`: `same_leg_shared` i odnośnik do limitu; `my_coupon.py` +
  `app/page_my_coupon.py`: ostrzeżenia §4.3.
- `simulation.py`: `option_d` (wydzielone z `high_hit_diagnosis`, zachowanie identyczne),
  `SAFE_PROFILES`, `safe_variants`, `shadow_versions`, `variant_replay`.
- `automation.py`: etykieta scalonego slotu (§5.1), `matchday_cap_effect`.
- `sportsdata.elo_interval` (wydzielone z `predict_offer`, wzór identyczny).
- `rolling.py`: `fit_team_total_calibrators`, `team_total_calibrators` w raporcie i w
  `train_at`, `elo_calibration` w `train_at`, team totals v3 skalibrowane w walk-forward
  (+ rola `v3:surowe`), `bound_coverage`, `team_totals_zone`, `bound_rows`.
  `probability_lab.team_total_family` / `team_total_map` i ich użycie w `predict_offer_goals`.
- `cli.py`: `sports-edge hit-rate [--days] [--hours] [--out]`; `walk-forward-v3 --out` zapisuje
  też `bound_rows`. `qa.py`: 2 nowe ścieżki; `qa_visual.py`: 2 nowe scenariusze w każdej
  rozdzielczości i odnośnik limitu w scenariuszu „Ryzyko dnia”.
- **Schemat bez zmian (v10).** Nowy profil mieszka w pliku polityki, jego włączenie w
  `data/settings.json`, mapy team totals w `validation_json` nowych wersji. Żaden istniejący
  rekord nie został zmieniony ani usunięty.

## 7. Testy i kontrole

`tests/test_task014.py` — 24 testy na danych syntetycznych (rozkład Poissona-dwumianowego
zgodny z rozwiązaniem dokładnym, 7 z 15 przy szansach jak w §1.1 ≈ 0,62, kupony bez szansy
osobno, panel tylko z kuponów liczonych w statystykach — bez aktualizacji kursów i duplikatów,
oznaczenia < 5% / > 95%, nogi na meczach, replay: te same decyzje, bez przecieku (prognoza po
decyzji nic nie zmienia, kontrola: późniejsza decyzja ją widzi), nic nie zapisuje; profil
domyślnie wyłączony i bez wpływu na propozycje, po włączeniu progi i walidacja, regresja czterech
profili przy limicie 0 i 1; karta „Y z 10”; odnośnik tylko dla tej samej selekcji; Mój kupon:
ostrzeżenie i najsłabsza noga; wspólne porażki; scalony slot i pułap; pokrycie granicy na
meczach as-of bez przecieku (wyniki znane po miesiącu nie zmieniają żadnej granicy), definicja
produkcyjna bez zmian; nowe wersje mają mapy team totals, stare/championy bez zmian; walk-forward
team totals przed/po; AppTest: Historia pokazuje panel, Ustawienia pokazują i zapisują profil).

**Zmienione istniejące testy** (żaden nie został wyłączony): `test_task007.py` — oczekiwana wersja
polityki `p3` → `p4` (+ sprawdzenie, że jedynym profilem opcjonalnym jest `safe_solo`; cztery
profile i ich progi sprawdzane jak dotąd); `test_ui_paths.py` oczekuje 2 nowych ścieżek QA.

Kod testowano z worktree (`conftest` wstawia własny `src`; dla QA, Playwright i pomiarów
`PYTHONPATH=<worktree>\src`). Pomiary na kopii: `sports-edge hit-rate` i
`sports-edge research walk-forward-v3 --scope core|extended --out …` z `SPORTS_EDGE_HOME`
wskazującym kopię.

| Kontrola | Wynik |
|---|---|
| `pytest -q` | **432 passed, 4 skipped** (436 testów; pominięte: brak lokalnych autentycznych PDF STS), 208 s |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 178 plików sformatowanych |
| `python -m sports_edge.qa` (ścieżki UI, DEMO) | **34/34 OK**, w tym nowe: „historia: oczekiwane kontra faktyczne”, „ustawienia: profil PEWNY SOLO (domyślnie wyłączony)” |
| `python -m sports_edge.qa_visual` (Playwright, DEMO) | **93 kontrole, 0 błędów** (14 stron × 5 rozdzielczości: 1920, 1366, 1024, 768, 390 px + scenariusze; nowe w każdej rozdzielczości: Historia — panel „Oczekiwane kontra faktyczne”, Ustawienia — limit i profil PEWNY SOLO, Kupon dnia — odnośnik „Ustaw limit…” w Ryzyku dnia) |
| `python -m sports_edge.qa_browser` (Playwright, DEMO) | 22 zrzuty, 0 błędów |
| `qa_visual --home <kopia>`: Historia, Kupon dnia, Mój kupon, Źródła i ustawienia × 5 rozdzielczości | **20 kontroli, 0 błędów**; po poprawce kolejności kolumn panelu Historia ponownie w 1366 i 390 px: 2/2 OK |

**Przegląd ręczny zrzutów z kopii** (zrzutów nie dodano do repozytorium; usunięte razem z
kopią): Historia — panel z liczbami z §1 (7 z 14, oczekiwane 6,9, 62% / 60%, osobna linia „Bez
szansy modelu”, tabela nóg, linia wspólnych porażek); Kupon dnia (07.11) — „Ryzyko dnia” z tą
samą selekcją w dwóch kuponach i odnośnikiem „Ustaw limit…”, na kartach linia „Szansa X% —
statystycznie przegrywa Y z 10 takich kuponów; wypłata Z zł”; Mój kupon — pusty kupon bez błędów.
Przegląd wykazał dwie usterki panelu (kolumna „Oczekiwane (ostrożnie)” za długą oceną, podwojona
liczba w linii „1 1 noga”) — poprawione przed końcowymi kontrolami. Przed QA na kopii zapisano w
niej tryb offline i polecenie „stop” dla workera (stan kopii zmienił się z „running” na
„stopped”); żaden worker na kopii nie wystartował.

## 8. Ograniczenia

- **Mała próba.** 14 rozliczonych kuponów z szansą modelu, 27 meczów z nogami, 8 dni replayu
  (24 decyzje). Wariant PEWNY SOLO to 2 kupony; „0 z 2” i „2 z 2” to szum, nie dowód.
- **Trafność ≠ zysk.** Przy 12% podatku od stawki kupon SOLO po kursie 1,19 wygrywa 0,47 zł na
  10 zł i przegrywa 10 zł — wysoka szansa nie oznacza dodatniego wyniku (EV tylko diagnostycznie).
- **Kupony jako niezależne** w rozkładzie Poissona-dwumianowym; kupony dnia ze wspólną nogą są
  skorelowane, więc rzeczywisty rozrzut jest większy (oznaczenia „gorzej/lepiej” są wtedy
  raczej zbyt pochopne niż zbyt ostrożne).
- **Kursy w archiwum stare:** 2–8 dokumentów STS dziennie, mediana wieku 4,6 h — stąd 6 decyzji
  bez kandydatów przez kurs starszy niż 3 h. Pomiar „w dniu meczu” wymaga kilku dni pracy nowego
  kodu (§5.1).
- **Pokrycie granicy** zmierzone na historii 5 i 7 lig (sezony 2024/25–2026/27); granica jest
  przedziałem kalibracji bloku, nie gwarancją dla pojedynczego meczu.
- **Team totals:** kalibracja poprawia niezawodność strefy ≥ 60%, ale nie poprawia log loss; linie
  1,5 nadal są optymistyczne w 3 z 4 przypadków. Działa dopiero w wersji v3 po Twojej decyzji.
- **Challengera v3 nie ma w bazie** — wiersz „w cieniu” pusty.

## 9. Proponowany TASK 015

1. **Pierwsze dni trybu „w dniu meczu”** (po restarcie): wiek kursów z raportu tygodniowego i
   powtórzona diagnoza WYSOKIEJ TRAFNOŚCI na świeżych kursach (`sports-edge hit-rate`).
2. **Decyzja o opcji D** na danych z §5.2 i obserwacja PEWNEGO SOLO, jeśli go włączysz
   (oczekiwane kontra faktyczne w Historii pokaże go osobno).
3. **Challengery v3 z mapami team totals** w trybie cienia (bramka v3 i dowody n/300).
4. **Wariant B** jako pomiar, gdy zakres przekroczy 300 rozliczonych meczów (dziś 37 i 57).
5. **Korelacja kuponów dnia** w ocenie „oczekiwane kontra faktyczne” (dni ze wspólną nogą) —
   gdy przybędzie rozliczeń.
