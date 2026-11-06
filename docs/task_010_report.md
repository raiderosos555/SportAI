# TASK 010 — Jeden kupon, jedna historia

Deduplikacja kuponów dnia i wiarygodne statystyki. Commit bazowy: `522dfda` (HOTFIX 009.1).
Zmiany nie są zatwierdzone — bez commitu i pusha, zgodnie z zasadą 0.

Pomiary na prawdziwych danych wykonano na **kopii** prywatnej bazy w katalogu tymczasowym
(kopia przez API backupu SQLite, tylko do odczytu oryginału); kopia została potem usunięta.

**Uwaga — prawdziwa baza jest już w wersji 8.** 06.11 o 18:42 (czas polski) aplikacja
użytkownika lub jej worker, uruchomione z katalogu roboczego z kodem TASK 010, same otworzyły
bazę nowym kodem:

* migracja v7 → v8 z kopią `backups/pre-migration-v8_2026-11-06_17-42-07.db` (v7, 18 kuponów);
* backfill z tym samym wynikiem co na kopii: 9 oficjalnych, 1 użytkownika, 3 duplikaty,
  3 aktualizacje kursów, 2 rewizje;
* analiza o 18:42 dopisała `price_update` (STANDARDOWY) i `revision` (WYŻSZA WYPŁATA);
* żadnego nowego kuponu oficjalnego, nadal najwyżej jeden oficjalny na profil i dzień.

Kod sprzed TASK 010 odmówi otwarcia bazy v8 („Database was created by a newer version”).
Powrót do starego kodu wymaga więc przywrócenia tej kopii zapasowej.

## 0. Kryteria odbioru

| Kryterium | Stan | Dowód |
|---|---|---|
| Nowy PDF ani ponowna analiza nie tworzą nowego kuponu oficjalnego z tymi samymi meczami | spełnione | testy 1–2 (§9); kopia bazy: nowszy PDF → `price_update`/`revision`, 2× „Analizuj” → 20 → 20 → 20 kuponów (§6) |
| Jeden profil w jednym dniu ma najwyżej jeden kupon oficjalny | spełnione | `max_officials_per_profile_day = 1` po backfillu i po każdej analizie na kopii |
| Rewizje i aktualizacje kursów są zapisane, widoczne i powiązane z oficjalnym | spełnione | `coupon_lineage.official_coupon_id`; Kupon dnia (plakietki, różnice), Historia → „Wersje” (§8) |
| Żaden istniejący rekord nie został usunięty ani zmieniony | spełnione | nowa tabela append-only; migracja kopii: liczby wierszy wszystkich tabel bez zmian (§3) |
| Statystyki, kalibracja i ROI liczą każdy kupon i każdą selekcję raz | spełnione | §5; testy 6–7 |
| Jedna zmiana przed meczem daje jeden alert | spełnione | test 8 (trzy kupony z różnym kursem wyjściowym → 1 alert); §5.4 |
| Podwójne kliknięcie nie tworzy drugiego kuponu | spełnione | test 10 (Mój kupon, Historia, Diagnostyka, `real_manual`) |
| Ekspozycja widoczna, opcja różnicowania istnieje i jest domyślnie wyłączona | spełnione | „ten mecz jest też w: …”, blok „Ryzyko dnia”; `max_profiles_per_selection = 0` |
| Wszystkie testy przechodzą, Ruff bez uwag, Playwright bez błędów | spełnione | §9 |
| Raport zawiera liczby z kopii prawdziwej bazy | spełnione | §4–§7 |

## 1. Przyczyny duplikatów i co zmieniono

### 1.1 Nowy PDF STS = nowy kupon (błąd główny)

* **Przyczyna.**
  * `services.save_coupon_proposals` budował klucz z identyfikatorów kursów:
    `digest(["proposal", name, sorted(odds_ids), rules.version, day])` (`522dfda`,
    `services.py:1575`).
  * `odds_id` zależy od importu: `repository._insert_row` liczy
    `quote_key = digest(["quote", market_key, import_key, …])` (`repository.py:166`), a z niego
    `odds id` (`repository.py:192`).
  * Każdy dokument STS dawał więc nowe `odds_id` tej samej selekcji i nowy kupon.
