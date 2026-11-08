# Raport TASK 015 — zegar dowodów i decyzje w jednym miejscu

Data pracy: 08.11.2026. Kod bazowy: `91773f1` (TASK 014). Wszystkie pomiary na prawdziwych
danych wykonano na **kopii** prywatnej bazy (`storage.snapshot_file`, tylko do odczytu, katalog
tymczasowy, kopia z 08.11 ok. 09:52 czasu polskiego). Przed pomiarami w kopii zapisano tryb
offline i polecenie „stop” dla workera; po pomiarach kopię usunięto. Prawdziwa baza nie była
otwierana nowym kodem. W raporcie są wyłącznie liczby zbiorcze — bez nazw meczów i bez nazw z
kuponów. Raport nie zawiera rekomendacji zysku ani stawek. Żaden model nie został aktywowany,
progi profili, polityka walidacji i definicja szansy ostrożnej w Twoich ustawieniach są bez
zmian; żadnej decyzji nie podjęto za Ciebie.

## 0. Najważniejsze w skrócie

- **Zegar dowodów rusza od razu.** Na kopii jeden przegląd (33 s) wygasił 6 czekających
  challengerów v2 — 4 jako **identyczne z championem** (te same okna, hiperparametry,
  kalibratory i miary okna selekcji; holdout v2 różni się tylko nowszymi meczami), 2 jako
  zastąpione — i utworzył **pierwsze challengery v3 w obu zakresach**, mimo że od treningu
  championów przybyło tylko 33 (5 lig) i 61 (7 lig) nowych meczów. Zaraz potem powstały
  prognozy cienia dla 15–20 dzisiejszych meczów na rodzinę. Nic nie zostało aktywowane.
- **Czym różnią się v3 od championów:** Elo 5 lig — przewaga gospodarza 40 → 60, margines
  remisu 0,25 → 0,27; Elo 7 lig — k 30 → 15, przewaga 40 → 60, margines 0,24 → 0,26;
  Dixon–Coles w obu zakresach — okres połowicznego zaniku 480 dni (reguła `sticky12` go
  zatrzymała), wszystkie 5 map kalibracji inne (5 lig: okno 24 mies., 7 lig: 12 mies.) i
  6 map team totals (TASK 014).
- **Start po wdrożeniu:** worker przy starcie na nowym kodzie planuje jednorazowy przegląd za
  10 minut (najpóźniej w ciągu godziny). Bez sieci albo przy wyłączonym treningu przegląd jest
  pomijany z jasnym powodem w historii zadań.
- **n/300 z szacunkiem daty:** dziś 0/300 w każdej rodzinie. Szacunek z tempa rozliczeń:
  5 lig ok. **29.12.2026** (Elo) i 03.01.2027 (Dixon–Coles), 7 lig ok. **10.12.2026** i
  12.12.2026. To szacunek z zaledwie ok. 7 dni zapisów prospektywnych; przerwa
  reprezentacyjna 09–15.11 (0 meczów 5 lig w terminarzu) go opóźni.
- **Panel „Decyzje do podjęcia”** (Modele i statystyki → widok „Decyzje do podjęcia”, na
  Dashboardzie liczba otwartych decyzji z odnośnikiem): pięć decyzji z tym, co zmieniają,
  dowodami w liczbach, ryzykiem i przyciskiem z potwierdzeniem. Każda decyzja i jej cofnięcie to
  nowy wpis nowej tabeli append-only `user_decisions` (schemat v11). Na kopii otwarte są
  **3 z 5**: opcja D, PEWNY SOLO, limit różnicowania. **Bramka v3 jest już zaakceptowana przez
  Ciebie: 08.11 o 09:24** (Modele → Uczenie AI) — już po diagnozie zadania.
- **Opcja D jako przełącznik** (domyślnie dzisiejsza definicja). Na kopii, przełączona tylko w
  obiekcie ustawień skryptu pomiarowego, na tych samych 24 decyzjach co w TASK 014: WYSOKA
  TRAFNOŚĆ **0 → 1 kupon** (ostrożnie 76,9% — dokładnie wynik pomiaru TASK 014), zwalidowane
  typy podwójnej szansy Elo ≥ 0,75 kwalifikujące się do kreatora **5 → 14** (typ × decyzja).
- **Wiek kursów: za mało danych (1 dzień z pobraniami w dniu meczu; potrzeba 3).** Pierwsze
  pobranie trybu „w dniu meczu” nastąpiło 08.11 o 09:30. Na Dashboardzie jest nowa linia z
  wiekiem kursu ostatniego dokumentu dla meczów dziś.

## 1. Diagnoza (08.11.2026, kopia z ok. 09:52, kod `91773f1`)

### 1.1 Aplikacja i worker

- Worker uruchomiony ponownie 08.11 o **09:01** na kodzie `91773f1` (z poprawką zapisu
  `runtime.json`). Baza w schemacie v10 (migracja 07.11 o 22:54).
- Ostatnia awaria workera: 07.11 o 23:15 (`PermissionError` przy zapisie `runtime.tmp`) — ślad
  pochodzi z kodu sprzed poprawki workera (zapis przez wspólny `runtime.tmp`); po restarcie
  08.11 nie powtórzyła się.
