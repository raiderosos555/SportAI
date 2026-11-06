# TASK 009 — Complete UI/UX Overhaul (Sports Edge AI 3.0)

Raport z przebudowy interfejsu, wykonanej jako dodatkowy priorytet P0/P1 TASK 009. Opisuje
tylko to, co zostało zbudowane i sprawdzone. Commit i push nie zostały wykonane — czekają na
Twoje polecenie.

**Najważniejsze w skrócie**

* **System projektowy:** nowy `ui.py` z zasadami układu. Liczby, kursy, procenty, daty i
  kwoty nigdy się nie łamią. Słowa dzielą się tylko między wyrazami. Karty mają czytelne
  minimalne szerokości.
* **Przebudowane strony:** Analiza meczu, Dashboard, Kupon dnia, Mój kupon, Modele
  i statystyki, Automatyzacja AI, Źródła danych i Historia kuponów — każda według trzech
  poziomów informacji.
* **Harmonogram przez WWW:** obok interwałów i kontroli względem rozpoczęcia meczu są też stałe
  godziny w wybrane dni tygodnia, zadania jednorazowe i kalendarz 7 dni. Harmonogram Windows
  nie jest używany.
* **Testy wizualne Playwright:** 14 stron × 5 rozdzielczości, 3 scenariusze, asercje układu.
  Wynik końcowy: 76 kontroli, 0 błędów.
* **Testy:** 308 testów pytest przechodzi, Ruff bez uwag, 22 ścieżki QA bez błędów.

---

## 1. Nowy system projektowy (`src/sports_edge/app/ui.py`)

**Zasady układu** (zapisane w module i sprawdzane testami):

* Liczba, kurs, procent, data i kwota nie łamią się nigdy (`white-space: nowrap`, klasa
  `se-num`). Wyrazy łamią się tylko między słowami.
* Usunięto regułę `overflow-wrap: anywhere`, która w wąskich kartach łamała tekst litera po
  literze.
* Siatki mają minimalne szerokości: KPI 220 px, kafelki liczb 128 px, karty rynków 270 px,
  karty 320 px, panele 440 px. Przy braku miejsca kolumny przechodzą do nowego wiersza zamiast
  się zwężać.
* Na telefonie wszystko układa się w jednej kolumnie.
* Wiersze przycisków i filtrów (kontenery `row-…`) zawijają się przy szerokości poniżej
  210 px na kolumnę. Główne przyciski sterowania mają co najmniej 250 px, więc etykiety nie są
  ucinane.
* Strony z dwoma panelami (`split-…`) stoją obok siebie na szerokim ekranie, a pod sobą, gdy
  panel miałby mniej niż 360 px.
* Kolory statusów zawsze coś znaczą: zielony = potwierdzone, bursztynowy = ostrzeżenie,
  czerwony = błąd, niebieski = informacja.
* Profile kuponów mają własne kolory identyfikacji: Tasiemka fioletowy, Standardowy
  niebieski. Nigdy nie wyglądają jak status.

**Nowe komponenty**

| Komponent | Zastosowanie |
|---|---|
| `status_strip` | Pasek stanu strony (np. oferta STS, dane sportowe, automatyzacja, alerty) |
| `metric_cards` / `kpi` | Karty KPI; wartość nigdy się nie łamie |
| `stat_strip` | Kafelki liczb kuponu: kurs, szansa, ostrożnie, stawka, wypłata, zysk |
| `profile_badge` | Profil kuponu (Wysoka trafność, Standardowy, Wyższa wypłata, Tasiemka, Mój kupon) |
| `list_row` / `rows` | Czytelne wiersze listy zamiast wąskich kart |
| `legs_html` | Uporządkowana lista selekcji: numer, mecz, typ, kurs, szansa, status składu |
| `market_block` | Paski prawdopodobieństwa jednego rynku z oznaczeniem walidacji |
| `compare_table` | Porównanie drużyn; brak danych pokazany jako „brak danych”, nigdy jako 0 |
| `factors` | Czynniki ryzyka oznaczone „w modelu”, „tylko ostrzeżenie” lub „brak danych” |
| `summary` | Krótkie podsumowanie w zdaniach (Za prognozą, Niepewność, …) |
| `match_header` | Nagłówek meczu: gospodarze, goście, rozgrywki, data, statusy |
| `table` | Przyjazna tabela HTML: liczby wyśrodkowane i niełamane |
| `details` | Poziom 3: szczegóły techniczne w panelu zamkniętym domyślnie |

Wszystkie dotychczasowe funkcje `ui.*` zachowały sygnatury — żadna strona nie straciła
funkcji. Pasek boczny pokazuje wszystkie strony (bez zwiniętego „View more”), a pola wyboru
mają polskie podpowiedzi zamiast „Choose options”.

## 2. Trzy poziomy informacji

| Strona | Poziom 1 — najważniejsze | Poziom 2 — analiza | Poziom 3 — szczegóły (zamknięte) |
|---|---|---|---|
| Analiza meczu | nagłówek meczu, prawdopodobieństwa rynków, najlepszy typ | porównanie drużyn, czynniki ryzyka, „Dlaczego AI wybrało taki typ?” | model i dane meczu, pełne uzasadnienie (dane użyte / nieuwzględnione / model rozszerzony), zakładki |
| Dashboard | pasek stanu, propozycje kuponów, najważniejsze mecze | kupony do rozliczenia, alerty wymagające uwagi | liczby oferty, monitoring przedmeczowy, dane i ograniczenia |
| Kupon dnia | karty kuponów: profil, kafelki liczb, selekcje | ostrzeżenia, statusy składów | „Szczegóły, ocena AI, ryzyko, składy i historia” (zakładki) |
| Mój kupon | kupon i jego liczby, werdykt oceny | zakładki oceny | pytania, historia kursów |
| Modele i statystyki | tabela rynków: model, status, obserwacje, przewaga, ECE, challenger | wykres przewagi nad częstością | log loss, Brier, ECE, kalibracja według przedziałów |
| Automatyzacja AI | zakładka Przegląd | Harmonogram, Procesy | Historia, Alerty, szczegóły workera |
| Źródła danych | jeden wiersz na dostawcę | — | status, licencje, limity, pokrycie lig |
| Historia kuponów | podsumowanie i lista z filtrami | szczegóły zaznaczonego kuponu | zakładki techniczne |

## 3. Analiza meczu

Długie listy tekstowe zastąpiono strukturą (`app/match_view.py`).

* **Nagłówek:**
  * gospodarze, goście, rozgrywki, data i godzina;
  * statusy: wydarzenie, skład (potwierdzony / przewidywany / brak danych), aktualność wyników
    ligi, potwierdzenie kursu, tryb przed lub po meczu;
  * wersja aktywnego modelu w panelu „Model i dane tego meczu”.
* **Prawdopodobieństwa:**
  * osobny blok dla 1X2, Over/Under (linie 0,5–3,5) i BTTS;
  * każdy blok ma poziome paski, oznaczenie „zwalidowany”, „częściowo zwalidowany” lub
    „eksperymentalny” oraz nazwę modelu, który go policzył;
  * prognozy różnych modeli nigdy nie są łączone w jedną wartość.
* **Porównanie drużyn:**
  * tabela: forma (5), punkty na mecz (5 i 10), gole zdobyte i stracone, forma u siebie /
    na wyjeździe, wynik wobec siły rywali, dni odpoczynku (z liczbą meczów w 14 dni), xG;
  * wartości pochodzą z cech as-of; brak to „brak danych”, korzystniejsza wartość jest
    zielona.
* **„Co może wpłynąć na ocenę meczu?”:**
  * czynniki z rozróżnieniem: „w modelu” (siła drużyn Elo, średnie gole Dixona–Colesa) wobec
    „tylko ostrzeżenie” (skład przewidywany, zmiany w składzie, zagęszczony terminarz, rynki
    bez walidacji) i „brak danych” (skład nieopublikowany, brak źródła kontuzji, brak
    statystyk).
