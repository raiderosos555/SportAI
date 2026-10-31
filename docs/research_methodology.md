# Metodologia wykrywania przewagi

Hipoteza zerowa ekonomiczna: oczekiwany **zysk netto po wszystkich kosztach ≤ 0**
dla zarejestrowanej polityki i dostępnego zbioru ofert. Dodatnie modelowe EV ani
dodatnie ROI pojedynczej próby nie odrzuca tej hipotezy.

## Chronologia i niezależność

1. Wybór rynku na podstawie wykonalności danych; katalog wyborów zapisany przed testem.
2. Wstępny okres eksploracyjny do kontroli jakości, cech i kalkulacji mocy.
3. Zewnętrzny walk-forward: train → validation → test kolejnego okresu. Wewnętrzne
   foldy dobierają preprocessing, model, kalibrator, wagi ensemble i próg EV.
4. Ostatni okres holdout z identyfikatorem/hashami i zamrożonym planem. Jedno otwarcie
   po zakończeniu projektu polityki. Powtórny tuning tworzy nową hipotezę i wymaga nowych danych.
5. Potwierdzenie na przyszłych wydarzeniach w paper, z decyzjami zapisanymi przed startem.

Przedziały są półotwarte `[start, end)`. Wszystkie selekcje meczu zostają w tym samym
foldzie; przy zależnościach turniejowych rozważamy wspólny turniej. Etykiety treningu
muszą być rozstrzygnięte i dostępne przed odcięciem. Purge usuwa nakładające się
okna etykiet; embargo odpowiada rzeczywistemu opóźnieniu publikacji i horyzontowi,
nie magicznej stałej 24 h. Hiperparametry i selekcja ligi nie widzą testu zewnętrznego.

## Jakość probabilistyczna

Liczymy na wszystkich kwalifikujących się wydarzeniach, nie tylko zaakceptowanych typach:

| Metryka | Definicja / interpretacja |
|---|---|
| Log loss | `−mean(log p_observed)`; polityka clippingu, np. epsilon 1e−12, zapisana z góry |
| Brier | Dla K stanów `mean(sum_k (p_k−y_k)^2)`; dla binary dodatkowo podać, czy raportowany jeden składnik (różnica ×2) |
| Kalibracja | Reliability diagram, intercept/slope; przedziały w koszykach, liczebności, bez dopasowywania do holdoutu |
| ECE | Stałe koszyki ustalone na treningu, suma wag × bezwzględna różnica p i częstości; zależy od binningu i próby |
| Różnica do rynku | Sparowane straty dla tych samych wydarzeń, ten sam stan/okres i kurs z chwili decyzji |
| Stabilność | Sezony/okresy, ligi/rynki i drift; bez wybierania zwycięskiego fragmentu testu |

Benchmark rynkowy: pełne rozłączne wyniki tej samej oferty i czasu; początkowo
`q_i=(1/o_i)/sum_j(1/o_j)`. Metoda proporcjonalna to przybliżenie; Shin/power jako
zarejestrowane testy wrażliwości. Overround nie jest dodatkowym podatkiem od EV:
oferowany kurs już zawiera marżę. Closing jest osobnym benchmarkiem ex post,
często z bogatszą informacją — nie uczciwym wejściem do prognozy T−60.

## Wynik ekonomiczny i CLV

Raport zawiera n selekcji i n niezależnych eventów, win rate (jawnie traktowane zwroty
i half outcomes), średni kurs, średnie prognozowane EV, sumę stawek, zysk w jednostkach,
ROI brutto/po podatku od stawki/po pozostałych podatkach/netto po wykonaniu.
ROI to `sum(profit)/sum(deposited_stake)`, nie nieważona średnia ROI kuponów przy różnych
stawkach. Zwroty są w mianowniku obrotu wg przyjętej definicji; pokazujemy ich udział.
Koszty danych/API i obliczeń są także raportowane jako stałe koszty programu poza
ROI transakcyjnym. „Rentowny” wymaga dodatniego wyniku po ich alokacji.