- 08.11 do 09:52: zadanie `sts_offer` 2 razy (09:01 z interwału, **09:30 w trybie „w dniu
  meczu”**), `forecasts` łącznie 5 przebiegów (ostatni 09:30), rozliczenia i składy działają.
- 08.11 o 09:01 Twoja aplikacja zapisała **pierwszy w historii kupon oficjalny WYSOKIEJ
  TRAFNOŚCI** (SOLO, kurs 1,28, ostrożnie 76,3%, zwalidowany) — przy świeżych kursach, przy
  dzisiejszej definicji szansy ostrożnej. Do 07.11 ten profil nie dał ani jednego kuponu.

### 1.2 Zegar dowodów stoi

- Wersje modeli: **9 v1, 10 v2, 0 v3**.
- Czekające challengery: 5 lig — po 2 na rodzinę (02.11 i 06.11), 7 lig — po 1 (06.11).
- Challengery z 06.11 mają te same hiperparametry, kalibratory, statusy i miary okna selekcji co
  championy. Różni się tylko holdout v2, bo sięga do daty treningu: n 3927 → 3936 (Elo 5 lig),
  3899 → 3907 (Dixon–Coles 5 lig), 5543 → 5563 i 5493 → 5513 (7 lig).
- Ostatni automatyczny przegląd: 02.11 (20:19). Następny tygodniowy wypadłby 09.11 i zostałby
  pominięty: nowych meczów 33 / 61 < 150, czekają challengery, ostatni trening < 7 dni temu.
- Rozliczone mecze aktywnych wersji: **40 (5 lig), 61 (7 lig)** — w TASK 014 było 37 i 57.

### 1.3 Decyzje

| Decyzja | Stan 08.11 |
|---|---|
| Bramka v3 | **zaakceptowana przez Ciebie 08.11 o 09:24** (Modele → Uczenie AI) |
| Opcja D | brak przełącznika (do TASK 015) |
| PEWNY SOLO | wyłączony (brak pola w `data/settings.json`) |
| Limit różnicowania | wyłączony (polityka: 0, brak pola w ustawieniach) |
| Challengery | 6 czekających v2 (żadnego v3); 4 z 06.11 identyczne z championami |

### 1.4 Tryb „w dniu meczu” i najbliższe mecze

- Dni z pobraniami w dniu meczu: **1** (08.11). Dziś w ofercie STS **49 meczów 12 lig**
  (24 w 5 ligach, 25 w 7 ligach); o 09:52 żaden się jeszcze nie zaczął.
- Kolejne dni w archiwum STS: brak meczów 12 lig po 08.11 (najnowszy dokument z 08.11, 09:25 —
  oferta na kolejne dni jeszcze nie była pobrana).
- Terminarz 5 lig (lokalne pliki openfootball CC0 z 01.11): **09–15.11: 0 meczów** (przerwa
  reprezentacyjna), 16–22.11: 46, 23–29.11: 50.
- 7 lig: lokalnego terminarza brak — brak danych. Nie założono, że też pauzują.

## 2. Zegar dowodów (P0)

### 2.1 Wygaszanie challengerów identycznych z championem

- **Reguła** (`retraining.identical_to_champion`): challenger z **tym samym protokołem** co
  champion (v2 = v2, v3 = v3; nigdy v1) jest identyczny, gdy:
  - ma te same okna (`split`), hiperparametry, miary okna selekcji (`decision`) i statusy;
  - ma te same kalibratory: Elo — `calibration`; Dixon–Coles — `calibrators`, mapy team
    totals, okres połowicznego zaniku, okno i odświeżanie, serwowane rynki;
  - holdout ma te same liczby **albo** — w protokole v2, gdzie holdout sięga do daty treningu i
    jest tylko raportowany — to samo okno (od 2024-07-01) poszerzone wyłącznie o nowsze mecze
    (nigdy mniej meczów);
  - zabezpieczenie: jeżeli zapisane prognozy obu wersji dla tego samego meczu, rynku i stanu
    danych (`data_snapshot`) się różnią, challenger **nie** jest identyczny.
- **Dlaczego holdout „poszerzony”, a nie „te same liczby”:** dosłownie identyczny holdout v2
  jest niemożliwy, gdy od treningu championa przybył choć jeden mecz. Model jest jednak ten
  sam — każda część, od której zależy prognoza, jest równa. Przy dosłownej regule na kopii nie
  wygasłby żaden challenger.
- **Zapis:** nowy wiersz `model_events` (`expired`, `actor = 'system'`) z powodem „Wygasły:
  identyczny z championem (ten sam protokół v2: te same okna, hiperparametry, kalibratory i
  miary okna selekcji, holdout od 2024-07-01 poszerzony tylko o nowsze mecze: n 3927 → 3936) —
  nie może dać innej prognozy” i `payload.identical = true`. Stare wiersze bez zmian; klucz
  idempotentny (jeden wpis na wersję). Wygaśnięcie nigdy niczego nie aktywuje.
- Kolejność powodów bez zmian: zastąpiony nowszym → 14 dni bez wspólnych meczów → identyczny.
- **Na kopii:** wygasły 4 challengery z 06.11 jako identyczne i 2 z 02.11 jako zastąpione.

### 2.2 Pierwszy challenger v3 bez warunku 150 meczów

