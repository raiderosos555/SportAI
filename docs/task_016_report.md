# Raport TASK 016 — higiena danych: rozmiar bazy, kopie zapasowe, alerty bez szumu, absencje ze źródłem

Data pracy: 11–12.11.2026. Kod bazowy: `3c69fd5` (TASK 015). Wszystkie pomiary na prawdziwych
danych wykonano na **kopii** prywatnej bazy (`storage.snapshot_file`, katalog tymczasowy, kopia
z 11.11 ok. 16:10 czasu polskiego). Przed kontrolą interfejsu w kopii ustawiono tryb offline i
zapisano polecenie „stop” dla workera; po pomiarach kopię usunięto. Prawdziwa baza nie była
otwierana nowym kodem. W raporcie są wyłącznie liczby zbiorcze — bez nazw meczów i kuponów.
Raport nie zawiera rekomendacji zysku. Żaden model ani rozgrywka nie zostały aktywowane; progi
profili, polityka walidacji i Twoje decyzje są bez zmian. Nie wykonano żadnego scrapingu i nie
pobrano żadnych danych z Flashscore ani Meczyki.pl.

## 0. Najważniejsze w skrócie

- **Co naprawdę zajmuje bazę.** Największa tabela to nie kursy, tylko `analyses`: 54 wiersze
  zajmują ok. 291 MB (41% z 713 MB), bo każda analiza zapisywała pełną listę wszystkich selekcji
  dnia (5–20 MB na analizę). Kursy (`odds` + `quote_sets`) to ok. 287 MB (40%).
- **Powtórzone kursy: 32,7%** zestawów kursów jest identycznych z poprzednim dokumentem tego
  samego rynku (26,9% przed trybem „w dniu meczu”, 42,4% w dniach 08–10.11, 55,1% w dniu z 13
  dokumentami). To powyżej progu 30%, więc zmiana jest wdrożona.
- **Dwie zmiany zapisu (tylko nowe dane, stare wiersze nietknięte):**
  - niezmieniony kurs nowego dokumentu to lekkie potwierdzenie zamiast kopii (schemat v12);
  - nowa analiza zapisuje pozycja po pozycji tylko selekcje z prognozą modelu (3,9% pozycji).
- **Skutek na kopii** (odtworzenie wszystkich 72 dokumentów nowym kodem): część kursowa
  358,8 → 280,4 MB (−22%; w dniu z 13 dokumentami −41%), część analiz 272,1 → 13,0 MB (−95%).
  Razem te dwie części: 630,9 → 293,4 MB (**−53%**). W dniach 08–10.11 przyrost tych części
  spadłby z ok. 75 do ok. 34 MB dziennie.
- **Odczyty identyczne.** Na prawdziwych danych, baza pełna kontra oszczędna: 12 tabel ofert
  (71 119 wierszy), świeżość, 4000 historii kursów, 11 031 kursów przedmeczowych, porównanie z
  rynkiem (72 grupy, 8102 prognozy) i wiek kursów — **0 różnic**.
- **Kopia cykliczna** działa jako zadanie workera (domyślnie codziennie o 03:30): na kopii
  713 MB w 2,6 s, kontrola integralności OK; retencja 7 dziennych + 4 tygodniowe.
- **Alerty:** 691 zapisanych alertów to po zgrupowaniu 266 pozycji; wszystkie dotyczą meczów już
  rozpoczętych, więc aktualnych ważnych jest 0. Jest przycisk „Oznacz wszystkie jako
  przeczytane” dla każdego alertu, nie tylko widocznych.
- **Absencje:** żadne darmowe API nie potwierdziło absencji bieżącego sezonu, więc importu
  automatycznego nie ma. Jest formularz ręcznego wpisu z obowiązkowym źródłem.
- **Reprezentacje i puchary:** nic nie aktywowano. Model reprezentacji nie przeszedł walidacji
  (kalibracja), a nagłówek STS Ligi Narodów nie jest przypisany do rozgrywek — szczegóły w §6.

## 1. Diagnoza (11.11.2026, kopia z ok. 16:10, kod `3c69fd5`)