* **„Dlaczego AI wybrało taki typ?”:**
  * cztery krótkie zdania: za prognozą, niepewność, wykorzystane dane, niewykorzystane dane;
  * budowane z danych aplikacji, bez modelu językowego, więc nie ma miejsca na wymyślone
    przyczyny.
  * Pełny podział „dane użyte / dodatkowe dane / model rozszerzony” jest w panelu poniżej.

## 4. Dashboard

* **Kolejność zgodna ze specyfikacją:**
  1. pasek stanu: oferta STS, dane sportowe, automatyzacja, alerty;
  2. propozycje kuponów jako czytelne wiersze z kursem, szansą, ostrożną szansą, wypłatą
     i statusem;
  3. najważniejsze mecze z prognozą, z przyciskiem dodania do kuponu;
  4. kupony oczekujące na rozliczenie;
  5. alerty wymagające uwagi z przyciskiem „SPRAWDŹ WSZYSTKIE MOJE KUPONY TERAZ”.
* **Bez duplikatów:** identyczne propozycje kilku profili pokazują się raz, z wszystkimi
  plakietkami profili; baza zachowuje każdą propozycję.
* **Alerty:** na pierwszym poziomie tylko ważne (wysoki i średni priorytet). Informacje
  o niższym priorytecie, np. brak źródła kontuzji dla każdego meczu, są zebrane w jednym
  wierszu.
* **Szczegóły techniczne** są w panelu zamkniętym na dole strony.

## 5. Kupon dnia i Mój kupon

* **Wspólna karta kuponu** (`app/coupon_view.py`):
  * plakietka profilu, data, liczba selekcji i status (zwalidowany / symulacja; rozliczony /
    nierozliczony);
  * kafelki: kurs łączny, szansa, ostrożnie, stawka, wypłata, zysk netto;
  * uporządkowana lista selekcji ze statusem składów i ostrzeżenia;
  * w panelu zakładki: Dlaczego, Ocena AI (na żądanie), Ryzyko, Składy (z kontrolą przez
    workera), Historia zmian (wszystkie zapisane warianty profilu z danego dnia), Symulacja
    budżetu.
  * Symulacja to dokładny rozkład dwumianowy dla 20 kuponów: ile trafień daje zysk i jaka jest
    szansa zysku według modelu i ostrożnie.
* **Kupon dnia:**
  * każdy profil to pełnoszeroka karta zamiast czterech wąskich kolumn;
  * profil bez propozycji to krótki wiersz z powodem;
  * Tasiemka ma fioletową plakietkę i linię — nie da się jej pomylić ze Standardowym.
* **Mój kupon:**
  * oferta i kupon obok siebie na szerokim ekranie, pod sobą na węższym;
  * każda selekcja to jeden wiersz (typ, kurs, model) z przyciskiem „Dodaj”;
  * kupon ma te same kafelki liczb co Kupon dnia;
  * ocena AI pokazuje werdykt i najsłabsze ogniwo, a resztę w zakładkach: Ocena AI (tabela
    selekcji), Ryzyko, Składy, Alternatywy, Symulacja budżetu, Historia zmian (kurs przy
    dodaniu i teraz), Zapytaj.

## 6. Modele i statystyki

* **Tabela dla każdego zakresu lig:** rynek, aktywny model, status walidacji, liczba
  obserwacji, przewaga log loss nad częstością bazową, kalibracja (ECE), challenger.
* **Wykres słupkowy:** przewaga nad częstością dla każdego rynku (jedna oś, kolor = status).
* **Szczegóły w panelu:** log loss, log loss bazowy, Brier, ECE, n oraz kalibracja według
  przedziałów prawdopodobieństwa i powody statusu.
* **Język:** usunięto sformułowanie „można ufać”. Status opisany jest jako „zwalidowany” z
  wyjaśnieniem, że to nie jest gwarancja trafienia ani zysku. Test pilnuje, by „można ufać” nie
  wróciło do interfejsu.

## 7. Automatyzacja AI

Strona podzielona na pięć zakładek. Każda ma własne przyciski zapisu — koniec z jednym
ogromnym formularzem.

* **Przegląd:**
  * pasek stanu: automatyzacja, worker, ostatnie zadanie, następne zadanie;
  * przyciski w dwóch rzędach: uruchom i kontrola teraz, potem wstrzymaj, wznów, zatrzymaj;
  * najważniejsze błędy; szczegóły workera i monitoring danych kadrowych w panelu.
* **Harmonogram:**
  * kalendarz 7 dni (godziny w czasie polskim, zadania stałe w tle opisane jednym zdaniem);
  * plan kontroli względem rozpoczęcia najbliższych meczów (następny punkt T-48h…T-15min);
  * edytor harmonogramu zadania: „co określony czas” albo „o stałych godzinach” w wybrane dni
    tygodnia;
  * zadania jednorazowe z możliwością anulowania.
* **Procesy:** włączanie i wyłączanie, częstotliwość, limit kandydatów, automatyczne wznowienie
  oraz przycisk ZAPISZ USTAWIENIA.
* **Historia:** filtry zadania i wyniku; czas wykonania, wyniki, przyczyny błędów; szczegóły
  i ponowne uruchomienie zaznaczonego zadania.
* **Alerty:** ważne nowe zdarzenia, informacje systemowe workera, historia powiadomień.

**Backend harmonogramu** (`automation.py`, tylko WWW, bez Harmonogramu Windows):

* **Tryb „o stałych godzinach”:** godziny w czasie Europe/Warsaw i wybrane dni tygodnia.
  * Godzina, która minęła przy wyłączonej aplikacji, wykonuje się raz po starcie (nadrabianie).
  * Kolejny termin to następny slot.
  * Po błędzie zadanie wraca po wykładniczym odroczeniu, nie później niż w następnym slocie.
* **Zadania jednorazowe:**
  * zapisane w ustawieniach (SQLite, wersjonowane);
  * wykonane raz i oznaczone zdarzeniem `one_off_done`;
  * anulowanie usuwa je z bieżących ustawień, a starsze wersje zostają w bazie.
* **Kalendarz** (`planned`): stałe godziny, interwały co najmniej 6 h i zadania jednorazowe;
  krótkie interwały są opisane zbiorczo.

## 8. Źródła danych i historia kuponów

* **Źródła danych:**
  * jeden czytelny wiersz na dostawcę: STS, football-data.org, PitchAPI, openfootball,
    API-Sports, OpenScores;
  * w wierszu: status, zakres danych, ostatnia aktualizacja, dostępność klucza i liczba
    rekordów;
  * status szczegółowy, zmienna klucza, licencja i limity oraz pokrycie lig są w panelu.
* **Historia kuponów:**
  * tabela z filtrami: zakres dat, profil, status (nierozliczone / trafione / nietrafione /
    inne), typ kuponu;
  * typy kuponów: bieżący kandydat, zapisany wirtualny kupon, kupon rozliczony, rzeczywisty
    (postawiony w STS), historyczny wariant propozycji;
  * historyczny wariant to starszy zapis tego samego profilu i dnia albo profil sprzed TASK 007;
  * po zaznaczeniu wiersza widać kafelki liczb, selekcje z wynikami i analizę wyniku;
  * nic nie jest usuwane.

## 9. Automatyczne testy wizualne (Playwright)

`python -m sports_edge.qa_visual <katalog>` uruchamia interfejs w **tymczasowym** katalogu
domowym:

* kopia `config/`, bez bazy, `.env` i kluczy;
* oferta DEMO z fikcyjnymi drużynami, w tym długa nazwa ligi i klubów;
* tryb offline.