- Gdy w zakresie **nie ma żadnej wersji v3**, przegląd (`retraining.training_conditions`)
  pomija warunki „150 nowych meczów” i „7 dni od ostatniego treningu”.
  - Oba liczą nowe dane w obrębie jednego protokołu.
  - Warunek 7 dni pominięto też dlatego, że trening v2 z 06.11 zablokowałby pierwszy v3 do
    13.11.
- **Nadal wymagane:**
  - co najmniej 1 nowy mecz od treningu championa (v3 odmawia treningu na tych samych danych);
  - żaden challenger nie czeka — identyczne wygasają wcześniej w tym samym przeglądzie.
- Trening idzie przez `run_protocol_v3`. Gdy okna v3 są niewykonalne (za krótka historia),
  zapasowy challenger v2 powstaje tylko przy spełnionych zwykłych warunkach (test 6). Inaczej co
  tydzień powstawałby nowy, identyczny v2.
- **Pierwszy v3 ma wymagane reguły:** okna z `config/protocol_v3.toml` `2026-11-07.v3b`, Dixon–Coles
  `sticky12`, kalibratory 24 mies. dla 5 lig, mapy team totals (TASK 014), tryb cienia.
- W historii uczenia zostaje wpis „Przegląd: pierwszy challenger v3 w zakresie — bez warunku 150
  nowych meczów i 7 dni od treningu (inny protokół niż champion v2)”.
- Kolejne challengery v3 podlegają zwykłym warunkom (test 4).
- **Bramka v3 blokuje tylko aktywację.** Trening i tryb cienia działają bez akceptacji (test 3).

**Na kopii — challengery v3 kontra championy v2:**

| | 5 czołowych lig | 7 lig |
|---|---|---|
| Nowe mecze od treningu championa | 33 | 61 |
| Okna v3 | kalibracja od 2023-11-08 (24 mies., 3448 meczów), selekcja od 2025-11-08 (1734) | kalibracja od 2024-11-08 (12 mies., 2435), selekcja od 2025-11-08 (2379) |
| Elo: k / przewaga / margines remisu | 15 → 15 · 40 → **60** · 0,25 → **0,27** | 30 → **15** · 40 → **60** · 0,24 → **0,26** |
| Dixon–Coles: okres połowicznego zaniku | 480 → 480 dni (`sticky12`: bez zmiany) | 480 → 480 dni (`sticky12`: bez zmiany) |
| Kalibratory Dixon–Coles (1X2, O/U 1,5/2,5/3,5, BTTS) | wszystkie 5 inne | wszystkie 5 inne |
| Team totals | 6 map (gospodarz i gość 0,5 / 1,5 / 2,5) | 6 map |
| Okno selekcji (informacja) | Elo n = 1734, log loss 0,99129, ECE 0,0095; Dixon–Coles n = 1722, log loss 1,00918 | Elo n = 2379, log loss 1,01982, ECE 0,0131; Dixon–Coles n = 2360, log loss 1,02816 |
| Statusy w oknie selekcji (informacja) | 1X2 i podwójna szansa zwalidowane, rynki bramkowe eksperymentalne | to samo |
| Prognozy cienia zaraz po treningu (mecze dziś) | Elo 20, Dixon–Coles 20 | Elo 17, Dixon–Coles 15 |
| Aktywacja | zablokowana: „za mało dowodów w trybie cienia (0/300 …)” | to samo |

Log loss okna selekcji v3 dotyczy innego okresu niż okno v2. Nie porównuje się go z championem —
rozstrzyga tryb cienia na tych samych meczach.

### 2.3 Start po wdrożeniu

- `automation.plan_first_review`: przy starcie workera (tryb `run`, nie „tylko polecenia”)
  planowane jest zadanie jednorazowe `review` (wariant `first_v3_review`) za **10 minut** —
  po nadrobieniu zaległych zadań, najpóźniej w ciągu godziny.
- Warunek planowania: zakres z aktywnym modelem bez wersji v3. Drugi start nie planuje drugiego
  zadania, dopóki pierwsze czeka. Pominięte (offline) planuje się ponownie przy następnym
  starcie.
- Wykonanie (`automation.job_first_review`):
  - bez sieci (tryb offline) — „pominięty (bez sieci)”;
  - przy wyłączonym „Treningu challengera” w automatyzacji — pominięty z powodem i wskazówką;
  - w pozostałych przypadkach przegląd z §2.1–2.2, a po treningu od razu automatyczne prognozy
    (`run_forecasts`), żeby challengery dostały prognozy cienia.
- U Ciebie trening w automatyzacji jest włączony. Zadanie widać w Automatyzacji AI (kalendarz:
  „jednorazowo”) i w historii zadań (`auto:review`, wyzwalacz `one_off`).

### 2.4 n/300 i szacowana data

- Panel challengerów (Uczenie AI) pokazuje przy każdym zakresie i rodzinie kolumny „Dowody n/300”
  i „Szacowana data 300 meczów” oraz linię z opisem.
- Szacunek (`retraining.evidence_estimate`) pochodzi z tempa rozliczeń z ostatnich 14 dni:
  rozliczone mecze wersji serwowanych tego zakresu i rodziny. Prognozy cienia powstają dla tych
  samych meczów.
- Gdy zapis prospektywny jest młodszy niż 14 dni, liczony jest z własnego okresu i tak opisany.
- Bez rozliczeń w 14 dniach: „za mało danych”, nigdy zgadywana data.

