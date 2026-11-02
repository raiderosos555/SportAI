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