- Baza: 713,3 MB, schemat v11, 47 tabel, 786 317 wierszy. Plik WAL: 101 MB.
- Dokumenty STS: 72 (69 pobranych, 3 ręczne). `quote_sets`: 154 237, `odds`: 366 919.
- Ostatnia prognoza: 08.11 20:15; ostatni kupon: 08.11 18:48 (przerwa reprezentacyjna).
- Kopie w `backups/`: tylko `pre-migration-v3…v11`.
- Alerty: 691, wszystkie nieprzeczytane (liczby rodzajów jak w specyfikacji).

**Rozmiar bazy w czasie** (rozmiary kopii sprzed migracji i kopii pomiarowej):

| Moment | Rozmiar | Przyrost |
|---|---|---|
| 02.11 13:14 | 162,0 MB | — |
| 06.11 18:42 | 364,8 MB | ok. 48 MB dziennie (przed trybem „w dniu meczu”) |
| 07.11 22:54 | 442,3 MB | |
| 08.11 11:18 | 504,3 MB | |
| 11.11 ok. 16:10 | 713,3 MB | ok. 65 MB dziennie (po włączeniu trybu, w tym 2 dni przerwy) |

## 2. Rozmiar bazy

### 2.1 Pomiar

**Metoda.** `dbstat` i `sqlite3_analyzer` nie są dostępne. Rozmiar każdej tabeli i indeksu
oszacowano z sumy długości wartości wszystkich wierszy plus narzut rekordu i przeskalowano do
liczby zajętych stron (szacunek pokrył 94% pliku). Udziały są więc przybliżone do kilku procent.

| Tabela | Wiersze | Tabela | Indeksy | Razem | Udział |
|---|---|---|---|---|---|
| `analyses` | 54 | 291,1 MB | 0 | 291,1 MB | 40,8% |
| `odds` | 366 919 | 83,8 MB | 111,2 MB | 195,0 MB | 27,3% |
| `quote_sets` | 154 237 | 62,1 MB | 29,5 MB | 91,6 MB | 12,8% |
| `selections` | 114 887 | 18,0 MB | 18,6 MB | 36,6 MB | 5,1% |
| `historical_results` | 48 551 | 24,6 MB | 5,2 MB | 29,8 MB | 4,2% |
| `predictions` | 19 368 | 23,5 MB | 3,3 MB | 26,8 MB | 3,8% |
| `markets` | 47 878 | 11,5 MB | 7,5 MB | 19,0 MB | 2,7% |
| `prediction_settlements` | 19 368 | 6,5 MB | 4,6 MB | 11,0 MB | 1,5% |
| pozostałe 39 tabel | | | | ok. 12 MB | 1,7% |

- **`analyses`:** 287 289 zapisanych pozycji, z czego prognozę modelu ma 11 147 (3,9%). Reszta to
  wiersze oferty bez prognozy — ich kursy i dokumenty są już w tabelach oferty. Pola `items_json`
  aplikacja nigdzie nie czyta (poza eksportem).
- **`odds`:** trzy indeksy zajmują więcej niż sama tabela. Indeks `odds_quote` (28,6 MB) powtarza
  początek indeksu unikalnego — propozycja w §9.

**Powtórzone kursy** (zestaw kursów rynku identyczny z poprzednim dokumentem: te same ceny, stan,
kod oferty i wiersz źródłowy):

| Okres | Dokumenty | Zestawy kursów | Identyczne | Udział |
|---|---|---|---|---|
| 31.10–07.11 (przed trybem „w dniu meczu”) | 37 | 84 352 | 22 701 | 26,9% |
| 08.11 | 13 | 34 208 | 18 840 | 55,1% |
| 09.11 | 9 | 19 649 | 5 813 | 29,6% |
| 10.11 | 6 | 11 008 | 2 849 | 25,9% |
| 11.11 (do 16:10) | 2 | 5 020 | 259 | 5,2% |
| **Razem** | **67** | **154 237** | **50 462** | **32,7%** |

- Licząc pojedyncze selekcje: 39,4% ma kurs i stan jak poprzednio.
- Udział zależy od odstępu między dokumentami: do 3 h ok. 57–64%, 6–12 h ok. 38%, ponad dobę
  16%. Im częstsze pobrania, tym więcej powtórzeń.
- 1,7% zestawów (2625) to rynek wydrukowany dwa razy w jednym dokumencie z różnymi kursami
  (dwie linie odczytane jako ten sam rynek).

