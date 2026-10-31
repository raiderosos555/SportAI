# Wybór obszarów badawczych

Ranking dotyczy **możliwości rzetelnego testowania**, nie przyszłego ROI. Nie korzysta
z holdoutu, wyników strategii ani z deklarowanych zysków zewnętrznych autorów.

Wagi początkowe: kursy historyczne 30%, dane sportowe 25%, niezależne wydarzenia 20%,
wykonalność kursu 15%, koszt/prawa 10%. Każdy wymiar 0–5; wynik to średnia ważona.
Prawo do użycia danych i możliwość odtworzenia czasu to dodatkowe **twarde bramki**:
wysoki wynik nie może kompensować zakazu licencyjnego ani braku timestampów.

## Wstępny ranking warunkowy

Poniższe liczby są oceną projektową audytora, nie pomiarem pobranego zbioru.
Zakładają legalny feed historyczny/prospektywny (np. The Odds API), a nie nieuprawnione
użycie darmowych CSV. Niezależną liczebność trzeba policzyć z event_id, nie notowań.

| Kolejność / obszar | Kursy | Sport | n | Wykonalność | Koszt/prawa | Wynik /5 | Warunek |
|---|---:|---:|---:|---:|---:|---:|---|
| 1. Futbol, jedna dobrze pokryta liga, regulation 1X2 | 4 | 4 | 4 | 3 | 3 | 3,75 | Legalny feed wyników i kursów, wspólny czas, bez zakazanych zbiorów |
| 2. NBA/MLB, podstawowe moneyline/total | 4 | 4 | 4 | 3 | 2 | 3,65 | Prawa do statystyk, listed pitchers/dogrywki, snapshoty |
| 3. ATP/WTA main tour, zwycięzca | 3 | 4 | 4 | 3 | 2 | 3,35 | Zweryfikowany feed danych, krecz/walkover, zgodność identyfikatorów |
| 4. Futbol mniejszych lig, 1X2 | 3 | 2 | 3 | 2 | 3 | 2,60 | Faktyczne pokrycie, data startu, limity i mniejsza płynność |
| 5. Player props / rynki okresów | 2 | 3 | 3 | 2 | 2 | 2,45 | Krótsza historia, silna korelacja, start zawodnika, rewizje |
| 6. PlusLiga / Challenger / ITF / esports (oddzielne przyszłe audyty) | 1 | 2 | 2 | 1 | 2 | 1,55 | Obecnie brak potwierdzonego kompletu danych/reguł; grupowanie nie znaczy jednego modelu |

Różnice 0,1 pkt nie mają empirycznej precyzji. Przed zakupem ocenę zastępuje audyt
próbki: coverage, % znanych timestampów, n eventów/lat, spójność, koszt na event,
przyczyny braków i dostępni operatorzy. Wynik nie jest zezwoleniem na pobranie płatnego feedu.

## Pierwszy eksperyment po fundamentach

**Hipoteza**: konsensus prawdopodobieństw innych operatorów może wskazywać oferty,
których oczekiwany zysk netto jest dodatni poza próbą. Prostota pozwala najpierw
sprawdzić jakość danych, a nie ukryć ją za ML. Pierwsza liga wybierana według
pokrycia i reguł (kandydat: EPL), bez obietnicy przewagi.

Model: dla każdego operatora proporcjonalny devig pełnego 1X2; konsensus jako
średnia tych rozkładów z wyłączeniem ocenianego operatora. Obecność co najmniej
trzech niezależnie identyfikowalnych pozostałych operatorów to propozycja do zamrożenia.
Wspólni dostawcy feedów ograniczają niezależność, co trzeba opisać. Benchmark:
rynek z chwili decyzji i prosta częstość z treningu. Stawka stała w jednostkach.

Horyzont T−60 min, wstępne okno świeżości 5 min; kurs po uwzględnieniu ustalonego
opóźnienia musi być dostępny. Początkowy próg EV netto oraz jego ewentualna kalibracja
wyłącznie w development. Dla PL/STS wymagany rzeczywisty kurs STS i reguły STS;
Pinnacle lub inny operator z podatkiem PL to tylko scenariusz stresowy, nie wynik STS.

Potrzebne dane: terminarz i jego korekty, wszystkie trzy kursy z tego samego momentu,
bookmaker/source ID, czasy snapshot/update/ingest, wynik regulation i jego publikacja,
closing zgodnej linii, regulamin i koszt profilu, prawa do retencji. Brak legalnych
danych historycznych oznacza początek przyszłego paper, nie backdating danych dzisiejszych.
Free 500 kredytów/mies. to budżet do pilota schematu/pokrycia, nie gwarancja pełnego
monitoringu wszystkich wydarzeń. Wyliczyć koszt harmonogramu przed uruchomieniem.

**Sukces techniczny**: pełny lineage, brak naruszeń czasu, identyczna powtórka metryk,
brak domyślnych kursów, rozliczenie kontrolnej próbki zgodne z ręcznym wyliczeniem.
**Sukces badawczy**: niezależny, odpowiednio mocny przyszły test, dodatnia dolna granica
CI netto po korekcie rodziny testów, odporność na koszty i utratę kursów. CLV pomocniczy.
**Odrzucenie/inconclusive**: górna CI poniżej efektu praktycznego / CI obejmuje zero;
niekompletne dane ⇒ nieważny eksperyment. Szczegóły w metodologii.

## Rozszerzanie

Po pilocie jeden nowy rynek albo sport na iterację. Najpierw źródła/reguły i fixture,
potem baseline, prerejestracja i nowy okres. Cała rodzina eksperymentów zachowuje
historię prób. Nie odrzucać największych lig jako „zbyt efektywnych” ani nie promować
małych jako „łatwych” bez danych. Props/LLM dopiero po działającym benchmarku i archiwum.