* **Zmiana.** Tożsamość kuponu liczy jedna funkcja, `coupon_store.coupon_identity`
  (`coupon_store.py:74`): źródło + profil + dzień analizy + wersja polityki + posortowane
  `selection_id`. `odds_id`, import, kurs i stawka do niej nie wchodzą — kurs i stawka są
  atrybutami wersji. Zapis kreatora (`coupon_store.save_builder`, `coupon_store.py:216`):

  | Sytuacja | Wynik zapisu |
  |---|---|
  | pierwszy zapis profilu w dniu | `official` (kupon dnia) |
  | ta sama tożsamość i kursy, jak w którejkolwiek zapisanej wersji | `duplicate` — nic nowego, zwrot istniejącego kuponu |
  | ta sama tożsamość, inne kursy (nowy PDF) | `price_update` powiązany z oficjalnym |
  | inne nogi tego samego profilu i dnia | `revision` powiązana z oficjalnym |

  Duplikat jest porównywany z każdą zapisaną wersją, nie tylko z oficjalną. Gdy kurs wróci do
  poprzedniej wartości, nie powstaje kolejna kopia.

### 1.2 Każde „Analizuj” dopisywało warianty, a statystyki liczyły wszystkie

* **Przyczyna.**
  * `run_analysis(..., save_coupons=True)` zapisywał każdą propozycję.
  * `pages_history.newest_variants` jedynie oznaczał starsze warianty jako historyczne.
  * Wszystkie kupony wchodziły do `coupon_performance`, `paper.performance`,
    `selection_outcomes` i paneli Historii.
* **Zmiana.** Warunek `coupon_store.counted_sql()` (`coupon_store.py:60`): liczą się kupony
  `official` i `user` (oraz kupon bez wpisu w `coupon_lineage`, dla zgodności wstecz). Używają
  go:
  * `performance.builder_coupons` i `coupon_performance` (`performance.py:32`, `:87`);
  * `paper.performance` (`paper.py:419`);
  * `coupon_store.saved_proposals`;
  * `reassessment.active_coupons` (`reassessment.py:25`);
  * Dashboard („Kupony oczekujące na rozliczenie” przez `builder_coupons`).

  `selection_outcomes` deduplikuje po `selection_id` (`performance.py:131`), a docstring
  mówi teraz prawdę. Rewizje i aktualizacje kursów mają osobny wiersz „tylko diagnostyka”
  (`coupon_performance(versions=True)`). Rozliczane są nadal wszystkie kupony.

### 1.3 Duplikaty mnożyły alerty i pracę workera

* **Przyczyna.**
  * `alerts.create` liczył `alert_id = digest(["alert", kind, event_id, coupon_id, …])`
    (`522dfda`, `alerts.py:58`), więc ta sama zmiana dawała osobny alert dla każdej kopii.
  * `reassessment.active_coupons` brał wszystkie nierozliczone kupony.
* **Zmiana.**
  * `reassess` zbiera **fakty** (rodzaj, wydarzenie, selekcja, treść zmiany).
  * `reassessment.alert_facts` (`reassessment.py:174`) robi z każdego faktu jeden alert
    z listą kuponów w `payload.coupons` („… — dotyczy kuponów: N (…)”).
  * Ruch kursu jest faktem „kurs zmienił się na X”. Kupony zapisane po 1,53 i po 1,55 dostają
    jeden alert, gdy rynek przejdzie na 1,65. Sama ocena kuponu zachowuje swój kurs („z 1,55
    na 1,65”).
  * Ten przypadek wyszedł na kopii bazy: jeden ruch Atalanta–Parma dał tam 3 alerty.
  * `prematch_targets` bez zmian: `SELECT DISTINCT` — jedno sprawdzenie składu na mecz.

### 1.4 Ten sam mecz w kilku profilach

* Kupon dnia pokazuje ekspozycję: przy nodze „ten mecz jest też w: …”
  (`coupon_view._exposed`).
* Blok **„Ryzyko dnia”** (`page_coupon._day_risk`) wymienia mecze obecne w kilku kuponach i
  liczy, ile kuponów przegrywa, jeśli typ nie wejdzie. Przy różnych typach jednego meczu mówi
  o skorelowanych wynikach.
* Opcja różnicowania: §7.

### 1.5 Ręczne zapisy bez idempotencji

* **Przyczyna.**
  * Domyślny klucz `paper.create_coupon` zawierał `iso(now)` (`522dfda`, `paper.py:101`).
  * Bez własnego klucza wołały go: Historia → „Zapisz kupon” (`pages_history.py:149`),
    Diagnostyka (`app/diagnostics.py:145`) i `save_my_coupon`.