**Prognoza rozmiaru** (przy tempie z 08–11.11, czyli ok. 65 MB dziennie; w weekendy ligowe
tempo jest wyższe, w przerwie niższe — to szacunek, nie pomiar):

| Horyzont | Bez zmian | Po zmianach (ok. 30 MB dziennie) |
|---|---|---|
| 3 miesiące | ok. 6,6 GB | ok. 3,4 GB |
| 12 miesięcy | ok. 24 GB | ok. 12 GB |

### 2.2 Oszczędny zapis nowych importów — wdrożony

- Nowa tabela append-only `quote_confirmations` („dokument X potwierdził zestaw kursów Y”) i
  widok `quote_observations` (zapisane zestawy i ich potwierdzenia). Schemat v12.
- Zestaw kursów identyczny z ostatnim zapisanym dla rynku nie dopisuje wierszy `quote_sets` ani
  `odds` — tylko jeden wiersz potwierdzenia. Zmieniony kurs, zmieniony stan i wycofany rynek
  zapisują się jak dotąd.
- Identyfikator potwierdzenia to identyfikator, który miałby zestaw kursów. Dzięki temu klucze
  kandydatów i migawka porównania z rynkiem są takie same jak przy zapisie pełnym.
- Rynek wydrukowany dwa razy w jednym dokumencie zapisuje się zawsze w całości. Powód: o tym,
  która linia się liczy, decyduje ich kolejność w dokumencie. Pierwsza wersja tego nie
  uwzględniała i odtworzenie na kopii pokazało różnice w 12 z 12 tabel ofert; po poprawce 0.
- Wszystkie odczyty idą przez widok: `offer_table` (też `as_of`), historia kursu selekcji i
  rynku, CLV, kursy przedmeczowe porównania z rynkiem, wiek kursów, zmiany między dokumentami.
- Stare wiersze, także powtórzone, zostają bez zmian.

**Jedyna różnica względem zapisu pełnego:** potwierdzony kurs wskazuje wiersz `odds` dokumentu,
który zapisał go pierwszy. Tożsamość kuponu opiera się na selekcjach, więc się nie zmienia.
Przy wpisie kuponu „po fakcie” warunek „kurs zaobserwowany przed startem meczu” odnosi się więc
do pierwszej obserwacji tego kursu.

### 2.3 Nowe analizy bez pełnej listy oferty — wdrożone

- Nowa analiza zapisuje pozycja po pozycji selekcje z prognozą modelu oraz te wymienione w
  wyniku analizy. Podsumowanie podaje liczbę wszystkich i zapisanych (`items_total`,
  `items_stored`).
- Selekcje bez prognozy da się odtworzyć: `offer_table(as_of=moment analizy)` zwraca ofertę z
  tamtej chwili.
- To zmiana poza literą specyfikacji. Wdrożyłem ją, bo bez niej cel „baza nie rośnie o
  dziesiątki MB dziennie” byłby nieosiągalny: analizy to 41% bazy. Stare analizy są nietknięte.

### 2.4 Plik WAL

- 101 MB to nie wyciek, tylko ślad największego importu: plik WAL nie kurczy się sam.
- Worker wykonuje `wal_checkpoint(TRUNCATE)` po każdym imporcie z nowym dokumentem i przed kopią
  cykliczną. Checkpoint nigdy nie czeka: przy zajętej bazie jest pomijany.

### 2.5 Widoczność

Źródła i ustawienia → „Zbieranie danych i monitoring”: rozmiar bazy, przyrost z 7 dni, wolne
miejsce na dysku i lista kopii cyklicznych. Przyrost liczony jest od znanego wcześniejszego
rozmiaru (kopia zapasowa albo zapis zadania kopii) najbliższego 7 dniom, z podaną datą. Bez
takiego punktu: „brak danych”.

### 2.6 Wynik na kopii (odtworzenie 72 dokumentów nowym kodem)

| Dzień importu | Zapis pełny | Zapis oszczędny | Oszczędność |
|---|---|---|---|
| 02.11 | 30,9 MB | 22,9 MB | 26% |
| 06.11 | 49,9 MB | 38,1 MB | 24% |
| 07.11 | 40,0 MB | 33,0 MB | 18% |
| 08.11 | 70,9 MB | 41,8 MB | 41% |
| 09.11 | 46,2 MB | 37,3 MB | 19% |
| 10.11 | 24,7 MB | 20,0 MB | 19% |
| **Cała historia** | **358,8 MB** | **280,4 MB** | **22%** |