| Zakres, rodzina | n/300 | Tempo | Szacunek |
|---|---|---|---|
| 5 lig, Elo | 0/300 | 40 meczów w 6,8 dnia (ok. 41 tygodniowo) | ok. 29.12.2026 |
| 5 lig, Dixon–Coles | 0/300 | 36 meczów (ok. 37 tygodniowo) | ok. 03.01.2027 |
| 7 lig, Elo | 0/300 | 61 meczów w 6,6 dnia (ok. 64 tygodniowo) | ok. 10.12.2026 |
| 7 lig, Dixon–Coles | 0/300 | 59 meczów (ok. 62 tygodniowo) | ok. 12.12.2026 |

- To szacunek z jednego tygodnia z dwoma pełnymi weekendami.
- 09–15.11 w terminarzu 5 lig nie ma meczów, więc realna data będzie późniejsza. Panel liczy ją
  na nowo przy każdym otwarciu.

## 3. Panel „Decyzje do podjęcia” i opcja D (P0)

### 3.1 Gdzie i jak

- **Dashboard:** sekcja „Decyzje do podjęcia — otwarte: N z 5” z nazwami otwartych decyzji i
  odnośnikiem „Przejdź do panelu decyzji” (`?widok=Decyzje do podjęcia`).
- **Modele i statystyki → widok „Decyzje do podjęcia”:** pięć pozycji. Każda pokazuje:
  - co zmienia;
  - dowody — na żywo z funkcji pomiarowych TASK 011–014 oraz liczby z raportów TASK 012–014 z
    podanym źródłem;
  - ryzyko;
  - wybór, pole „Potwierdzam…” i przycisk zapisu.

  Pod pozycjami jest tabela „Historia decyzji”.
- Dowody na żywo:
  - bramka i challengery — stan n/300 z szacunkiem (`retraining.comparison`);
  - opcja D — dzisiejsza oferta przy obu definicjach, tym samym silnikiem co kupony
    (`decisions.option_d_today`);
  - PEWNY SOLO — `performance.expected_vs_actual` profilu;
  - limit — `performance.shared_losses`.
- **Otwarta decyzja:** taka, która nie ma jeszcze Twojego zapisu. Bramka jest zamknięta także przez
  Twoją akceptację w Uczeniu AI. Challengery są „otwarte” dopiero przy dowodach ≥ 300; wcześniej
  nie ma przycisku aktywacji, tylko odnośnik do panelu challengerów.
- **Zapis** (`decisions.record`): wiersz `user_decisions` — kiedy, decyzja, `actor = 'user'`,
  poprzedni i nowy stan, opis, miejsce (panel albo Ustawienia). Cofnięcie to kolejny wiersz.
  Triggery `immutable_*` odrzucają `UPDATE` i `DELETE`.
- Bramka korzysta z mechanizmu TASK 013 (`accept_gate` / `withdraw_gate`) i dodatkowo zapisuje
  wiersz historii decyzji.
- **Ustawienia → „Zapisz ustawienia”** zapisuje wiersz historii tylko dla decyzji, które
  naprawdę się zmieniły (opcja D, PEWNY SOLO, limit).
- **System nigdy sam:** funkcje `decide_*` wywołują wyłącznie przyciski panelu (test 16
  przeszukuje `src/`). Worker, przegląd i trening nie dotykają ustawienia opcji D.

### 3.2 Panel na kopii (po przeglądzie z §2)

| Decyzja | Stan | Otwarta | Dowody na żywo (kopia) |
|---|---|---|---|
| Bramka v3 | zaakceptowana (08.11 09:24) | nie | 4 challengery v3 w cieniu: 0/300; szacunki jak w §2.4 |
| Opcja D | suma dwóch przedziałów — domyślnie | **tak** | oferta 08.11: typy podwójnej szansy Elo z ostrożną ≥ 75%: **5 → 29**; kwalifikujące się do kreatora 5 → 26 (zwalidowane 3 → 12) |
| PEWNY SOLO | wyłączony — domyślnie | **tak** | rozliczone kupony PEWNY SOLO: brak danych (profil nie zapisał kuponu) |
| Limit różnicowania | bez limitu — domyślnie | **tak** | 5 rozliczonych dni; 1 dzień, w którym jedna przegrana noga była w 2+ kuponach (4 kupony) |
| Challengery | czekają na dowody | nie | 4 × 0/300 (z szacunkiem daty) |

Otwarte: **3 z 5**.

### 3.3 Opcja D — przełącznik

- **Definicje:**
  - `sum` — **dzisiejsza, domyślna:** suma dolnych granic dwóch wyników 1X2;
  - `single` — opcja D: 1 − górna granica wykluczonego wyniku (jeden przedział, jak
    Dixon–Coles).
- Wariant `single` jest **dokładnie** wariantem zmierzonym w TASK 013 §5 i TASK 014 §5.2
  (`simulation.option_d`, `rolling._elo_bounds`). Bierze wyższą z dwóch granic i nigdy nie
  przekracza szansy modelu (test 11 sprawdza zgodność).
  - W typowym przypadku granica równa się dopełnieniu górnej granicy wykluczonego wyniku.
  - Suma zostaje tylko wtedy, gdy przedział wykluczonego wyniku jest szerszy niż dwa pozostałe
    razem.