* **Zmiana.**
  * `coupon_store.save_manual` (`coupon_store.py:470`) z kluczem `manual_key`: tryb +
    posortowane `selection_id` + stawka + dzień (Europe/Warsaw), bez zegara.
  * Ponowne kliknięcie zwraca istniejący kupon i komunikat „Ten kupon jest już zapisany”.
  * Drugi identyczny kupon `real_manual` (postawiony dwa razy w STS) zapisuje się tylko po
    zaznaczeniu „Postawiłem identyczny kupon w STS jeszcze raz” w Moim kuponie.

### 1.6 Błędy znalezione podczas weryfikacji (kopia bazy i Playwright)

Wszystkie poprawione i pokryte testami:

1. **Panele skuteczności padały.**
   * Rozliczony „Mój kupon” na rynku bez zwalidowanego modelu ma szansę `None`.
   * `coupon_performance` (już w `522dfda`) kończył się `TypeError`. Padała zakładka
     „Skuteczność kreatora kuponów” i strona Uczenia AI.
   * Teraz średnie liczą tylko znane szanse.
   * `selection_summary` pomija selekcje bez prognozy, bo nie są obserwacją kalibracji.
2. **Szczegóły kuponu w Historii** szukały wyniku nogi po `odds_id` w `selection_outcomes`.
   * Po deduplikacji po `selection_id` część nóg pokazywałaby „czeka na wynik”.
   * Nowa funkcja `performance.leg_outcomes` (`performance.py:171`) liczy wyniki nóg danego
     kuponu, także wersji spoza statystyk.
3. **Komunikat duplikatu późniejszej wersji.**
   * Gdy analiza powtarzała rewizję, komunikat brzmiał „kupon dnia z 08:55 jest aktualny”,
     a kupon z 08:55 miał inne nogi.
   * Teraz: „Bez zmian — rewizja z 16:12 jest aktualna; kupon dnia z 08:55 pozostaje
     w statystykach”. Karta pokazuje plakietkę tej wersji.
4. **Linia różnic.**
   * Wypłata była liczona brutto (stawka × kurs), niezgodnie z kafelkiem karty (po
     współczynniku 0,88).
   * Typy były surowymi kluczami („total 1.5 over”).
   * Teraz: wypłata z zapisanej propozycji i „Powyżej 1,5 gola”.
5. **Przycisk „DODAJ DO MOJEGO KUPONU”** był ucięty przy 768 px. Wykrył to nowy scenariusz
   Playwright, bo wcześniej QA nigdy nie pokazywało karty kuponu. Poprawka: reguła CSS dla
   `row-actions` w `ui.py`.

## 2. Reguła kuponu oficjalnego

* `config/probability_policy.toml`, sekcja `[policy]`: `official_rule = "first"` (pole
  `ProbabilityPolicy.official_rule`). Wersja polityki bez zmian: `2026-11-02.p3`.
* Kuponem oficjalnym jest **pierwszy zapisany kupon profilu w danym dniu**.
  * Zapis jest odrzucany, gdy któryś mecz już się zaczął (`create_coupon`), więc oficjalny
    kupon zawsze powstaje przed swoim pierwszym meczem.
  * Rewizje i aktualizacje kursów są przechowywane i pokazywane, ale nigdy go nie zastępują.
* **Dlaczego zakaz podmiany po rozpoczęciu meczu:**
  * reguła, która mogłaby wymienić kupon po starcie któregoś meczu, pozwalałaby wybierać po
    fakcie — np. zastąpić kupon wersją bez nogi, która już przegrywa;
  * skuteczność byłaby wtedy zawyżona przez wiedzę niedostępną w chwili decyzji;
  * reguła „first” jest najprostszą regułą bez tej możliwości: decyzja zapada raz, przed
    meczami.
* **Wariant alternatywny — opis i pomiar na kopii, bez zmiany domyślnej reguły.**
  * Reguła: „ostatnia wersja zapisana przed pierwszym meczem kuponu, który zastępuje”.
  * W kopii zmieniłaby kupon oficjalny w 4 dniach WYŻSZEJ WYPŁATY.
  * Rozliczone: „first” — 3 kupony, 2 wygrane, +14,37 zł (ROI 47,9%); „ostatnia wersja” —
    3 kupony, 1 wygrana, −7,82 zł (ROI −26,1%).
  * Przy trzech rozliczeniach to szum. Pokazuje jednak, że regułę trzeba ustalić z góry, a nie
    dobierać po wynikach. Dlatego jest jawna w pliku polityki i nie zmienia się bez decyzji
    użytkownika.