- Zapis oszczędny: 103 786 zestawów, 245 931 kursów i 50 451 potwierdzeń (zamiast 154 237 i
  366 919).
- Oszczędność jest mniejsza niż udział powtórzeń, bo potwierdzenie też zajmuje miejsce, a nowe
  rynki i selekcje zapisują się bez zmian.
- Reszta przyrostu to prawdziwe nowe kursy — historii kursów nie wolno tracić.

**Zgodność odczytów na prawdziwych danych** (baza pełna kontra oszczędna, dni 06–08.11):

| Odczyt | Zakres | Różnice |
|---|---|---|
| `offer_table` z `as_of` (10:00, 14:00, 18:00, 23:00) | 12 tabel, 71 119 wierszy | 0 |
| Świeżość kursu | 71 119 wierszy | 0 |
| Historia kursu selekcji | 4000 selekcji | 0 |
| Kursy przedmeczowe | 3338 wydarzeń, 11 031 kursów | 0 |
| Porównanie z rynkiem (prognozy z kopii) | 72 grupy, 8102 prognozy, do 135 meczów w grupie | 0 |
| Wiek kursów (raport i linia Dashboardu) | 173 mecze | 0 |

Odtworzona baza pełna zgadza się też z samą kopią (ceny, dokumenty i obecność dla 6441 rynków
z 08.11), a wszystkie identyfikatory rynków wyszły takie same.

## 3. Kopie zapasowe

- **Zadanie workera `backup`** — „Kopia zapasowa bazy”. Domyślnie codziennie o 03:30;
  pominięty termin wykonuje się raz po starcie. Widoczne w Automatyzacji AI jak inne zadania
  (przełącznik, harmonogram, historia).
- Kopia powstaje przez API backupu SQLite do `backups/auto-RRRR-MM-DD_GG-MM-SS/` — z
  konfiguracją, ustawieniami, sumami SHA-256 i instrukcją odtworzenia.
- **Bezpieczeństwo:** wolne miejsce co najmniej 2× rozmiar bazy, inaczej zadanie jest pominięte
  z powodem i tworzy alert. Po kopii `PRAGMA quick_check`; błąd tworzy alert, a retencja się nie
  wykonuje.
- **Retencja:** najnowsza kopia z każdego z ostatnich 7 dni i z każdego z ostatnich 4 tygodni.
  Usuwane są wyłącznie foldery `auto-…` z własnym manifestem. Kopie `pre-migration-*`, kopie
  ręczne i rekordy bazy nie są dotykane. Liczby: `config/app.toml`, sekcja `[backup]`.
- **Na kopii:** 713,3 MB w 2,6 s (z sumą kontrolną), `quick_check` 0,7 s — OK; pełny
  `integrity_check` 11,1 s — OK.
- Przy dzisiejszym rozmiarze komplet kopii to do ok. 7 GB (9–10 kopii). Na dysku G: jest 167 GB.

## 4. Alerty

- **Grupowanie:** zmiany składu jednego meczu z jednej kontroli to jeden alert z listą zmian.
  Priorytet: najwyższy ze zmian (zmiana bramkarza — wysoki). Stare alerty zostają i są grupowane
  przy wyświetlaniu.
- **Kolejność:** 1) kupony, 2) składy meczów z kuponów oraz problem z kopią zapasową,
  3) pozostałe. „Ważne” to grupy 1 i 2, o ile mecz się jeszcze nie rozpoczął.
- **Nieaktualne:** alert meczu, który się rozpoczął, jest zwinięty. Starsze alerty kuponowe bez
  zapisanego meczu oceniane są po pierwszym meczu kuponu. Nic nie jest usuwane.
- **Dashboard i pasek boczny:** „ważne N · wszystkie M”.
- **„Oznacz wszystkie jako przeczytane”** (Automatyzacja AI → Alerty) dopisuje odczyt do każdego
  nieprzeczytanego alertu. Wcześniej obejmował najwyżej 100.
- **Brak źródła absencji:** jedna zbiorcza informacja dziennie zamiast alertu na mecz. Mecz z
  ręcznym wpisem absencji jest pomijany.