Każdą z 14 stron sprawdza w rozdzielczościach **1920×1080, 1366×768, 1024×768, 768×1024
i 390×844**.

**Asercje dla każdej strony:**

* brak poziomego przewijania całej strony;
* brak elementów poza krawędziami ekranu (poza tabelami przewijanymi wewnątrz);
* żaden wyraz, kurs, procent ani data nie jest złamany między wierszami — sprawdzane na
  prostokątach tekstu w przeglądarce;
* karty nie węższe niż 200 px;
* przyciski aplikacji widoczne, nie ściśnięte i z nieuciętą etykietą;
* brak wyjątków Streamlit;
* przy 1366 px każda zakładka przełącza się poprawnie, ze zrzutem każdej zakładki.

**Scenariusze** (1366 i 390 px):

* dodanie typu w Moim kuponie i ocena AI ze wszystkimi zakładkami;
* mecz z najdłuższymi nazwami klubów i ligi (nazwa musi być widoczna i łamać się tylko między
  słowami);
* analiza w Kuponie dnia bez internetu.

**Wynik** (ostatni przebieg lokalny, Windows):

* 76 kontroli (14 stron × 5 rozdzielczości oraz 3 scenariusze × 2 rozdzielczości),
  0 błędów;
* 22 zakładki przełączone poprawnie;
* 99 plików: zrzuty stron, zakładek i scenariuszy oraz raport.

Wcześniejsze przebiegi wykryły problemy opisane w sekcji 10 — wszystkie usunięto przed
ostatnim przebiegiem.

**CI:**

* GitHub Actions uruchamia zestaw na Windows i Ubuntu (Python 3.12).
* Zrzuty i `visual_report.json` trafiają do artefaktów `qa-report-Windows` i `qa-report-Linux`,
  obok zrzutów cyklu workera i raportu ścieżek.
* Artefakt jest sprawdzany pod kątem plików bazy i kluczy.
* Test jednostkowy sprawdza, że sam checker wykrywa złamaną liczbę, za wąską kartę i poziome
  przewijanie.

## 10. Ręczny przegląd zrzutów — wykryte i usunięte problemy

* **Wąskie karty i łamanie liczb:**
  * 6 kart KPI po 172 px na Dashboardzie, w Historii i w Automatyzacji;
  * kursy i procenty łamane w kartach Historii;
  * daty i słowa łamane w kartach Źródeł.
  * Naprawa: nowe minimalne szerokości, lista i tabela zamiast kart, brak `overflow-wrap:
    anywhere`.
* **Kupon dnia:** 4 profile w wąskich kolumnach (ok. 260 px) zastąpiono pełnoszerokimi kartami.
* **Dashboard:**
  * wiersze najlepszych meczów zajmowały połowę szerokości, bo reguła zawijania wyrównywała
    proporcje kolumn — poprawione;
  * ten sam tytuł i plakietka profilu w kuponach do rozliczenia — tytułem są teraz mecze;
  * pięć identycznych alertów o braku źródła kontuzji zastąpił jeden wiersz;
  * przycisk „SPRAWDŹ WSZYSTKIE MOJE KUPONY TERAZ” był ucięty przy 1024 i 768 px — teraz
    zajmuje pełny wiersz.
* **Automatyzacja AI:**
  * przyciski sterowania ucięte wielokropkiem przy 1366 px — teraz dwa rzędy, minimum 250 px;
  * checker zyskał asercję uciętej etykiety przycisku;
  * zrzut strony robiony był po ostatniej zakładce — teraz najpierw strona, potem każda
    zakładka.
* **Mój kupon przy 1024 i 768 px:**
  * filtry ucięte („wszystk”), przełączniki skrócone do „T.” i „P.”;
  * kursy i słowa łamane w czterokolumnowych wierszach rynków.
  * Naprawa: panele pod sobą, zawijane wiersze filtrów, jedna linia na selekcję.
* **Reguła paneli** obejmowała zagnieżdżone kolumny (przycisk usunięcia selekcji spadał do
  nowej linii) — ograniczona do zewnętrznych paneli.
* **Historia:**
  * wyjątek przy kuponie bez wiarygodnej szansy (`None`) — obsłużony;
  * zawężone pole zakresu dat — poszerzone;
  * stare profile oznaczone jako „Bieżący kandydat” — teraz „Historyczny wariant propozycji”.
* **Teksty po angielsku:** „Choose options” w polach wyboru i „View 4 more” w nawigacji —
  zastąpione polskimi podpowiedziami i pełną nawigacją.
* **Długie nazwy w DEMO:** pierwsze próby przekraczały kolumnę wydruku STS i były ucinane przez
  parser. Wybrano najdłuższe nazwy, które parser czyta w całości.

## 11. Testy i jakość

* **Pytest:** 308 testów przechodzi (w tym 9 nowych z TASK 009).
* **Nowe testy TASK 009** (`tests/test_task009.py`):
  * stałe godziny w czasie polskim i wybrane dni;
  * jedno uruchomienie na slot i nadrabianie;
  * zadania jednorazowe: raz, bez powtórzeń, anulowanie;
  * kalendarz;
  * typy kuponów w historii;
  * symulacja budżetu;
  * komponenty: brak łamania liczb, brak danych ≠ 0, kolory profili, czynniki;
  * zakaz „można ufać”;
  * sam checker wizualny.
* **Ruff:** `ruff check .` i `ruff format --check .` bez uwag.
* **Ścieżki QA** (`python -m sports_edge.qa`): 22 ścieżki, 0 błędów.

## 12. Kryteria odbioru

| Kryterium | Stan |
|---|---|
| 1. Dashboard uporządkowany | spełnione — 5 bloków w kolejności specyfikacji, szczegóły w panelu |
| 2. Karty kuponów czytelne | spełnione — pełna szerokość, kafelki liczb, lista selekcji |
| 3. Liczby i teksty nie łamią się pionowo | spełnione — asercja we wszystkich 5 rozdzielczościach |
| 4. Historia kuponów przejrzysta | spełnione — tabela z filtrami i typami |
| 5. Analiza meczu z wizualnym porównaniem drużyn | spełnione — tabela porównawcza i paski rynków |
| 6. Dane użyte i nieuwzględnione rozdzielone | spełnione — czynniki „w modelu” / „tylko ostrzeżenie” i pełny podział w panelu |
| 7. Modele i statystyki przejrzyste | spełnione — tabela, wykres, szczegóły w panelu |
| 8. Źródła danych czytelne | spełnione — wiersz na dostawcę, szczegóły w panelu |
| 9. Harmonogram AI przez WWW | spełnione — kalendarz, godziny, dni, jednorazowe, interwały, względem meczu |
| 10. Laptop i telefon | spełnione — 5 rozdzielczości w Playwright |
| 11. Istotne akcje dostępne | spełnione — 22 ścieżki QA i scenariusze Playwright |
| 12. Playwright potwierdza scenariusze | spełnione lokalnie; w CI po pierwszym pushu |
| 13. Zrzuty sprawdzone wizualnie | spełnione — przegląd ręczny, problemy z sekcji 10 usunięte |
| 14. Funkcje zachowane | spełnione — sygnatury `ui.*` bez zmian, wszystkie testy przechodzą |
| 15. Historyczne prognozy i rozliczenia bez zmian | spełnione — przebudowa dotyczy prezentacji; nowe zapisy tylko w ustawieniach harmonogramu |

## 13. Ograniczenia i dalsze kroki

* **CI:** zestaw wizualny w GitHub Actions uruchomi się dopiero po commicie i pushu. Na
  Ubuntu inne fonty mogą dać drobne różnice w szerokościach — warto przejrzeć pierwszy
  artefakt.