## 3. Migracja v8 i backfill

* `SCHEMA_VERSION = 8`, opis w `MIGRATIONS[8]`. Nowa tabela append-only `coupon_lineage`:

  ```text
  id, coupon_id (UNIQUE), official_coupon_id, relation, identity_key, slot_key,
  recorded_at, reason
  ```

  * `relation` ∈ `official`, `duplicate`, `price_update`, `revision`, `user`;
  * indeksy po (`slot_key`, `relation`) i po `official_coupon_id`;
  * triggery `immutable_coupon_lineage_UPDATE/DELETE` tworzy `Store` jak dla każdej tabeli.
* Kopia zapasowa `pre-migration-v8_*.db` powstaje przed migracją (istniejący mechanizm
  `Store`). Na kopii bazy:
  * powstała `pre-migration-v8_2026-11-06_15-04-52.db`;
  * liczba wierszy **każdej** istniejącej tabeli bez zmian;
  * dopisany jeden wiersz `schema_migrations`.
* **Backfill:** `coupon_store.backfill_lineage` (`coupon_store.py:621`). Uruchamia go
  `open_store` przy każdym starcie aplikacji albo komenda `sports-edge lineage`. Jest
  idempotentny — tylko dopisuje brakujące wiersze.
  * **Kupony kreatora** grupuje po tożsamości z §1.1 w kolejności zapisu:
    * pierwszy w profilu i dniu = `official`;
    * ta sama tożsamość i kursy = `duplicate`;
    * inne kursy = `price_update`;
    * inne nogi = `revision`.
  * **Kupony użytkownika** grupuje po kluczu z §1.5: pierwszy = `user`, kolejne =
    `duplicate`.
  * Żaden kupon nie jest usuwany.
* Drugi przebieg backfillu na kopii nie dopisał nic (`{}`).

## 4. Kopia prawdziwej bazy — duplikaty, aktualizacje kursów, rewizje

Kopia ma 18 kuponów (17 kreatora, 1 rzeczywisty „Mój kupon”) z dni 02–06.11.

| Relacja | Łącznie | Opis |
|---|---|---|
| `official` | 9 | kupon dnia profilu |
| `user` | 1 | „Mój kupon” (postawiony w STS) |
| `duplicate` | 3 | te same nogi i kursy |
| `price_update` | 3 | te same nogi, nowy PDF z innym kursem |
| `revision` | 2 | inne nogi tego samego profilu i dnia |

| Profil | official | duplicate | price_update | revision |
|---|---|---|---|---|
| WYŻSZA WYPŁATA | 4 | 1 | 3 | 2 |
| STANDARDOWY | 2 | 2 | — | — |
| Bezpieczny / Zrównoważony / Agresywny (profile polityki p2) | po 1 | — | — | — |

| Dzień | official | duplicate | price_update | revision | user |
|---|---|---|---|---|---|
| 02.11 | 5 | 1 | 1 | 1 | 1 |
| 03.11 | 1 | — | — | 1 | — |
| 04.11 | 1 | — | 1 | — | — |
| 06.11 | 2 | 2 | 1 | — | — |

**Przykład 06.11.** Trzy PDF-y „oferta-dzisiaj”: 08:55, 09:40 i 10:37.

* 08:55 — kupon oficjalny WYŻSZEJ WYPŁATY.
* 09:40 — te same nogi, kurs jednej z 1,50 na 1,47 (`price_update`); pierwszy STANDARDOWY.
* 10:37 — oba bez zmian (2× `duplicate`).

## 5. Statystyki przed i po deduplikacji (kopia bazy)

„Przed” to kod `522dfda`. Wywracał się on na kuponie bez szansy modelu (§1.6), więc do
pomiaru dodano tylko tę samą poprawkę średnich. Liczby kuponów, trafność, ROI i serie się od
niej nie zmieniają.

### 5.1 Skuteczność kreatora per profil