**Na kopii:** 691 alertów → 266 pozycji. 486 alertów o zmianach składu to 61 pozycji (często
7–14 alertów w jednej). Według grup: kupony 80, składy meczów z kuponów 49, pozostałe 137.
Wszystkie 266 dotyczą meczów już rozpoczętych, więc Dashboard pokazałby „ważne 0 · wszystkie
266”.

## 5. Absencje

### 5.1 Darmowe API — faktyczne odpowiedzi z 11.11.2026

API-Sports: wykonano dokładnie 10 zapytań (limit dzienny 100; po pomiarze serwer podał 94
pozostałe).

| Źródło | Kontuzje i zawieszenia | Statystyki meczowe (strzały, xG) | Bieżący sezon 2026/27 | Opóźnienie | Koszt |
|---|---|---|---|---|---|
| API-Sports (plan Free) | sezon 2024: tak — 3168 wpisów Premier League. Sezon 2026: odmowa („Free plans do not have access to this season, try from 2022 to 2024”) | sezon 2024: tak, 18 statystyk w tym `expected_goals` | zapytania po sezonie: nie. Zapytania **po dacie** działają tylko w oknie wczoraj–jutro: mecze 11.11 — 200 wyników; kontuzje 11.11 — 0 wpisów (przerwa reprezentacyjna) | niezmierzone | 1 zapytanie na dzień (wszystkie ligi) albo na ligę i sezon |
| PitchAPI | brak — `/v1/injuries` i `/v1/matches/{id}/injuries` zwracają 404 | tak: xG i strzały dla meczu z 08.11 | tak: Premier League 2026/27, 380 meczów, 100 zakończonych | mecz z 08.11 miał statystyki 11.11; dokładniej niezmierzone | 1 zapytanie na mecz |
| football-data.org (Free) | brak — mecz i drużyna nie mają takich pól (kadra: tylko dane osobowe) | brak — mecz bez statystyk i składów | tak (wyniki) | wynik meczu z 08.11 miał `lastUpdated` 11.11 00:20 | 1 zapytanie na okno dat |

### 5.2 Decyzja o imporcie automatycznym: nie teraz

- Żadne źródło nie zwróciło ani jednej absencji bieżącego sezonu.
- Jedyna szansa to kontuzje API-Sports „po dacie”: zapytanie nie zostało odrzucone, ale zwróciło
  0 wpisów, bo 11.11 nie grały ligi objęte pokryciem. Nie da się więc stwierdzić, czy plan Free
  zwraca tam dane lig — nie zgaduję.
- Do sprawdzenia w pierwszym dniu ligowym po przerwie (1–2 zapytania) — §9.

### 5.3 Ręczny wpis

- Analiza meczów → „Składy i absencje” → „Dodaj absencję ręcznie (ze źródłem)”: drużyna,
  zawodnik, powód (kontuzja, zawieszenie, inne), **źródło — obowiązkowe**, adres źródła
  (opcjonalnie), data informacji.
- Zapis w istniejącej tabeli absencji z TASK 008 (`entered_by = user`). Wycofanie to kolejny
  wiersz; poprzedni zostaje.
- Wpis jest widoczny w kontroli przedmeczowej i w ocenie kuponu jako „zweryfikowana przez
  użytkownika”, ze źródłem. Kupon z takim meczem dostaje status „wymaga uwagi”.
- Absencje pozostają ostrzeżeniem. Nie zmieniają żadnej prognozy.

## 6. Reprezentacje i puchary (stan, bez aktywacji)

### 6.1 Stan na kopii

| Rozgrywki | Mecze historii | Bieżący sezon | Mecze w ofercie STS (przypisane) |
|---|---|---|---|
| Liga Mistrzów | 2412 (od 2011/12) | 93 | 18 |
| Liga Europy | 1327 (od 2020/21) | 72 | 20 |
| Liga Konferencji | 1292 (od 2021/22) | 51 | 18 |
| Liga Narodów UEFA | 658 (2018–2025) | **0** | **0** |
| Eliminacje ME | 1016 | 0 | 0 |
| Eliminacje MŚ | 1008 | 12 | 0 |
| Mecze towarzyskie reprezentacji | 1091 | 20 | 0 |
| Mistrzostwa świata / Europy | 356 / 186 | 100 / 0 | 0 |