* **Strony nieprzebudowane w tej części:** Kupon na kurs, Tasiemka, Wydarzenia, Symulatory
  i Uczenie AI. Korzystają z nowego systemu projektowego i przechodzą asercje we wszystkich
  rozdzielczościach, ale ich układ nie był przebudowywany.
* **Główna specyfikacja TASK 009** (modele rozszerzone, automatyzacja i stabilizacja) nie
  została mi przekazana w tej części rozmowy. Ten raport obejmuje wyłącznie przebudowę
  interfejsu.

## 14. International Football Intelligence — europejskie puchary i reprezentacje

Rozszerzenie obowiązkowe TASK 009. Liczby w tym rozdziale pochodzą z pełnego importu i
eksperymentów wykonanych **na kopii prywatnej bazy w katalogu tymczasowym** (stan z
06.11.2026). Kopię usunięto po pomiarach. Prywatna baza nie została zmieniona: import u Ciebie
uruchamia przycisk **Źródła → Puchary i reprezentacje → Pobierz / uzupełnij dane
międzynarodowe** albo zadanie harmonogramu WWW. Pierwsze uruchomienie pobiera całą historię,
kolejne tylko bieżące sezony.

### 14.1 Rozgrywki, źródła i stan danych

Źródła i identyfikatory sprawdzono 02.11.2026 Twoimi kluczami (football-data.org, PitchAPI) i
w publicznych repozytoriach (openfootball, CC0). Do katalogu wpisano wyłącznie identyfikatory
potwierdzone w katalogu dostawcy.

| Rozgrywki | Źródła | Sezony | Wyniki | Bieżący sezon | Niepewne 90' | Konflikty | Nagłówek STS | Mecze STS (piłka) |
|---|---|---|---:|---:|---:|---:|---|---:|
| Liga Mistrzów UEFA | football_data, pitchapi, openfootball_cups | 2011-12–2026-27 | 2412 | 93 (2026-27) | 11 | 1 | KLUBOWE - LIGA MISTRZÓW | 18 |
| Liga Europy UEFA | pitchapi, openfootball_cups | 2020-21–2026-27 | 1323 | 68 (2026-27) | 21 | 0 | KLUBOWE - LIGA EUROPY | 20 |
| Liga Konferencji UEFA | pitchapi, openfootball_cups | 2021-22–2026-27 | 1292 | 51 (2026-27) | 17 | 0 | KLUBOWE - LIGA KONFERENCJI | 18 |
| Mistrzostwa świata | football_data, pitchapi, openfootball_int | 2010–2026 | 356 | 100 (2026) | 3 | 0 | nie zaobserwowano | 0 |
| Mistrzostwa Europy | football_data, openfootball_int | 2012–2024 | 186 | 0 (2026) | 1 | 0 | nie zaobserwowano | 0 |
| Liga Narodów UEFA | openfootball_int | 2018–2025 | 658 | 0 (2026) | 6 | 0 | nie zaobserwowano | 0 |
| Eliminacje mistrzostw Europy | openfootball_int | 2010–2024 | 1016 | 0 (2026) | 2 | 0 | nie zaobserwowano | 0 |
| Eliminacje mistrzostw świata | openfootball_int | 2012–2026 | 1008 | 12 (2026) | 0 | 0 | nie zaobserwowano | 0 |
| Mecze towarzyskie reprezentacji | openfootball_int | 2010–2026 | 1091 | 20 (2026) | 0 | 0 | nie zaobserwowano | 0 |

* **football-data.org (plan darmowy):** `CL` sezony 2023/24–2026/27 (starsze: HTTP 403), `EC`
  = **EURO reprezentacji** (2024), a nie Liga Europy. Ligi Europy i Ligi Konferencji plan
  darmowy nie obejmuje. `WC` = MŚ 2026 (2022: 403). Wynik zawiera `regularTime`,
  `extraTime` i `penalties`. `fullTime` obejmuje dogrywkę **i** serię rzutów karnych, więc w
  bazie zapisywany jest `regularTime`.
* **PitchAPI:** LM `l_0bfbkO`, LE `l_38d9HA`, LK `l_2WZ2tt` (2021/22–2026/27, bez
  kwalifikacji), MŚ `l_1XLtP0`. Lista meczów nie ma etapu, a **wynik zawiera gole z dogrywki**
  (bez karnych), np. Juventus–Galatasaray 3:2 = 3:0 po 90 min + 0:2 w dogrywce. Adapter MŚ nie
  pobiera, bo format sezonu nie został zweryfikowany.
* **openfootball/champions-league (CC0):** LM 2011/12–2025/26, LE od 2020/21, LK od 2021/22,
  kwalifikacje tylko 2024/25–2025/26, bez sezonu 2026/27. Wynik ma postać „90 min, dogrywka,
  karne”, a przy każdym klubie jest kod kraju.
* **openfootball/internationals (CC0):** pliki roczne Ligi Narodów, EURO, eliminacji, MŚ i
  meczów towarzyskich. Wynik **jest końcowy (z dogrywką) bez znacznika „a.e.t.”**, dlatego wynik
  po 90 minutach jest ustalany z minut bramek (14.3).
* **Puchary krajowe i pozostałe (P3):** Superpuchar UEFA, FA Cup, EFL Cup, DFB-Pokal, Copa del
  Rey, Coppa Italia, Puchar Polski — brak w sprawdzonych bezpłatnych źródłach. Klubowe MŚ są
  tylko w openfootball/club-worldcup (niewdrożone). Żadnej z tych integracji nie udawano; lista
  jest w aplikacji (Źródła → Inne puchary).

### 14.2 Rejestr rozgrywek i oferta STS

* `datasources/competitions.py` jest jedynym katalogiem. Każda pozycja ma `kind` (liga krajowa,
  puchar krajowy, puchar międzynarodowy klubów, turniej, eliminacje, Liga Narodów, mecz
  towarzyski), z niego wynika kategoria (Ligi krajowe / Europejskie puchary / Reprezentacje).
  Pozycja zawiera też zweryfikowane identyfikatory źródeł (`sources`) i datę weryfikacji.
  Adaptery (football-data, PitchAPI, oba openfootball) biorą listę rozgrywek z katalogu, więc
  nowe rozgrywki dopisuje się w jednym miejscu.
* Nagłówki STS to **wyłącznie tekst zaobserwowany w ofertach**:
  * „KLUBOWE - LIGA MISTRZÓW” (03–04.11.2026);
  * „KLUBOWE - LIGA EUROPY” (04–05.11.2026);
  * „KLUBOWE - LIGA KONFERENCJI” (05.11.2026).

  Dopasowanie wymaga też dyscypliny. Ten sam nagłówek „KLUBOWE - LIGA MISTRZÓW” występuje w
  koszykówce (8 meczów 03–04.11) i **nie** jest Ligą Mistrzów UEFA. „KLUBOWE - LIGA MISTRZÓW
  AFC” to inne rozgrywki. Nagłówki reprezentacji nie wystąpiły jeszcze w Twoich ofertach, więc
  nie zostały wpisane.
* Raport pokrycia pokazuje nagłówki międzynarodowe i pucharowe bez mapowania, np. „ANGLIA -
  PUCHAR”, „FRANCJA - PUCHAR”, „KLUBOWE - LIGA MISTRZÓW AFC”. To kandydaci na przyszłość,
  nigdy nie mapowani zgadywaniem.
* Regularna aktualizacja wyników (`RESULTS_COMPETITIONS`) obejmuje jak dotąd ligi krajowe i inne
  dyscypliny. Puchary i reprezentacje mają osobny import z własnymi limitami.

### 14.3 Semantyka pucharów

* **Wynik w bazie to zawsze 90 minut z doliczonym czasem** — to, co rozlicza STS dla rynków
  „Mecz”, podwójnej szansy, goli i BTTS (Z 15.1.1; potwierdzone dla meczów LM w Twojej
  ofercie). Wynik po dogrywce (`aet`), karne (`pens`), etap, dwumecz i awans są w `details`
  osobno. Rynku „awans” STS w obsługiwanych rynkach nie ma.