| Profil | Kupony | Rozliczone | Wygrane | Trafność | Zysk netto | ROI | Maks. obsunięcie | Najdł. seria W / P | Bieżąca seria |
|---|---|---|---|---|---|---|---|---|---|
| WYŻSZA WYPŁATA — przed | 10 | 7 | 3 | 42,9% | −3,45 zł | −4,9% | 20,00 zł | 2 / 2 | 2 × przegrana |
| WYŻSZA WYPŁATA — po | 4 | 3 | 2 | 66,7% | +14,37 zł | +47,9% | 10,00 zł | 2 / 1 | 1 × przegrana |
| STANDARDOWY — przed | 4 | 2 | 2 | 100% | +13,52 zł | +67,6% | 0 | 2 / 0 | 2 × wygrana |
| STANDARDOWY — po | 2 | 1 | 1 | 100% | +6,76 zł | +67,6% | 0 | 1 / 0 | 1 × wygrana |

Bezpieczny, Zrównoważony, Agresywny i Mój kupon (po 1 kuponie) się nie zmieniają.

Trafność WYŻSZEJ WYPŁATY ma 95% przedział Wilsona 16–75% przed i 21–94% po. Trzy
rozliczenia nic nie mówią o jakości modelu. Zmiana wynika wyłącznie z tego, że przegrane
duplikaty i wersje liczyły się wcześniej wielokrotnie.

### 5.2 ROI per tryb (`paper.performance`)

| Tryb | Kupony | Rozliczone | Stawki | Zysk netto | ROI |
|---|---|---|---|---|---|
| paper trading (kreator) — przed | 17 | 12 | 120,00 zł | +29,51 zł | 24,6% |
| paper trading (kreator) — po | 9 | 7 | 70,00 zł | +40,57 zł | 58,0% |
| rzeczywisty (ręczny) | 1 | 1 | 10,00 zł | −10,00 zł | −100% (bez zmian) |

### 5.3 Kalibracja selekcji (`selection_outcomes`)

| | Selekcje | Rozliczone | Trafione | Trafność | Śr. szansa modelu | Śr. szansa ostrożnie |
|---|---|---|---|---|---|---|
| przed (po `odds_id`) | 37 | 24 | 19 | 79,2% | 75,9% | 71,0% |
| po (po `selection_id`) | 17 | 12 | 10 | 83,3% | 77,2% | 72,0% |

Przed zmianą ta sama selekcja z różnych PDF-ów i kopii kuponów liczyła się wielokrotnie:
37 wierszy kalibracji opisywało 17 różnych selekcji.

### 5.4 Kontrola przed meczem i alerty

* Aktywne kupony sprawdzane przez `reassess_all` w chwili pomiaru: 5 → 2 (tylko oficjalne
  i użytkownika).
* Istniejące alerty kuponowe w kopii (stary format, jeden na kupon):
  * 17 alertów dotyczyło 9 kuponów;
  * 7 z nich dotyczyło duplikatów i wersji;
  * wymieniały 37 powodów (najwyżej 3 na alert), a różnych zmian było 14.

  Według nowych reguł każda z tych zmian daje jeden alert z listą kuponów.
* Stare alerty zostają w bazie bez zmian (append-only).

## 6. „Analizuj” na kopii bazy

1. **Dwa razy bez nowego PDF (offline).**
   * Wynik: 18 → 18 → 18 kuponów, żadnej propozycji.
   * Ostatni dokument STS z 10:35 miał 5,5 h, a limit świeżości to 180 min. Kreator słusznie
     nie budował kuponów — to nie był test deduplikacji.
2. **Nowszy PDF.**
   * Pobrano oficjalny PDF STS „oferta na dziś” z 16:10 (331 wydarzeń) — funkcją aplikacji
     z włączoną zgodą użytkownika, jednym żądaniem, **do kopii**.
   * Analiza: 18 → 20 kuponów, nowych oficjalnych 0:
     * **STANDARDOWY → `price_update`**, powiązany z kuponem oficjalnym z 09:40.
       Sevilla – Deportivo Alaves, Powyżej 1,5 gola: 1,38 → 1,42. Kurs łączny 1,82 → 1,87,
       wypłata 16,03 → 16,49 zł.
     * **WYŻSZA WYPŁATA → `revision`**, powiązana z kuponem oficjalnym z 08:55. Usunięte:
       Toulouse FC – RC Strasbourg, Powyżej 1,5 gola. Dodane: Willem II Tilburg – PEC Zwolle,
       Powyżej 1,5 gola.