**Walidacja z TASK 009** (kryteria: co najmniej 300 meczów, log loss lepszy od bazowego,
ECE ≤ 3%):

| Zakres | Mecze w oknie | Rynki zwalidowane | Rynki niezwalidowane |
|---|---|---|---|
| Puchary UEFA | 1501 | 1X2, podwójna szansa, gole 1,5 i 2,5 (ECE 1,6–2,8%) | BTTS (ECE 4,8%) |
| Reprezentacje | 453 | żaden | wszystkie 5: 1X2 i podwójna szansa mają lepszy log loss, ale ECE 4,7%; rynki bramkowe nie przechodzą obu kryteriów |

**Prognozy cienia:** zapisanych 0, rozliczonych 0 — kalibracji na meczach nie da się podać
(brak danych). Zadanie „Nowe mecze pucharowe w ofercie STS” kończyło się wynikiem „0 wydarzeń”.

**Czego brakuje do statusu pozwalającego na kupon:**

- Reprezentacje:
  - kalibracja poniżej progu 3% (dziś 4,7%);
  - przypisanie nagłówków STS: „Liga Narodów UEFA” (44 mecze w najbliższym tygodniu) i
    „Puchar Narodów Afryki — kwalifikacje” (24) są dziś nierozpoznane;
  - wyniki bieżącej edycji Ligi Narodów (jest 0; jedyne źródło kończy się na 2025);
  - prognozy cienia i ich rozliczenie.
- Puchary UEFA:
  - prognozy cienia (0) — trzeba ustalić, dlaczego zadanie nie znajduje meczów;
  - Twoja decyzja — dziś nie ma dla niej przycisku.

### 6.2 Liga Konferencji

- Na kopii czeka **1 nazwa** do potwierdzenia (Liga Europy), 0 dla Ligi Konferencji.
- Brak 135 ze 153 meczów fazy głównej 2025/26 nie wynika więc z nazw, tylko z pokrycia źródeł
  dla tamtego sezonu.
- Widok już istniał (Źródła → Puchary i reprezentacje). Panel „Decyzje do podjęcia” ma teraz
  sekcję „Nazwy drużyn STS do potwierdzenia” z liczbą i odnośnikiem.

### 6.3 Opcje (decyzja należy do Ciebie)

| Opcja | Co daje | Liczby |
|---|---|---|
| A. Zostawić jak jest | brak prognoz i kuponów w przerwach reprezentacyjnych | 0 prognoz od 09.11 |
| B. Puchary UEFA w trybie cienia, potem decyzja | dowody prospektywne dla 4 zwalidowanych rynków | 1501 meczów wstecz, 0 prognoz cienia |
| C. Naprawa danych reprezentacji i ponowna walidacja | przypisanie nagłówków, źródło bieżącej Ligi Narodów, kalibracja | 453 mecze, ECE 4,7% → potrzeba ≤ 3% |

## 7. Drobiazgi

- **Panel decyzji:** zapis, który nie zmienia stanu już podjętej decyzji, nie tworzy wpisu;
  pojawia się komunikat „bez zmian — nic nie zapisano”. Pierwsze potwierdzenie stanu domyślnego
  nadal zamyka otwartą decyzję jednym wpisem (z dopiskiem „potwierdzenie dotychczasowego
  stanu”) — inaczej decyzja pozostawałaby otwarta na zawsze.
- **`docs/data_sources.md`:** werdykt o Flashscore i Meczyki.pl z 11.11.2026 (operatorzy, punkty
  regulaminów, `robots.txt`, legalne drogi).

## 8. Zmiany w kodzie i danych

- `storage.py`: schemat v12 — tabela `quote_confirmations`, widok `quote_observations`.
- `repository.py`: `insert_rows`, zapis oszczędny (`thrifty`), odczyty przez widok.
- `history.py`, `prospective_eval.py`, `app/common.py`: odczyty przez widok.
- `analysis/coupon.py`: `save_analysis` zapisuje pozycje z prognozą.
- `maintenance.py`: `checkpoint`, `size_report`, `size_lines`.
- `backup.py`: kopie cykliczne, kontrola, retencja, `[backup]` w `config/app.toml`.
- `automation.py`: zadanie `backup`, checkpoint po imporcie STS.
- `alerts.py`: grupowanie, rangi, `overview`, `mark_all_read`, `absence_notice`.
- `prematch.py`: `add_user_absence`, `withdraw_absence`, `active_absences`.
- `decisions.py`, `reassessment.py`, `services.py`: zapis bez zmian, opis wpisu użytkownika,
  zbiorcza informacja o braku źródła.