* **Dwumecze** (`cups.annotate`): ta sama para z odwróconym gospodarzem, w tym samym sezonie i
  etapie, najwyżej 35 dni odstępu. Wcześniejszy mecz to pierwszy mecz dwumeczu. Dwa mecze tej
  samej pary w fazie grupowej nigdy nie tworzą dwumeczu. Rewanż w ofercie STS jest rozpoznawany
  tylko wtedy, gdy pierwszy mecz tej pary w tej rundzie już się odbył. Pierwszego meczu nie da
  się odróżnić od meczu fazy ligowej ani finału przed losowaniem, więc system tego nie zgaduje.
* **Formaty sezonów:** faza ligowa (36 drużyn) od 2024/25, wcześniej grupy. Reguła bramek na
  wyjeździe tylko do 2020/21 (zniesiona od 2021/22). Sezon 2019/20 (turniej finałowy w
  Lizbonie) ma 119 meczów zamiast 125.
* **Wynik, który może zawierać dogrywkę:** liczony arytmetycznie, nigdy zgadywany
  (`cups.ninety_candidates`):
  * wyliczane są wszystkie wyniki po 90 minutach zgodne z podanym wynikiem i zasadami dwumeczu;
  * jeden kandydat oznacza wynik pewny;
  * kilku kandydatów oznacza `regulation_certain = False` — taki mecz nie trafia do modeli
    goli i 1X2, do rozliczeń ani do wykrywania konfliktów;
  * przy kilku źródłach wybierany jest wynik pewny (osobna hierarchia źródeł dla pucharów:
    football-data > openfootball > PitchAPI).