3. **Jeszcze dwa razy bez nowego PDF.**
   * Wynik: 20 → 20 → 20 kuponów, oba zapisy `duplicate`.
   * Komunikaty: „Bez zmian — aktualizacja kursów z 16:12 jest aktualna; kupon dnia z 09:40
     pozostaje w statystykach” oraz „Bez zmian — rewizja z 16:12 jest aktualna; kupon dnia
     z 08:55 pozostaje w statystykach”.
   * Po każdej analizie: najwyżej 1 kupon oficjalny na profil i dzień.
4. **Zrzuty sprawdzone ręcznie** (Playwright na kopii, 1366 i 390 px): Dashboard, Kupon dnia
   przed i po analizie, Historia (domyślnie i z „Wersjami”), Skuteczność kreatora z wierszem
   diagnostycznym wersji.
   * Bez wyjątków i bez błędów układu.
   * Oczekujące na rozliczenie: 2 kupony oficjalne (wcześniej liczyłyby się też kopie).
   * Zrzuty pokazują prywatne dane, więc nie trafiły do repozytorium (jest publiczne).

## 7. Różnicowanie profili — pomiar (pkt 4)

### 7.1 Opcja

* `max_profiles_per_selection` w `[builder]` pliku polityki: `0` = wyłączone (domyślnie).
  Można ją nadpisać w Ustawieniach („Ta sama selekcja najwyżej w N kuponach dnia”).
* Kolejność budowy profili = kolejność w pliku polityki: WYSOKA TRAFNOŚĆ → STANDARDOWY →
  WYŻSZA WYPŁATA → TASIEMKA.
* Selekcja użyta już przez N propozycji nie trafia do następnych profili. Progi profili się
  nie zmieniają.
* Ta sama wartość obowiązuje w analizie i w symulatorze historycznym (`replay_day`).

### 7.2 Pomiar

Pomiar (`sports-edge diversity`, `simulation.diversity_replay`) działa tak:

* jedna analiza as-of na dzień — tylko dane znane w chwili decyzji;
* potem kreator z limitem 0, 1 i 2;
* nic nie jest zapisywane.

**Kopia bazy:** dni 31.10–06.11 × godziny decyzji 09:00–18:00 = 70 decyzji.

| Limit | Kupony | Decyzje z ≥ 1 kuponem | Śr. ostrożna szansa | Jedna porażka przegrywa 2+ kupony | Ten sam mecz w 2+ kuponach (różne typy) | Usunięte / zmienione kupony |
|---|---|---|---|---|---|---|
| bez limitu | 33 | 21 | 46,2% | 0 | 4 | — |
| 1 | 33 | 21 | 46,2% | 0 | 4 | 0 / 0 |
| 2 | 33 | 21 | 46,2% | 0 | 4 | 0 / 0 |

* Na tych danych limit niczego nie zmienia: w odtworzonych decyzjach profile nie wybrały tej
  samej selekcji.
* 4 decyzje z tym samym meczem dotyczą różnych typów jednego meczu. Limit per selekcja ich nie
  obejmuje — pokazuje je blok „Ryzyko dnia”.
* Zapisane kupony oficjalne wyglądały inaczej. 02.11 jedna selekcja była w 4 kuponach naraz:
  profile polityki p2 i p3 tego samego dnia; jej porażka przegrałaby 4 kupony (wygrała).
* Replay 02.11 o 10:00 odtwarza STANDARDOWY z 09:24, ale dla WYŻSZEJ WYPŁATY wersję z 09:54
  (rewizję), a nie kupon oficjalny z 09:24. Wspólna selekcja kuponów z 09:24 nie występuje więc
  w odtworzeniu.

**Dane syntetyczne z prawdziwą polityką** (przypadek z pkt 1.4):

| Pula | Bez limitu: wspólne selekcje / śr. ostrożna szansa | Limit 1 |
|---|---|---|
| HOTFIX 009.1 (71 zwalidowanych + 30 eksperymentalnych) | 2 / 56,0% | 0 / 55,7%; STANDARDOWY 63,7 → 63,5%, TASIEMKA 24,3 → 23,6%; żaden kupon nie znika |
| szeroki zakres kursów (60 zwalidowanych) | 1 / 67,8% | 0 / 67,2%; WYŻSZA WYPŁATA 64,31 → 64,29%, TASIEMKA 46,2 → 43,9% |