- Interfejs: Dashboard, pasek boczny, Automatyzacja AI (Alerty), Źródła (monitoring), Analiza
  meczów (formularz), panel decyzji (komunikat i odnośnik do nazw).
- `qa.py`: 3 nowe ścieżki; `qa_visual.py`: 2 nowe scenariusze w każdej rozdzielczości.
- **Migracja v11 → v12 na kopii:** 2,9 s, kopia `pre-migration-v12_….db` (713,3 MB), liczby
  wierszy wszystkich 47 tabel bez zmian (poza nowym wpisem `schema_migrations`), nowa pusta
  tabela `quote_confirmations`. Żaden istniejący rekord nie został zmieniony ani usunięty.
- Progi profili, polityka walidacji i protokół v3 w `config/` bez zmian (doszła tylko sekcja
  `[backup]`).

## 9. Testy i kontrole

`tests/test_task016.py` — 28 testów na danych syntetycznych:

- zapis oszczędny: potwierdzenia zamiast kursów; nowy kurs i wycofany rynek jak dotąd; rynek
  wydrukowany dwa razy; wszystkie odczyty identyczne z zapisem pełnym (tabela ofert `as_of`,
  świeżość, historia, CLV, zmiany między dokumentami, porównanie z rynkiem, wiek kursów,
  `reassess`); kupon z potwierdzonego kursu ma poprawną cenę i tożsamość; nowa analiza zapisuje
  tylko pozycje z prognozą;
- WAL: checkpoint nie rzuca wyjątku i nie czeka przy zajętej bazie; przyrost „brak danych” bez
  punktu odniesienia;
- kopia cykliczna: zadanie tworzy sprawdzoną kopię; retencja usuwa tylko kopie cykliczne;
  nieudana kopia zostaje tylko jako najnowsza; brak miejsca i uszkodzona kopia dają alert;
- alerty: jeden alert z listą zmian; stare alerty grupowane i uszeregowane; alert po starcie
  meczu zwinięty; „oznacz wszystkie”; brak źródła absencji raz dziennie;
- absencje: źródło obowiązkowe; wycofanie to nowy wiersz; wpis widoczny w kontroli i ocenie
  kuponu; żadna prognoza się nie zmienia;
- decyzje: zapis bez zmiany nie tworzy wpisu;
- migracja v11 → v12: kopia, liczby wierszy, stare kursy nietknięte;
- interfejs (AppTest): Dashboard, Źródła, formularz absencji, panel alertów, panel decyzji.

**Zmienione istniejące testy** (żaden nie został wyłączony):

- `test_task008.py`: zmiany składu to jeden alert `lineup_changes` o wysokim priorytecie z
  listą zmian — zamiast osobnego alertu o bramkarzu (wymóg §4.1);
- `test_task010.py`: pomocnik importu zapisuje dokumenty po staremu (`thrifty=False`), bo testy
  opisują kupony z dokumentów o własnych identyfikatorach kursów, jakie są w istniejącej bazie;
- `test_task015.py`: test migracji przyjmuje bieżącą wersję schematu (jak wcześniejsze);
- `test_ui_paths.py`: oczekuje 3 nowych ścieżek.

| Kontrola | Wynik |
|---|---|
| `pytest -q` | **480 passed, 4 skipped** (484 testy; pominięte: brak lokalnych autentycznych PDF STS), 645 s |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 184 pliki sformatowane |
| `python -m sports_edge.qa` (ścieżki UI) | **40/40 OK**, w tym nowe: „dashboard: alerty ważne i wszystkie”, „źródła: rozmiar bazy i kopie cykliczne”, „analiza meczu: formularz absencji ze źródłem” |
| `python -m sports_edge.qa_browser` (Playwright) | 22 zrzuty, 0 błędów. Pierwsze dwa przebiegi dały 19 zrzutów i błąd „worker nie działa”: uruchomiłem je ze względną ścieżką `PYTHONPATH`, więc podproces workera wczytał stary kod z głównego checkoutu i odmówił otwarcia bazy v12. Ze ścieżką bezwzględną do tego worktree: 22/0 |
| `python -m sports_edge.qa_visual` (Playwright, DEMO) | **108 kontroli, 0 błędów** (14 stron × 5 rozdzielczości + scenariusze; nowe w każdej rozdzielczości: „Dashboard i Źródła: alerty, rozmiar bazy, kopie” oraz „Analiza meczu: formularz absencji ze źródłem”) |
| Playwright na kopii: Dashboard, Analiza meczów, Automatyzacja AI, Źródła × 5 rozdzielczości | 20/20 bez błędów; worker kopii nie wystartował |