- **Zakres:**
  - zmienia wyłącznie dolną granicę podwójnej szansy Elo (`edge_engine.derive_from_1x2(bound=…)`);
  - szanse modelu, górne granice, Dixon–Coles i progi profili zostają bez zmian.
- **Ustawienie:** `elo_dc_bound` w `data/settings.json`. Brak pola oznacza dzisiejszą definicję,
  więc stare pliki czytają się jak dotąd.
- **Gdzie się przełącza:** panel decyzji oraz Ustawienia (radio „Granica „ostrożnie” podwójnej
  szansy Elo”).
- **Zapis w propozycji:** każda nowa propozycja ma pole `cautious_definition`.
  - Przy opcji D wersja polityki zapisana z kuponem ma przyrostek `+D` (np. `2026-11-07.p4+D`).
  - Przy definicji domyślnej wersja jest **bez zmian**, więc nie zmienia się żadna tożsamość
    kuponu.
- **Zmiana w ciągu dnia (reguła TASK 010):**
  - inna wersja polityki to inna tożsamość, ale ten sam slot profilu i dnia;
  - nowa propozycja jest **rewizją** z powodem „te same nogi, inna wersja polityki lub
    definicji szansy ostrożnej (zmiana w ciągu dnia)”;
  - kupon oficjalny dnia zostaje (test 12).
- Kupon dnia podaje używaną definicję obok wersji polityki.

**Skutki na kopii** (przełączone wyłącznie w obiekcie ustawień skryptu pomiarowego; replay as-of,
nic nie zapisano):

| Te same 24 decyzje co TASK 014 (31.10–07.11, godz. 10/14/18) | Dzisiejsza definicja | Opcja D |
|---|---|---|
| Kupony WYSOKIEJ TRAFNOŚCI | 0 | **1** (07.11 14:00, SOLO, kurs 1,27, ostrożnie 76,9%, model 78,6%, zwalidowany) |
| Kody odmów | brak kandydatów 16, niska szansa 4, wypłata 3, szansa kuponu 1 | brak kandydatów 16, niska szansa 4, wypłata 3, **kupon 1** |
| Zwalidowane typy w przedziale kursu 1,25–1,60 z ostrożną ≥ 0,75 (typ × decyzja / unikalne) | 0 / 0 | **3 / 3** |
| Typy podwójnej szansy Elo z ostrożną ≥ 0,75 — wszystkie kursy (typ × decyzja / unikalne) | 31 / 13 | 112 / 42 |
| … kwalifikujące się do kreatora i zwalidowane (typ × decyzja / unikalne) | 5 / 3 | **14 / 10** (+9 / +7) |

- Wynik pokrywa się z TASK 014: WYSOKA TRAFNOŚĆ z opcją D dała tam 1 kupon z ostrożną 76,9%.
  Wdrożony przełącznik daje więc te same liczby co pomiar.
- **Dzisiejsza oferta przy świeżych kursach** (08.11 09:52, po pobraniu „w dniu meczu” o 09:30;
  pojedyncza obserwacja):
  - WYSOKA TRAFNOŚĆ buduje kupon przy obu definicjach — dziś SOLO kurs 1,28, ostrożnie 76,3%;
    z opcją D inny SOLO, kurs 1,35, ostrożnie 80,6%; oba zwalidowane;
  - typy w przedziale kursu z ostrożną ≥ 0,75: 4 (4 zwalidowane) → 12 (10 zwalidowanych);
  - opcja D zmienia też nogi innych profili: WYŻSZA WYPŁATA 3 → 4 nogi, TASIEMKA 7 → 8,
    STANDARDOWY bez zmiany liczby nóg. Panel pisze o tym w „Ryzyku”.
- Dane historyczne do decyzji (TASK 014 §5.2), też w panelu:
  - dzisiejsza granica ma zapas +10,3 / +15,2 pkt;
  - opcja D ma zapas +2,8 / +2,4 pkt i cały przedział 95% nad granicą.

## 4. Wiek kursów po trybie „w dniu meczu” (P1)

- **Stan: za mało danych — 1 dzień z pobraniami w dniu meczu (08.11), potrzeba 3.**
  - Pierwsze pobranie trybu: 08.11 o 09:30.
  - O 09:52 żaden z 49 dzisiejszych meczów 12 lig się nie zaczął, więc nie ma jeszcze wieku
    kursu „przed startem” z tego trybu.
  - Diagnoza WYSOKIEJ TRAFNOŚCI na dniach ze świeżymi kursami wymaga 3 takich dni, więc jej nie
    powtórzono. Pojedynczą obserwację z 08.11 pokazuje §3.3.
- **Najbliższe dni:**
  - w ofercie STS tylko 08.11 (49 meczów);
  - w terminarzu 5 lig 09–15.11 nie ma meczów, od 16.11 są (46 w tygodniu);
  - dla 7 lig brak lokalnego terminarza.
  - Trzeci dzień z pobraniami w dniu meczu wypadnie najwcześniej w najbliższym dniu z meczami po
    przerwie: 5 lig od 16.11; 7 lig — nie wiadomo.
- **Dotąd (01–08.11, `prospective_eval.odds_age_report`):**