* Limit 2 daje tu wynik identyczny z „bez limitu”, bo najwyżej dwa kupony dzieliły selekcję.
* Wniosek opisowy: limit 1 usuwa wspólne selekcje za cenę niewielkiego spadku ostrożnej szansy
  dalszych profili.
* Bez rekomendacji zysku. Ustawienie domyślne zostaje wyłączone do decyzji użytkownika.

## 8. Interfejs (tylko to, co wynika z deduplikacji)

* **Kupon dnia:**
  * plakietki „Oficjalny kupon dnia”, „Rewizja z HH:MM”, „Kursy zaktualizowane (PDF z HH:MM)”;
  * różnice względem kuponu oficjalnego: dodane i usunięte nogi, zmiany kursów, kurs łączny
    i wypłata;
  * „Bez zmian — kupon dnia z HH:MM jest aktualny” zamiast nowej karty;
  * „Nowsza wersja: …” przy zapisanych propozycjach;
  * ekspozycja meczu i blok „Ryzyko dnia”.
* **Historia:**
  * lista „Kupony” domyślnie pokazuje kupony oficjalne i użytkownika;
  * przełącznik „Wersje” grupuje rewizje, aktualizacje kursów i duplikaty pod kuponem
    oficjalnym („↳”, „… (poza statystykami)”);
  * zakładka „Kupony wirtualne” ma kolumnę „Relacja” i przełącznik „Wersje i duplikaty”;
  * wiersz „Rewizje i aktualizacje kursów — tylko diagnostyka”.
* **Dashboard:** identyczne propozycje grupowane po `selection_id` (`page_dashboard._proposals`).
* **Mój kupon, Historia, Diagnostyka:** „Ten kupon jest już zapisany”. Drugi identyczny kupon
  rzeczywisty tylko po zaznaczeniu potwierdzenia.
* **Ustawienia:** limit różnicowania (0 = bez limitu).
* **Porządki P2:**
  * zapis kuponów przeniesiony do `sports_edge/coupon_store.py`, a `services.py` zostawia
    re-eksporty z niezmienionymi sygnaturami;
  * README: repozytorium jest publiczne, odnośnik „Stan i ograniczenia” prowadzi do tego
    raportu;
  * `.gitignore`: `*.pdf` i `*.xlsx` (z wyjątkiem `tests/fixtures/**`), sprawdzane testem.

## 9. Wyniki testów

* **pytest:** 356 testów, wszystkie przechodzą (338 wcześniejszych + 18 w
  `tests/test_task010.py`). Żaden istniejący test nie został wyłączony.
* **Ruff:** `ruff check .` i `ruff format --check .` bez uwag.
* **Ścieżki interfejsu** (`python -m sports_edge.qa`): 25/25.
* **Playwright:**
  * `qa_browser`: 22 zrzuty, 0 błędów;
  * `qa_visual`: 83 kontrole, 0 błędów — 14 stron × 5 rozdzielczości i scenariusze, w tym nowy
    scenariusz „Kupon dnia: kupony DEMO i Ryzyko dnia” we wszystkich 5 rozdzielczościach
    (asercje: „Ryzyko dnia”, „Oficjalny kupon dnia”, „ten mecz jest też w”).

Testy `tests/test_task010.py` (numeracja jak w pkt 7 specyfikacji):

1. Dwa importy tej samej oferty i trzy zapisy propozycji. Wynik: 1 kupon oficjalny, zapisy
   zwracają `duplicate` i komunikat „Bez zmian”. `coupon_performance` liczy 1 kupon,
   `selection_outcomes` 2 selekcje.
2. Drugi import z innym kursem daje `price_update`:
   * różnice kursu i wypłaty jak na karcie;
   * etykiety typów słowami;
   * ponowny zapis to `duplicate` tej aktualizacji.
3. Inne nogi dają `revision`, kupon oficjalny się nie zmienia. Inny profil tego dnia dostaje
   własny kupon oficjalny.
4. Zapis po rozpoczęciu meczu kuponu oficjalnego nie tworzy nowego oficjalnego.
   `official_rule == "first"`.
5. Backfill na bazie z duplikatami starego formatu: `official` + `duplicate`, drugi przebieg
   pusty, nic nie usunięte.
6. i 7. `coupon_performance`, `paper.performance` i `selection_outcomes` liczą raz. Rozliczenie
   duplikatu nie zmienia ROI ani trafności, a rozliczenie zostaje zapisane.