Profit factor = suma dodatnich zysków / wartość bezwzględna sumy ujemnych; przy braku
strat raport „niezdefiniowany”, nie nieskończony sukces. Drawdown liczony na kapitale
markowanym według jawnej kolejności rozliczeń, z osobnym kapitałem zablokowanym.
Pierwsza polityka: stała stawka, bez Kelly/martingale. Porównujemy wynik z sumą
prognozowanych zysków, prezentujemy rozkład reszt i niepewność modelu.

CLV cenowe = `o_entry/o_close − 1` dla tej samej selekcji, linii i reguł. Alternatywa
benchmarkowa = `o_entry × p_close_devig − 1` (bez podatków; wersja netto przez silnik
wypłat). Nie mieszać miar w jednym szeregu. Zamknięcie: ostatni aktywny snapshot przed
faktycznym startem w dopuszczalnym oknie, bez in-play. Brak zgodnej linii ⇒ brak CLV,
raport odsetka braków i możliwego selection bias. Dodatni CLV jest diagnostyką,
nie samodzielnym dowodem zysku netto. Exchange wymagałby osobnej definicji ceny/fill.

## Niepewność, wielokrotne testy i odporność

Jednostką resamplingu jest co najmniej wydarzenie ze wszystkimi selekcjami. Dla serii
stosujemy bloki czasu (np. tygodnie) i analizy wrażliwości na długość, zachowując wspólne
bloki dla porównywanych strategii. Bootstrap ROI za każdym razem przelicza iloraz sum.
Przy kilku sezonach/blokach CI może być niewiarygodny — raport „zbyt mało okresów”,
bez udawania, że 10 000 replik tworzy 10 000 niezależnych obserwacji.

Rodzina hipotez obejmuje modele, ligi, cechy, progi, prompty i porzucone warianty.
Konfirmacyjnie: uprzednio określona rodzina i Holm dla p-values. Eksploracyjnie można
użyć FDR, ale nie zmienia to wyników w potwierdzenie. Reality Check/SPA dopiero po
walidacji implementacji, wspólnych indeksów resamplingu i poprawnej definicji benchmarku.
Udział dodatnich bootstrapów nie jest prawdopodobieństwem prawdziwości hipotezy.

Moc: przed testem ustalamy minimalną interesującą przewagę netto (np. 2 pp), alpha,
power i model wariancji z development. Orientacyjne `n_eff ≈ ((z_alpha+z_power)σ/δ)^2`
jest przybliżeniem, wymaga korekty korelacji i asymetrii długich kursów. Dla σ≈1,
δ=0,02, testu jednostronnego 5% i mocy 80% to ok. 15,5 tys. **niezależnych** obserwacji,
przed korektą wielokrotności. To syntetyczna ilustracja, nie wymaganie wyliczone z danych.

Testy odporności z góry: opóźnienie 0/30/120 s, brak kwotowań, pogorszenie kursu,
podatek/fee, różne epoki, pojedyncze outliery i udział top-5 zysków, wyłączenie
niewykonalnych ofert, korelacja selekcji, zmiana dostawcy. Analizy progów EV są
wewnętrzną walidacją, nie przeszukiwaniem holdoutu.

## Decyzja o hipotezie

Promocja do kolejnego paper wymaga zgodności danych/reguł i powtarzalnego wyniku
zewnętrznych foldów. Potwierdzenie przewagi wymaga dodatkowo prospektywnego okresu
zamkniętego według planu, odpowiedniej mocy, skorygowanego testu oraz dodatniej dolnej
granicy CI netto. Raportujemy wielkość efektu, nie tylko istotność. Nietypowe koszty
wykonania i stałe koszty danych muszą zostać pokryte.

CI obejmujące zero ⇒ **brak wystarczających dowodów**. Górna granica poniżej minimalnej
interesującej przewagi ⇒ hipoteza nieprzydatna ekonomicznie przy tej mocy; górna poniżej
zera ⇒ dowód straty w analizowanych warunkach. Błąd danych ⇒ wynik nieważny, nie przegrana
hipoteza ekonomiczna. Brak istotności nie dowodzi pełnej efektywności wszystkich rynków.