| Dzień | Mecze (rozpoczęte) | Mediana wieku | ≤ 3 h | Pobrania automatyzacji (w dniu meczu) | Dokumenty STS | Sloty planu bez pułapu → z pułapem |
|---|---|---|---|---|---|---|
| 01.11 | 35 | 18,00 h | 0% | 0 (0) | 8 | 21 → 12 |
| 02.11 | 11 | 0,83 h | 100% | 3 (0) | 8 | 6 → 6 |
| 03.11 | 9 | 4,25 h | 0% | 2 (0) | 4 | 4 → 4 |
| 04.11 | 4 | 0,83 h | 100% | 2 (0) | 6 | 4 → 4 |
| 05.11 | 0 | — | — | 1 (0) | 3 | 0 |
| 06.11 | 14 | 2,33 h | 64% | 2 (0) | 6 | 6 → 6 |
| 07.11 | 54 | 4,08 h | 20% | 2 (0) | 4 | 21 → 12 |
| 08.11 (do 09:52) | 0 | — | — | 2 (**1**) | 3 | — |

  Razem 127 meczów: mediana 4,58 h, ≤ 3 h 27,6% (tak jak w TASK 014).
- **Dashboard** (`prospective_eval.today_odds_age`) — jedna linia pod paskiem stanu, na kopii:
  „Kursy na dziś (49 meczów 12 lig, przed startem 49): ostatni dokument STS z 09:25 — 0,5 h temu
  · pobrania w dniu meczu dziś: 1/12.” Bez meczów dziś: „brak meczów 12 lig w ofercie STS na
  dziś”; bez dokumentu z kursem: „brak danych”.

## 5. Zmiany w kodzie i danych

- `retraining.py`:
  - `identical_to_champion`, `MODEL_PARTS`, `_holdout_same`, `_shadow_differs`;
  - w `expiring` nowy powód „identyczny”, w `expire_challengers` pole `identical`;
  - `has_v3`, `training_conditions` (pierwszy v3, pole `waived`), `review` (trening pierwszego
    v3 przez v3; zapasowy v2 tylko przy zwykłych warunkach; wpis „pierwszy challenger v3”);
  - `evidence_estimate` i `estimate` w `comparison`.
- `automation.py`:
  - `FIRST_REVIEW`, `first_review_needed`, `plan_first_review` (przy starcie workera),
    `job_first_review` (pominięcie offline lub przy wyłączonym treningu, potem prognozy cienia);
  - jawny wykonawca zadania jednorazowego ma pierwszeństwo przed testowym; błąd planowania
    jest logowany i nie zatrzymuje workera;
  - `write_runtime`, `Worker.beat` i `_heartbeat_loop` bez zmian.
- `decisions.py` (nowy): pięć decyzji, historia, funkcje `decide_*` (tylko przyciski),
  `log_settings_change`, `option_d_today`, `panel`, `open_count`.
- `analysis/edge_engine.py`:
  - `BOUND_SUM` / `BOUND_SINGLE`, `single_interval_low`, `policy_version_with_bound`;
  - parametr `bound` w `derive_from_1x2` i `elo_dc_bound` w `evaluate`.
- `services.day_candidates_at` przekazuje ustawienie; `_analyse_one_day` przekazuje definicję do
  zapisu.
- `coupon_store.py`:
  - `cautious_definition` w propozycji, `+D` w wersji polityki;
  - powód rewizji „te same nogi, inna wersja polityki…”.
- `settings.py`: pole `elo_dc_bound = "sum"`.
- `prospective_eval.today_odds_age`.
- Interfejs:
  - `app/page_decisions.py` (nowy);
  - `page_models` (widok „Decyzje do podjęcia”, `?widok=`);
  - `page_dashboard` (sekcja decyzji i linia wieku kursów);
  - `page_learning` (kolumny n/300 i szacowanej daty, opis wygaszania);
  - `pages_settings` (radio opcji D, zapis decyzji);
  - `page_coupon` (definicja przy wersji polityki);
  - `common.day_candidates` (definicja w kluczu pamięci podręcznej).
- `qa.py`: 3 nowe ścieżki. `qa_visual.py`: scenariusz „Decyzje do podjęcia” w każdej
  rozdzielczości i radio opcji D w scenariuszu Ustawień.
- **Schemat v11** (`storage.py`): jedna nowa tabela append-only `user_decisions` z triggerami
  `immutable_*`.
  - Na świeżej kopii prawdziwej bazy migracja v10 → v11 utworzyła `pre-migration-v11_…db`.
  - Liczby wierszy wszystkich 46 tabel bez zmian, poza nowym wpisem `schema_migrations`.
    Wyjątek: polecenie „stop” w `automation_commands`, zapisane w kopii przed QA.
  - Żaden istniejący rekord nie został zmieniony ani usunięty.
- `config/` **bez zmian**: progi profili, polityka walidacji i protokół v3 nietknięte.

## 6. Testy i kontrole

`tests/test_task015.py` — 20 testów na danych syntetycznych (19 funkcji, jedna w dwóch
wariantach):

1. challenger identyczny z championem (ten sam protokół, holdout poszerzony tylko o nowsze mecze)
   wygasa od razu — jeden nowy wiersz `model_events`, stare bez zmian, nic nie aktywowane;
   challenger z innymi hiperparametrami lub kalibratorami nie wygasa;
2. inny protokół nigdy nie jest „identyczny”; różne zapisane prognozy na tym samym stanie danych
   też nie;