**Przegląd ręczny zrzutów z kopii** (nie dodano do repozytorium; usunięte z kopią): Dashboard —
pasek „Alerty: ważne … · wszystkie 266” i sekcja alertów z jedną linią o pozostałych i
nieaktualnych; Źródła → monitoring — karty „Rozmiar bazy 713,3 MB”, „Przyrost z 7 dni: brak
danych” (kopia nie miała wcześniejszych kopii zapasowych), „Wolne miejsce na dysku” i tabela
„Kopie cykliczne” z jedną kopią i kontrolą OK. Przegląd Dashboardu ujawnił, że 17 starych
alertów kuponowych bez zapisanego meczu liczyło się jako aktualne — poprawione (§4).

## 10. Ograniczenia

- **Rozmiar nadal rośnie** — o ok. połowę wolniej. Reszta to nowe kursy, nowe rynki i prognozy.
  Stare, powtórzone wiersze zostają (baza append-only), więc dzisiejsze 713 MB się nie zmniejszy.
- **Udziały tabel są szacunkiem** (brak `dbstat`), z dokładnością do kilku procent.
- **Prognoza rozmiaru** opiera się na 3–4 dniach z przerwą reprezentacyjną w środku.
- **Odtworzenie na kopii** użyło dokumentów zapisanych w bazie — oryginalne PDF-y nie są
  przechowywane.
- **Strona monitoringu ładuje się długo** na dużej bazie (pełna kontrola integralności, ok.
  11 s, wykonywana dwa razy). Tak było wcześniej; nie zmieniałem tego.
- **Absencje:** brak automatycznego źródła; kontuzje API-Sports „po dacie” niesprawdzone w dniu
  ligowym.
- **Zapis nowej analizy** nie zawiera selekcji bez prognozy — są odtwarzalne z tabel oferty.
- **Liczba ważnych alertów na zrzucie** pochodzi sprzed poprawki; po niej na kopii: 0.

## 11. Proponowany TASK 017

1. **Reprezentacje i puchary — opcja B lub C z §6.3:** przypisanie nagłówków STS, źródło
   bieżącej Ligi Narodów, przyczyna braku prognoz cienia pucharów, przycisk Twojej decyzji.
2. **Kontuzje API-Sports „po dacie”** w pierwszym dniu ligowym po przerwie: 1–2 zapytania;
   import tylko jeśli zwróci wpisy lig.
3. **Lżejsze kursy:** usunięcie zbędnego indeksu `odds_quote` (28,6 MB dziś, ok. 15% każdego
   nowego kursu) i krótsze identyfikatory w nowych tabelach.
4. **Szybszy monitoring:** jedna szybka kontrola integralności zamiast dwóch pełnych.
5. Zaległe z TASK 015: wiek kursów po 3 dniach trybu „w dniu meczu”, szacunek n/300 z
   terminarzem.

## Instrukcja dla Ciebie

Po tej aktualizacji (`G:\SportAI` zaktualizowany do commitu z tego raportu):

1. **Zamknij i uruchom ponownie aplikację i workera.** Przy pierwszym starcie baza przejdzie na
   schemat v12 i powstanie kopia `backups\pre-migration-v12_….db` (ok. 713 MB, kilka sekund).
2. **Pierwsza kopia cykliczna** wykona się zaraz po starcie workera (zaległy termin 03:30), a
   potem codziennie. Zobaczysz ją w Źródłach → „Zbieranie danych i monitoring”.
3. W **Automatyzacji AI → Alerty** możesz jednym przyciskiem oznaczyć stare alerty jako
   przeczytane.
4. Ręczny wpis absencji: **Analiza meczów → Składy i absencje → „Dodaj absencję ręcznie”**.