8. `reassess_all` sprawdza oficjalny kupon i kupony użytkownika raz, pomija duplikat:
   * trzy kupony z różnym kursem wyjściowym → jeden alert z listą trzech kuponów;
   * ponowny przebieg → 0 alertów;
   * `prematch_targets` → każdy mecz raz.
9. Podwójne kliknięcie:
   * Historia, Diagnostyka i `real_manual` → 1 kupon;
   * świadoma kopia `real_manual` → 2 kupony;
   * Mój kupon (`save_my_coupon_result`) → 1 kupon.
10. `max_profiles_per_selection`:
    * wyłączony daje dokładnie wyniki kreatora z danych `test_task009_1.py`;
    * limit 1 nigdy nie jest przekroczony i nie obniża progów.
11. Migracja v7 → v8 tworzy `pre-migration-v8_*.db`, liczba wierszy każdej tabeli bez zmian,
    triggery append-only istnieją.
12. `git check-ignore`: baza, PDF poza `tests/fixtures`, `.env`, eksporty i kopie są
    ignorowane, a kod i fixture nie.
13. Interfejs:
    * karta: plakietki, „Bez zmian”, duplikat rewizji;
    * Historia (AppTest) domyślnie ukrywa duplikat, a „Wersje” go pokazują;
    * Kupon dnia (AppTest): „Oficjalny kupon dnia”, a po drugiej analizie „Bez zmian — kupon
      dnia z 13:30 jest aktualny” i nadal 1 kupon.
14. Regresje z kopii bazy:
    * rozliczony kupon bez prognozy nie wywraca paneli;
    * wyniki nóg dla kuponów z tą samą selekcją z różnych importów;
    * Historia działa z takim kuponem;
    * pomiar różnicowania niczego nie zapisuje.

## 10. Ograniczenia

* **Małe próby.** Kilka rozliczonych kuponów na profil daje przedziały ufności szerokie na
  dziesiątki punktów. Zmiany w §5 to korekta liczenia, nie ocena modeli.
* **Backfill dni sprzed TASK 010.** Kupon oficjalny to pierwszy zapisany kupon. Interfejs
  pokazywał wtedy najnowszy wariant, więc użytkownik mógł widzieć inną wersję niż ta, która
  liczy się teraz w statystykach (np. 02.11 i 03.11 dla WYŻSZEJ WYPŁATY).
* **Alert jest jeden na fakt na zawsze.** Kupon zapisany później z tą samą selekcją nie dostaje
  ponownego alertu o tej samej zmianie. Jego ocena przedmeczowa nadal wymienia powód.
* **Kontrola przed meczem obejmuje tylko kupony oficjalne i użytkownika.** Mecz występujący
  wyłącznie w rewizji ma nadal sprawdzany skład (`prematch_targets` bez zmian), ale rewizja
  nie dostaje własnej oceny.
* **Klucz ręcznego zapisu obejmuje dzień.** Ten sam kupon zapisany innego dnia to nowy kupon.
* **Stare duplikaty zostają w bazie na zawsze** (append-only). Są rozliczane i widoczne w
  „Wersjach”, ale nie wchodzą do statystyk.
* **Pomiar różnicowania.** Na prawdziwych danych objął 7 dni, a odtworzone decyzje nie miały
  wspólnych selekcji, więc nie rozstrzyga wartości opcji. Decyzje 06.11 po godzinie pomiaru
  używają danych dostępnych w chwili pomiaru.

## 11. Proponowany zakres TASK 011

1. **Okno oceny.** Raport skuteczności dopiero od ≥ 30 rozliczonych kuponów oficjalnych na
   profil, z przedziałem Wilsona na pierwszym planie.
2. **Ryzyko dnia jako liczba.** Wspólna szansa przegranej 2+ kuponów dnia z uwzględnieniem
   wspólnych meczów (kupony dnia jako portfel). Dziś blok jest opisowy.
3. **Decyzja o `official_rule` i różnicowaniu** po zebraniu większej próby. Alternatywną
   regułę i limit 1 warto porównać w powtarzanym replayu as-of, bez zmiany ustawień
   domyślnych bez zgody.
4. **Prezentacja starych alertów** (format sprzed TASK 010: jeden na kupon) zgrupowana
   według zmiany, bez zmiany rekordów.