* **Reprezentacje:** w fazie grupowej i w meczach towarzyskich dogrywki nie ma. W pozostałych
  meczach wynik po 90 minutach ustalany jest z minut bramek. Gol po 99. minucie to dogrywka.
  Gol w minutach 91–99 może być doliczonym czasem albo dogrywką — taki mecz pozostaje
  niepewny. Przykład: Hiszpania–Holandia 3:3 (LN 2025) zapisana jako 2:2 po 90 min (gole w
  103' i 109'); Portugalia–Dania 5:2 jest niepewna (91').

### 14.4 Import i archiwizacja (etap 1)

Kolejność importu: openfootball (nazwy i kraje klubów) → football-data CL → PitchAPI LM/LE/LK →
reprezentacje.

**Aliasy z meczu w dwóch źródłach (`clubs.learn_fixture_aliases`).** Inna pisownia klubu staje
się aliasem tylko wtedy, gdy oba źródła podają ten sam mecz: ten sam dzień, ten sam wynik i
rozpoznany przeciwnik po tej samej stronie. Sprzeczne dowody odrzucają parę. Jedna pisownia
źródła może wskazywać różne pisownie kanoniczne w różnych pucharach (np. „Braga”).

**Nazwy podobne do istniejących.** Nazwa podobna do istniejącej, ale z innego kraju, oznacza
nowy klub (FK Partizani z Albanii ≠ FK Partizan z Serbii). Podobna nazwa z tego samego kraju
czeka na potwierdzenie.

**Wspólna pula tożsamości.** Każda kategoria ma jedną pulę: klub ma jedną pisownię w LM, LE i
LK, a reprezentacja we wszystkich rozgrywkach reprezentacji.

**Wyniki pełnego importu:**

| Rozgrywki | Źródło | Nowe wiersze |
|---|---|---:|
| Mecze towarzyskie reprezentacji | openfootball_int | 1091 |
| Mistrzostwa świata | football_data_org | 100 |
| Mistrzostwa świata | openfootball_int | 256 |
| Eliminacje mistrzostw świata | openfootball_int | 1008 |
| Liga Mistrzów UEFA | football_data_org | 559 |
| Liga Mistrzów UEFA | openfootball_cups | 2163 |
| Liga Mistrzów UEFA | pitchapi | 817 |
| Liga Konferencji UEFA | openfootball_cups | 1081 |
| Liga Konferencji UEFA | pitchapi | 749 |
| Liga Europy UEFA | openfootball_cups | 976 |
| Liga Europy UEFA | pitchapi | 867 |
| Mistrzostwa Europy | football_data_org | 51 |
| Mistrzostwa Europy | openfootball_int | 184 |
| Eliminacje mistrzostw Europy | openfootball_int | 1016 |
| Liga Narodów UEFA | openfootball_int | 658 |

* Aliasy z meczu w dwóch źródłach: **44** (np. „Salzburg” → RB Salzburg, „Young Boys” → BSC Young Boys, „Club Brugge” → Club Brugge KV, „Dynamo Kyiv” → Dinamo Kiev, „Benfica” → SL Benfica, „Wolfsburg” → VfL Wolfsburg).
* Konflikty pewnych wyników 90' między źródłami: **1** (FC Internazionale Milano - FC Barcelona 2025-05-06: 4:3 (openfootball_cups) vs 3:3 (football_data_org)).
* Wyniki niepewne po 90 min (PitchAPI, openfootball/internationals): **218** rekordów źródeł — poza modelami i rozliczeniami.
* Nazwy do potwierdzenia (mecze niezaimportowane): **10** — Anorthosis, Bosnia-Herzegovina, FC Santa Coloma, Flora Tallinn, GNK Dinamo Zagreb, Hapoel Beer Sheva, Royale Union Saint-Gilloise, Strasbourg, Union St.Gilloise, Universitatea Craiova.
* Mecze zaplanowane zapisane w terminarzu bieżącego sezonu: **198** (LM 72, LE 72, LK 54; najbliższe od 24.11.2026).


Surowe odpowiedzi są zapisywane z SHA-256 (`data/sources/results/...`), a wiersze dopisywane
(append-only). Zaplanowane mecze bieżącego sezonu są zapisywane jako obserwacje terminarza.

### 14.5 Tożsamość klubów między rozgrywkami

**Jedna tożsamość klubu.** Real Madryt z LaLigi i Real Madryt z LM to jeden klub
(`clubs.links`). Powiązanie wymaga jednego z trzech dowodów:
1. ten sam identyfikator drużyny u dostawcy;
2. ta sama nazwa i ten sam kraj;
3. alias (zweryfikowany albo z meczu w dwóch źródłach) i ten sam kraj.

Podobieństwo nazw nigdy nie wystarcza. Kluby bez dowodu zostają osobne.

**Rozdzielone warstwy.** Osobno trzymane są:
* surowa pisownia źródła;
* globalna tożsamość (zmiana nazwy na pisownię ligową przy odczycie);
* udział w rozgrywkach (wiersze wyników);
* oceny i parametry modeli (osobne pule).

**Reprezentacje** mają osobną przestrzeń tożsamości (`nation`) i nigdy nie są łączone z klubami.
Elo ligowe nie jest przenoszone do pucharów bez kalibracji: tę kalibrację testują warianty B i C.

Pisowni klubów w pucharach: **674**; powiązanych z klubem ligi krajowej: **204** — dowód: identyfikator dostawcy 153, nazwa + kraj 40, alias + kraj 11. Pozostałe to głównie kluby lig spoza katalogu (np. Szkocja, Austria, Grecja) — zostają osobnymi tożsamościami pucharowymi.

### 14.6 Integracja z ofertą STS

* Wydarzenia STS w zmapowanych nagłówkach są widoczne w Wydarzeniach, Analizie meczów, Mój
  kupon i Kupon na kurs z filtrem **Wszystkie / Ligi krajowe / Europejskie puchary /
  Reprezentacje**.
* Rozpoznawanie drużyn działa jak w ligach (klucz po normalizacji albo potwierdzony alias),
  ale w puli pucharowej z tożsamością ligową. Dzięki temu np. „Sporting Lizbona” rozpoznaje się
  przez istniejący alias ligowy.
* **Nazwy STS do potwierdzenia** (Źródła → Puchary i reprezentacje):
  * aplikacja proponuje parę tylko z dowodem — ten sam mecz w źródle: dzień, godzina ±15 min
    i rozpoznany przeciwnik;
  * przykłady z Twojej oferty: „Szachtar Donieck” → Shakhtar Donetsk, „Inter Mediolan” → FC
    Internazionale Milano, „LOSC Lille” → Lille OSC, „Hearts FC” → Heart of Midlothian;
  * potwierdzasz jednym przyciskiem — nazw STS nic nie łączy bez Twojej decyzji.
* Rozliczenie z niezależnych wyników używa wyniku po 90 minutach. Jest wstrzymywane, gdy wynik
  po 90 min jest niepewny albo źródła się różnią.

| Rozgrywki | Mecze STS (piłka, 03–06.11) | Obie drużyny rozpoznane |
|---|---:|---:|
| Liga Mistrzów UEFA | 18 | 13 |
| Liga Europy UEFA | 19 | 10 |
| Liga Konferencji UEFA | 18 | 7 |

Propozycje nazw STS z dowodem meczowym czekające na potwierdzenie: **20**.

### 14.7 Terminarz i odpoczynek (etap 2)

* Analiza meczu pucharowego pokazuje dla obu drużyn dni odpoczynku, mecze w 14 dniach (w tym
  europejskie), dogrywkę w ostatnim meczu i 5 ostatnich meczów ze wszystkich wiarygodnie
  zidentyfikowanych rozgrywek (liga + puchary). Jeśli to rewanż, pokazuje też pierwszy mecz,
  stan dwumeczu i regułę bramek na wyjeździe. Bez danych o minutach zmęczenie zawodników nie
  jest szacowane.
* **Eksperyment** (`international.schedule_experiment`):
  * próbka: mecze ligowe klubów grających w danym sezonie w pucharach;
  * model: softmax na różnicy Elo ligi i cechach terminarza, w trzech wariantach:
    * L — odpoczynek i mecze w 14 dniach liczone tylko z ligi;
    * X — to samo, ale liga + puchary;
    * X7 — X plus mecz europejski w ostatnich 7 dniach;
  * okna: dopasowanie 2021/22–2022/23, decyzja 2023/24, później tylko raport.

| Wariant | Okno | Mecze | Log loss | Δ wobec L | 95% CI |
|---|---|---:|---:|---:|---|
| Elo + odpoczynek i mecze w 14 dniach (tylko liga) | selection | 1630 | 0.8994 | +0,0000 | — |
| Elo + odpoczynek i mecze w 14 dniach (liga + puchary) | selection | 1630 | 0.8986 | -0,0008 | [-0,0029; +0,0013] |
| X + mecz europejski w ostatnich 7 dniach | selection | 1630 | 0.8976 | -0,0019 | [-0,0043; +0,0005] |
| tylko różnica Elo ligi | selection | 1630 | 0.8974 | -0,0020 | [-0,0047; +0,0008] |
| Elo + odpoczynek i mecze w 14 dniach (tylko liga) | holdout | 4091 | 0.9384 | +0,0000 | — |
| Elo + odpoczynek i mecze w 14 dniach (liga + puchary) | holdout | 4091 | 0.9385 | +0,0002 | [-0,0015; +0,0019] |
| X + mecz europejski w ostatnich 7 dniach | holdout | 4091 | 0.9391 | +0,0008 | [-0,0011; +0,0027] |
| tylko różnica Elo ligi | holdout | 4091 | 0.9369 | -0,0015 | [-0,0033; +0,0004] |

**Wniosek:** brak potwierdzonej poprawy — terminarz z pucharami pozostaje informacją opisową.

### 14.8 Modele pucharowe i reprezentacji (etap 3)

* **Protokół.** Walk-forward: wyniki dnia liczą się od następnego dnia. Okna dla pucharów
  (sezon startu):
  * dopasowanie do 2021/22;
  * kalibracja 2022/23 — hiperparametry i margines remisu;
  * selekcja 2023/24–2024/25 — decyzja;
  * holdout od 2025/26 — tylko raport.

  Dla reprezentacji: kalibracja 2022, selekcja 2023–2024, holdout od 2025. Wyniki niepewne po
  90 min są wykluczone.
* **Kryteria walidacji rynku.** Wszystkie trzy muszą być spełnione:
  * najlepszy wariant lepszy od bazowego (częstości) w oknie selekcji, z 95% przedziałem
    różnicy log loss poniżej zera;
  * co najmniej 300 meczów w oknie selekcji;
  * ECE ≤ 0,03.

  Holdout niczego nie decyduje.
* **Warianty — puchary:**
  * A — Elo tylko z pucharów;
  * B — Elo ligowe z kalibracją siły lig;
  * C — wspólna pula liga + puchary z osobnym K pucharów;
  * D — model goli (Poisson) na różnicy ocen C (lub A);
  * E — dodatkowe statystyki.
* **Warianty — reprezentacje:**
  * N1 — Elo meczów oficjalnych;
  * N2 — N1 + mecze towarzyskie z wagą 1/2;
  * N3 — N2 bez przewagi gospodarza na terenie neutralnym;
  * model goli.

**Europejskie puchary** — mecze: dopasowanie 1878, kalibracja 430, selekcja 1501, holdout 1169; wykluczone niepewne 90': 49.

| Rynek | Wariant | Log loss | Δ wobec bazowego | 95% CI | ECE |
|---|---|---:|---:|---|---:|
| 1X2 (90 min) | częstości z okna dopasowania (klimatologia) | 1.0349 | +0,0000 | — | 0.018 |
| 1X2 (90 min) | A: Elo wyłącznie z meczów pucharowych | 1.0083 | -0,0266 | [-0,0438; -0,0105] | 0.018 |
| 1X2 (90 min) | C: wspólna pula liga + puchary z osobnym K pucharów | 0.9929 | -0,0420 | [-0,0606; -0,0256] | 0.020 |
| 1X2 (90 min) | D: model goli (Poisson) na różnicy oceny C | 0.9964 | -0,0385 | [-0,0579; -0,0212] | 0.019 |
| 1X2 (90 min) | D (wariant A): model goli na różnicy oceny A | 1.0112 | -0,0237 | [-0,0414; -0,0068] | 0.021 |
| Podwójna szansa | częstości z okna dopasowania (klimatologia) | 0.6044 | +0,0000 | — | 0.018 |
| Podwójna szansa | A: Elo wyłącznie z meczów pucharowych | 0.5892 | -0,0152 | [-0,0247; -0,0062] | 0.018 |
| Podwójna szansa | C: wspólna pula liga + puchary z osobnym K pucharów | 0.5806 | -0,0238 | [-0,0339; -0,0148] | 0.020 |
| Podwójna szansa | D: model goli (Poisson) na różnicy oceny C | 0.5825 | -0,0219 | [-0,0322; -0,0125] | 0.019 |
| Podwójna szansa | D (wariant A): model goli na różnicy oceny A | 0.5908 | -0,0136 | [-0,0234; -0,0042] | 0.021 |
| Over/Under 1,5 | częstości z okna dopasowania (klimatologia) | 0.5510 | +0,0000 | — | 0.029 |
| Over/Under 1,5 | D: model goli (Poisson) na różnicy oceny C | 0.5480 | -0,0030 | [-0,0056; -0,0003] | 0.028 |
| Over/Under 1,5 | D (wariant A): model goli na różnicy oceny A | 0.5508 | -0,0002 | [-0,0033; +0,0031] | 0.026 |
| Over/Under 2,5 | częstości z okna dopasowania (klimatologia) | 0.6869 | +0,0000 | — | 0.006 |
| Over/Under 2,5 | D: model goli (Poisson) na różnicy oceny C | 0.6804 | -0,0065 | [-0,0097; -0,0031] | 0.016 |
| Over/Under 2,5 | D (wariant A): model goli na różnicy oceny A | 0.6829 | -0,0040 | [-0,0076; -0,0006] | 0.004 |
| BTTS | częstości z okna dopasowania (klimatologia) | 0.6929 | +0,0000 | — | 0.006 |
| BTTS | D: model goli (Poisson) na różnicy oceny C | 0.6988 | +0,0059 | [+0,0003; +0,0115] | 0.054 |
| BTTS | D (wariant A): model goli na różnicy oceny A | 0.6981 | +0,0052 | [-0,0007; +0,0109] | 0.048 |

Holdout (tylko raport): 1X2 (90 min) (C_combined): Δ -0,0510 [-0,0673; -0,0347], ECE 0.022; Podwójna szansa (C_combined): Δ -0,0290 [-0,0379; -0,0203], ECE 0.022; Over/Under 1,5 (D_goals): Δ -0,0043 [-0,0067; -0,0019], ECE 0.022; Over/Under 2,5 (D_goals): Δ -0,0076 [-0,0111; -0,0045], ECE 0.016; BTTS (D_goals_A): Δ +0,0071 [+0,0006; +0,0141], ECE 0.054.

**Reprezentacje** — mecze: dopasowanie 2197, kalibracja 232, selekcja 453, holdout 330; wykluczone niepewne 90': 12.

| Rynek | Wariant | Log loss | Δ wobec bazowego | 95% CI | ECE |
|---|---|---:|---:|---|---:|
| 1X2 (90 min) | częstości z okna dopasowania (klimatologia) | 1.0686 | +0,0000 | — | 0.013 |
| 1X2 (90 min) | N1: Elo meczów oficjalnych | 0.8691 | -0,1995 | [-0,2309; -0,1672] | 0.042 |
| 1X2 (90 min) | N2: N1 + mecze towarzyskie z połową wagi | 0.8650 | -0,2036 | [-0,2362; -0,1704] | 0.038 |
| 1X2 (90 min) | N3: N2 bez przewagi gospodarza na neutralnym terenie | 0.8653 | -0,2033 | [-0,2362; -0,1700] | 0.040 |
| 1X2 (90 min) | model goli (Poisson) na najlepszej ocenie reprezentacji | 0.8427 | -0,2258 | [-0,2844; -0,1661] | 0.047 |
| Podwójna szansa | częstości z okna dopasowania (klimatologia) | 0.6214 | +0,0000 | — | 0.013 |
| Podwójna szansa | N1: Elo meczów oficjalnych | 0.5107 | -0,1107 | [-0,1284; -0,0918] | 0.042 |
| Podwójna szansa | N2: N1 + mecze towarzyskie z połową wagi | 0.5083 | -0,1131 | [-0,1314; -0,0938] | 0.038 |
| Podwójna szansa | N3: N2 bez przewagi gospodarza na neutralnym terenie | 0.5085 | -0,1129 | [-0,1313; -0,0938] | 0.040 |
| Podwójna szansa | model goli (Poisson) na najlepszej ocenie reprezentacji | 0.4977 | -0,1237 | [-0,1571; -0,0878] | 0.047 |
| Over/Under 1,5 | częstości z okna dopasowania (klimatologia) | 0.5427 | +0,0000 | — | 0.021 |
| Over/Under 1,5 | model goli (Poisson) na najlepszej ocenie reprezentacji | 0.5327 | -0,0100 | [-0,0309; +0,0129] | 0.040 |
| Over/Under 2,5 | częstości z okna dopasowania (klimatologia) | 0.6936 | +0,0000 | — | 0.014 |
| Over/Under 2,5 | model goli (Poisson) na najlepszej ocenie reprezentacji | 0.6908 | -0,0028 | [-0,0400; +0,0341] | 0.048 |
| BTTS | częstości z okna dopasowania (klimatologia) | 0.6882 | +0,0000 | — | 0.021 |
| BTTS | model goli (Poisson) na najlepszej ocenie reprezentacji | 0.6741 | -0,0140 | [-0,0280; +0,0004] | 0.059 |

Holdout (tylko raport): 1X2 (90 min) (N_goals): Δ -0,2402 [-0,3088; -0,1619], ECE 0.045; Podwójna szansa (N_goals): Δ -0,1313 [-0,1719; -0,0861], ECE 0.045; Over/Under 1,5 (N_goals): Δ +0,0186 [-0,0216; +0,0650], ECE 0.074; Over/Under 2,5 (N_goals): Δ +0,0187 [-0,0315; +0,0789], ECE 0.117; BTTS (N_goals): Δ -0,0120 [-0,0319; +0,0081], ECE 0.099.

**Wariant B** (podzbiór 378 meczów selekcji dwóch klubów z lig katalogu): B Δ +0,0171 [-0,0207; +0,0526], A Δ -0,0181, C Δ -0,0223 — samo Elo ligowe z przesunięciem kraju nie jest lepsze od bazowego; kalibracja przez wspólną pulę (C) działa lepiej.

**Wariant E:** za mało meczów pucharowych ze statystykami (0 < 300) — nieuruchomiony.

Parametry: A K=40, przewaga gospodarza 70; C K pucharów=32, K ligi=20, przewaga gospodarza w pucharach 70; D ρ=-0.05.

### 14.9 Status walidacji i generator (etap 4)

| Rynek | Europejskie puchary | Reprezentacje |
|---|---|---|
| 1X2 (90 min) | **zwalidowany wstecznie** (C_combined) | niezwalidowany |
| Podwójna szansa | **zwalidowany wstecznie** (C_combined) | niezwalidowany |
| Over/Under 1,5 | **zwalidowany wstecznie** (D_goals) | niezwalidowany |
| Over/Under 2,5 | **zwalidowany wstecznie** (D_goals) | niezwalidowany |
| BTTS | niezwalidowany | niezwalidowany |

Powody niezwalidowania: BTTS w pucharach — model gorszy od bazowego (Δ > 0, ECE > 0,03); reprezentacje — duża poprawa log loss, ale kalibracja ECE > 0,03 we wszystkich wariantach 1X2 (zbyt pewne prognozy przy 453 meczach selekcji), a rynki goli bez istotnej poprawy.

* **Bramka generatora** (`international.gate_candidates`): przed budową kuponów zdejmuje
  prognozę z każdej selekcji spoza lig krajowych. Wyjątek byłby możliwy tylko dla rynku
  zwalidowanego **i** zatwierdzonego przez Ciebie. Takiej decyzji nie ma, więc mecze pucharowe
  i reprezentacji nie trafiają do automatycznego generatora. W interfejsie widać komunikat
  „Wydarzenie dostępne — model predykcyjny jeszcze niezwalidowany” i nie pojawiają się
  wymyślone procenty.
* Dla rynków zwalidowanych wstecznie zapisywane są **zamrożone prognozy cieniowe** sprzed meczu
  (`stat_observations`, nie `predictions` — generator ich nie widzi). Służą do prospektywnej
  oceny przed ewentualną decyzją. W analizie są opisane jako eksperymentalne.
* **Czego jeszcze nie ma:** przycisku zatwierdzenia modelu pucharowego ani ścieżki, która podawałaby jego prognozy do kuponów (wersja modelu w rejestrze, prognozy w `predictions`). To następny krok — sensowny dopiero po zebraniu rozliczonych prognoz cieniowych.

### 14.10 Harmonogram WWW

Nowe zadania pracują w tym samym workerze sterowanym z panelu, bez Harmonogramu zadań Windows i
bez otwartej karty. Mają nadrabianie zaległości i zapis każdego uruchomienia. Wszystkie
działają przez worker uruchamiany świadomie w panelu.

| Zadanie | Domyślnie | Co robi |
|---|---|---|
| Wyniki i terminarz pucharów UEFA | co 6 h | pierwsze uruchomienie: historia; potem bieżący sezon (football-data 1, PitchAPI 2 zapytania); terminarz |
| Wyniki reprezentacji | co 3 dni | Liga Narodów, eliminacje, mecze towarzyskie (filtr członków UEFA z danych) |
| Nowe mecze pucharowe w ofercie STS | co 3 h | rozpoznanie drużyn, nierozpoznane nazwy, prognozy cieniowe dla rynków zwalidowanych |
| Kontrola kompletności danych | co 24 h | liczba meczów sezonu wobec formatu, niepewne 90', konflikty |
| Eksperymenty modeli pucharowych | co 3 dni | tylko gdy przybyło ≥ 30 wyników; zapis eksperymentu, bez aktywacji |

Mecze europejskie są objęte istniejącym zadaniem składów, bo LM, LE i LK są teraz w adapterze
PitchAPI, a ich nagłówki STS w katalogu. Skład jest pobierany, gdy obie drużyny meczu STS są
rozpoznane.

### 14.11 Interfejs

* **Analiza meczów:**
  * wybór „Rozgrywki” (Wszystkie / Ligi krajowe / Europejskie puchary / Reprezentacje);
  * dla meczu pucharowego blok z etapem, rewanżem i dwumeczem, terminarzem obu drużyn, statusem
    modelu oraz prognozą cieniową albo komunikatem o braku walidacji.
* **Wydarzenia i Kupon na kurs:** ten sam filtr; kolumna „Rozgrywki”.
* **Źródła → Puchary i reprezentacje:**
  * osobne pokrycie: źródła, sezony, wyniki, bieżący sezon, niepewne 90', konflikty, nagłówek
    i mecze STS;
  * P3 i nierozpoznane nagłówki;
  * nazwy STS do potwierdzenia;
  * przycisk importu.
* **Modele → Puchary i reprezentacje:** osobne statusy rynków dla pucharów i reprezentacji
  (ligi krajowe zostały w widoku „Podstawowy”), wynik eksperymentu terminarza, ocena prognoz
  cieniowych i przycisk uruchomienia eksperymentów.

### 14.12 Testy

`tests/test_task009_international.py` — 17 testów na danych syntetycznych (bez sieci i bez
prywatnej bazy):

| # | Wymaganie | Test |
|---|---|---|
| 1 | parsowanie LM | etap, kraj, godzina CET→UTC, rok przechodzący, 90' zamiast „a.e.t.” |
| 2 | parsowanie LE/LK | PitchAPI: faza ligowa, pierwszy mecz, rewanż pewny i niepewny, finał |
| 3 | nagłówki STS | tylko zaobserwowany tekst + dyscyplina; „LIGA MISTRZÓW AFC” i koszykówka osobno |
| 4 | tożsamość klubu | Real Madryt LaLiga = LM (nazwa + kraj); FK Partizani (ALB) ≠ FK Partizan (SRB) |
| 5 | pierwszy mecz / rewanż | para w rundzie; dwa mecze w grupie nie tworzą dwumeczu; rewanż w ofercie |
| 6 | 90 min / dogrywka | football-data `regularTime`; pewny wynik wygrywa z wynikiem z dogrywką |
| 7 | rzuty karne | karne decydują o awansie, nigdy o wyniku |
| 8 | zmiany formatu | faza ligowa od 2024/25, bramki na wyjeździe tylko do 2020/21 |
| 9 | czas dostępności | wynik widoczny dopiero od `available_at` (36 h bez znacznika źródła) |
| 10 | brak przecieku | oceny dnia D tylko z wyników sprzed dnia D; okna protokołu rozłączne |
| 11 | puchary w terminarzu | odpoczynek, mecze w 14 dniach, europejskie, dogrywka w ostatnim meczu |
| 12 | osobne oceny | pule klubów i reprezentacji, statusy per kategoria, hierarchia źródeł pucharów |
| 13 | brak kuponu bez walidacji | bramka generatora zdejmuje prognozę; walidacja bez decyzji nie wystarcza |
| 14 | reprezentacje vs kluby | minuty bramek (103' dogrywka, 91' niepewne), karne, filtr członków UEFA |
| 15 | harmonogram | 5 zadań w harmonogramie WWW z limitami; tryb offline i warunki treningu |
| 16 | deduplikacja źródeł | jeden mecz z dwóch źródeł = jeden wynik; alias z meczu; sprzeczny dowód odrzucony |
| 17 | UI / Playwright | kategorie interfejsu; scenariusz przeglądarki „puchary_kategoria” |

Ścieżki interfejsu (`python -m sports_edge.qa`, AppTest): +3 ścieżki — „analiza: europejskie
puchary”, „źródła: puchary i reprezentacje”, „modele: puchary i reprezentacje”. Playwright
(`python -m sports_edge.qa_visual`): nowy scenariusz „Analiza meczu: kategoria Europejskie
puchary” (komunikat o braku walidacji widoczny, brak przewijania i uciętych przycisków) obok 14
stron w 5 rozdzielczościach. Demo zawiera jeden fikcyjny mecz w nagłówku „KLUBOWE - LIGA
MISTRZÓW”. Poprawiono też ukryty błąd: kolejność zapisów ustawień harmonogramu z tej samej
sekundy była rozstrzygana przez skrót identyfikatora (teraz kolejność zapisu).

**Wynik:** 325 testów zaliczonych (pytest; w tym 17 nowych), ruff i formatowanie bez uwag. Ścieżki interfejsu: 25/25.

Playwright, pełny przebieg 78 kontroli: 76 zaliczonych. 2 nowe kontrole scenariusza pucharowego nie przeszły — lokator szukał przycisku po roli „button”, a kontrolka segmentowa jej nie ma. Po zmianie lokatora na tekst ponowny przebieg Analizy meczów (5 rozdzielczości i 4 scenariusze × 2 rozdzielczości) dał 13/13. Komunikat o braku walidacji jest widoczny, bez poziomego przewijania i uciętych przycisków.


### 14.13 Ograniczenia danych

* **Brak sezonu 2026/27 w openfootball.** Bieżący sezon LE i LK pochodzi tylko z PitchAPI.
  Rewanże i finały mogą być tam niepewne po 90 min, dopóki inne źródło ich nie potwierdzi.
* **Brakujące „a.e.t.” w openfootball.** openfootball/champions-league miejscami pomija
  znacznik „a.e.t.”: Inter–Barcelona 06.05.2025 ma „4-3” zamiast 3:3 po 90 min. Na sezonach
  wspólnych z football-data wykryto 1 taki konflikt na ~430 meczów; wygrywa football-data, a
  rozliczenie jest wstrzymane. Na starszych sezonach (tylko openfootball) podobny błąd nie może
  zostać wykryty.
* **Nazwy do potwierdzenia.** Część pisowni czeka na potwierdzenie (lista w raporcie importu i w
  aplikacji). Ich mecze nie są importowane, stąd luki, np. LK 2025/26.
* **Brak statystyk meczów pucharowych.** Wariant E nie był możliwy.
* **Reprezentacje.** Brak danych o selekcjonerach i dostępności zawodników. Nagłówki STS
  reprezentacji nie wystąpiły jeszcze w Twoich ofertach.
* **Brak rynków awansu.** Rynki „awans/zwycięzca dwumeczu” nie są parsowane ani wspierane.
* **Wymagana decyzja.** Żadna rozgrywka nie jest „gotowa do typowania” bez Twojej decyzji i
  okresu prospektywnego. Status „zwalidowany wstecznie” to nie aktywacja.