3. stan prawdziwej bazy z 08.11 odtworzony na danych syntetycznych (champion v2, identyczny
   bliźniak v2 sprzed 2 dni, < 150 nowych meczów): przegląd wygasza bliźniaka i trenuje pierwszy
   v3 (`sticky12`, kalibracja 24 mies., mapy team totals); champion bez zmian; bramka blokuje
   aktywację, ale nie trening i nie tryb cienia;
4. drugi v3 podlega zwykłym warunkom (150 meczów, czekający challenger);
5. bez nowego meczu reguła 150 obowiązuje;
6. (2 warianty) gdy okna v3 są niewykonalne, zapasowy challenger v2 powstaje tylko przy
   spełnionych zwykłych warunkach — nigdy dzięki wyjątkowi „pierwszy v3”;
7. jednorazowy przegląd: planowany po starcie (≤ 1 h), nie dubluje się, offline i przy
   wyłączonym treningu pominięty z powodem;
8. bez aktywnego modelu nic nie jest planowane;
9. szacunek n/300: tempo z 14 dni, młodszy zapis, „za mało danych”, próg osiągnięty;
10. opcja D: granica = dopełnienie górnej granicy wykluczonego wyniku, szanse i górne granice bez
    zmian, przypadek szerokiego przedziału, wersja polityki `+D`;
11. domyślnie liczby identyczne jak przed zmianą (kandydaci i propozycje), po przełączeniu
    produkcja = zmierzony wariant `simulation.option_d`, inne rynki bez zmian;
12. zmiana w ciągu dnia daje rewizję, kupon oficjalny zostaje, definicja zapisana w propozycji;
13. każda decyzja i jej cofnięcie to nowy wpis, liczba otwartych decyzji, odmowa
    `UPDATE`/`DELETE`;
14. bez kliknięcia nic się nie zmienia (ustawienia, tabele); Ustawienia zapisują tylko zmienione
    decyzje;
15. challengery „otwarte” dopiero po progu;
16. system nigdy sam nie podejmuje decyzji (przeszukanie `src/`);
17. wiek kursów dziś: liczby poprawne, brak meczów, „za mało danych (1 dzień…)”;
18. migracja v10 → v11 (kopia, liczby wierszy, triggery);
19. AppTest: Dashboard pokazuje „Decyzje do podjęcia” (otwarte 4 z 5), panel ma pięć pozycji,
    decyzja o opcji D przyciskiem za potwierdzeniem.

**Zmienione istniejące testy** (żaden nie został wyłączony):

- `test_task012.py` (migracja v9 → v10) i `test_task013.py` (schemat): `SCHEMA_VERSION >= 10`
  zamiast `== 10`, nazwa kopii i wpis migracji z bieżącą wersją — tak jak TASK 011/012 robiły z
  wcześniejszymi testami migracji;
- `test_task013.py`: lista plików wywołujących `accept_gate(` obejmuje `decisions.py`, który
  wywołuje go wyłącznie z przycisku panelu (test 16);
- `test_ui_paths.py` oczekuje 3 nowych ścieżek QA.

Kod testowano z worktree (`conftest` wstawia własny `src`; dla QA, Playwright i pomiarów
`PYTHONPATH=<worktree>\src`).

| Kontrola | Wynik |
|---|---|
| `pytest -q` | **452 passed, 4 skipped** (456 testów; pominięte: brak lokalnych autentycznych PDF STS), 494 s |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 182 pliki sformatowane |
| `python -m sports_edge.qa` (ścieżki UI, DEMO) | **37/37 OK**, w tym nowe: „dashboard: decyzje do podjęcia”, „modele: decyzje do podjęcia (5 pozycji)”, „decyzje: opcja D i jej cofnięcie (dwa wpisy historii)” |
| `python -m sports_edge.qa_visual` (Playwright, DEMO) | **98 kontroli, 0 błędów** (14 stron × 5 rozdzielczości: 1920, 1366, 1024, 768, 390 px + scenariusze; nowy w każdej rozdzielczości: „Dashboard i Modele: Decyzje do podjęcia” — sekcja na Dashboardzie i pięć pozycji panelu; scenariusz Ustawień sprawdza też radio opcji D). Jeden z przebiegów kontrolnych dał 93/98: scenariusz decyzji mierzył stronę, zanim panel skończył się liczyć przy pustej pamięci podręcznej (kilka sekund — dowody liczone na żywo). Scenariusz czeka teraz na ostatnią pozycję panelu, a panel odświeża pamięć podręczną co 10 minut (lub po zmianie danych, ustawień albo decyzji); powtórka: 98/0 |
| `python -m sports_edge.qa_browser` (Playwright, DEMO) | 22 zrzuty, 0 błędów |
| Playwright na kopii: Dashboard, Modele (Decyzje do podjęcia, Uczenie AI), Ustawienia × 5 rozdzielczości | **19/20 OK**; Uczenie AI w 390 px: pasek narzędzi tabeli („Show/hide columns”) poza ekranem — pojawia się po najechaniu myszą (2 z 4 przebiegów), przy bezpośrednim pomiarze po załadowaniu 0 pasków poza ekranem; to samo zjawisko co w TASK 013, niezależne od nowych kolumn (tabela ma szerokość ekranu i własne przewijanie) |

**Przegląd ręczny zrzutów z kopii** (zrzutów nie dodano do repozytorium; usunięte z kopią):
- Dashboard: pod paskiem stanu linia „Kursy na dziś (49 meczów 12 lig, przed startem 49):
  ostatni dokument STS z 09:25 — 0,6 h temu · pobrania w dniu meczu dziś: 1/12.”, a niżej sekcja
  „Decyzje do podjęcia — otwarte: 3 z 5” z trzema nazwami i odnośnikiem do panelu.
- Modele → Decyzje do podjęcia (1366 i 390 px): pięć pozycji z kartami „Co zmienia / Dowody /
  Ryzyko”, bramka „zaakceptowana (decyzja z 08.11.2026 09:24)”, szacunki dat n/300, przyciski
  nieaktywne do zaznaczenia „Potwierdzam…”; w 390 px tekst się zawija, nic nie wychodzi poza
  ekran.
- Uczenie AI: kolumny „Dowody n/300” i „Szacowana data 300 meczów” oraz linie szacunku pod
  tabelą; challengery v3 „czeka na dowody (0/300)”.
- Ustawienia: radio „Granica „ostrożnie” podwójnej szansy Elo” z dzisiejszą definicją.
- Na kopii nie zapisano żadnej decyzji ani aktywacji przez interfejs; worker kopii nie
  wystartował (stan „stopped”).

## 7. Ograniczenia

- **„Identyczny” to interpretacja holdoutu v2.** Holdout v2 sięga do daty treningu, więc
  dosłownie te same liczby są możliwe tylko bez nowego meczu. Przyjęto: wszystkie części modelu
  równe, a holdout to samo okno poszerzone tylko o nowsze mecze. Dodatkowo zabezpieczenie na
  zapisanych prognozach. Przy dosłownej regule challengery z 06.11 wygasłyby dopiero 20.11.
- **Pierwszy v3 pomija też warunek 7 dni** (nie tylko 150 meczów). Inaczej trening v2 z 06.11
  wstrzymałby go do 13.11. Kolejne v3 — zwykłe warunki.
- **Szacunek daty** opiera się na ok. 7 dniach zapisów prospektywnych z dwoma weekendami i nie
  uwzględnia terminarza. Przerwa reprezentacyjna 09–15.11 przesunie go co najmniej o tydzień.
- **Opcja D zmierzona na 24 decyzjach** ze starymi kursami i na jednej decyzji z dzisiejszymi
  świeżymi kursami. 1 kupon (i 1 dziś) to pojedyncze obserwacje. Zapas granicy z historii
  (TASK 014) jest wąski: +2,4–2,8 pkt.
- **Wiek kursów niezmierzony** — 1 dzień trybu „w dniu meczu”, a najbliższe dni bez meczów
  5 lig. Dla 7 lig brak terminarza.
- **Pierwszy przegląd wymaga sieci i włączonego treningu.** U Ciebie trening jest włączony.
  Offline przegląd jest pomijany i planuje się przy następnym starcie.
- **Bramka v3 jest już zaakceptowana** (08.11 09:24). Aktywacja v3 nadal wymaga 300 wspólnych
  meczów i Twojego kliknięcia.

## 8. Proponowany TASK 016

1. **Wiek kursów po 3 dniach trybu „w dniu meczu”** (najwcześniej po przerwie reprezentacyjnej):
   mediana, udział ≤ 3 h, pobrania dziennie, pułap; powtórzona diagnoza WYSOKIEJ TRAFNOŚCI na
   dniach ze świeżymi kursami (08.11 dał pierwszy kupon as-of przy obu definicjach).
2. **Szacunek n/300 z terminarzem:** tempo z 14 dni skorygowane o przerwy w terminarzu (5 lig) i
   ofertę STS. Potem pierwsze wspólne rozliczenia v3 kontra v2 (różnica log loss na tych samych
   meczach).
3. **Decyzje z panelu** (opcja D, PEWNY SOLO, limit). Przy opcji D osobne „Oczekiwane kontra
   faktyczne” dla kuponów z definicją `single` (pole `cautious_definition`).
4. **Wariant B** (rekalibracja na rozliczeniach) jako pomiar po 300 rozliczonych meczach
   aktywnych wersji w zakresie (dziś 40 i 61).
5. **Korelacja kuponów dnia** w ocenie „oczekiwane kontra faktyczne” (dni ze wspólną nogą).

## Instrukcja dla Ciebie

Po tej aktualizacji (`G:\SportAI` zaktualizowany do commitu z tego raportu):

1. **Zamknij i uruchom ponownie aplikację i workera.** Przy pierwszym starcie baza przejdzie na
   schemat v11 i powstanie kopia `backups\pre-migration-v11_….db`.
2. **Pierwszy przegląd z challengerami v3 wykona się automatycznie w ciągu godziny** (planowany
   10 minut po starcie workera). Wygasną identyczne challengery v2, powstaną challengery v3 w obu
   zakresach i od razu ich prognozy cienia. Warunek: tryb online i włączony „Trening
   challengera” w Automatyzacji AI (u Ciebie jest włączony).
3. Potem zajrzyj do **Modele i statystyki → Decyzje do podjęcia**: 3 decyzje czekają na Ciebie,
   a n/300 z szacowaną datą widać przy challengerach. Nic nie zmieni się bez Twojego kliknięcia.
