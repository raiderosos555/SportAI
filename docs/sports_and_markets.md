# Dyscypliny i rozszerzalne rynki

Katalog to specyfikacja zakresu, nie deklaracja dostępnych danych ani wdrożonych modeli.
Sport ma własny adapter cech/wyników; rynek ma osobny adapter rozliczenia z wersją
regulaminu operatora. Wspólne pozostają rozkłady, EV, czas, eksperymenty i metryki.

| Sport / rozgrywki do oceny | Rodziny rynków | Szczególna semantyka |
|---|---|---|
| Futbol: Ekstraklasa/niższe ligi, EPL, La Liga, Bundesliga, Serie A, reszta świata, puchary/reprezentacje | 1X2, Double Chance, DNB, dowolne O/U, AH/EH, BTTS, rożne, kartki, strzelcy, props, połowy | 90 minut vs awans, doliczony czas, kartki ławki i druga żółta |
| Tenis: ATP/WTA, Challenger, ITF, Szlemy | Mecz/set, handicap gemowy/setowy, suma gemów, tie-break, props | Walkover, krecz, liczba rozegranych setów, super tie-break, nawierzchnia |
| Koszykówka: NBA, Euroliga, NCAA, ligi europejskie, kadry | Moneyline, spread, total, punkty/zbiórki/asysty/trójki, kwarty | Dogrywka, występ startera, minimalny udział, statystyki po korekcie |
| Siatkówka: PlusLiga, LM, ligi krajowe, kadry | Zwycięzca, dokładne sety, handicap setów/punktów, sumy | Złoty set, tie-break, przerwanie, różne formaty punktowe |
| Hokej: NHL, Europa, kadry | Moneyline, regulation 1X2, handicap, gole, tercje | Dogrywka, karne i ich wpływ na wynik rynku |
| Baseball: MLB, inne ligi | Moneyline, run line, totals, innings, props | Listed pitchers, skrócone mecze, zawieszenie, dodatkowe innings |
| Esport: CS2, LoL, Dota 2, kolejne gry | Seria/mapa, handicap map, rundy/kille, props | Walkover, zmiana składu, patch, remake, format BO |
| Inne: wyścigi, turnieje | Zwycięzca z pola N uczestników, klasyfikacja | Dead heat, dyskwalifikacje, etap vs cały turniej |

## Tożsamość rynku

Klucz ekonomiczny obejmuje event, definicję i wersję, okres, linię, subject_id,
dogrywkę, regułę rozliczenia i operatora. `Selection` ma klucz należący do instancji
`Market`. Nazwy wyświetlane nie są identyfikatorami. Nie mapujemy automatycznie
podobnie brzmiących graczy/zespołów. `Event.participant_ids` nie narzuca liczby dwa.

`state_keys` są rozłącznymi, wyczerpującymi stanami prognozy. `selection_keys` to
dostępne zakłady. Dla Double Chance trzy selekcje nakładają się; ich prawdopodobieństwa
**nie sumują się do 1**. Model prognozuje H/D/A, a osobny adapter mapuje stany na wypłaty.
Dla exact score wymagany jest kontrolowany ogon rozkładu, nie obcięcie i ukryta renormalizacja.
Props i sumy wymagają rozkładu licznika wystarczającego do rozliczenia danej linii.

## Reguły i rozszerzanie

`config/markets.toml` zawiera działające szablony `winner`, `asian_total` i przykład
`double_chance` bez adaptera. `RuleRegistry` mapuje **wersjonowany** identyfikator na
dedykowaną klasę. Nieznany identyfikator lub status wyniku odrzuca rozliczenie.
Nowy rynek wymaga konfiguracji, walidatora wyniku, adaptera reguł, testów przypadków
granicznych i dowodu pokrycia danych; sam wpis TOML nie czyni rynku obsługiwanym.

Zaimplementowane adaptery przyjmują tylko `completed` z pasującym okresem i dogrywką.
Zwycięzca obsługuje dowolną liczbę rozłącznych wyników (także jawny remis).
AsianTotal dzieli linie ćwiartkowe na dwie połowy; np. over 2,25 przy 2 daje half-loss,
over 2,75 przy 3 half-win. Obsługuje nieujemne całkowite sumy; nie jest adapterem AH/EH.

Arytmetyka `settle` przyjmuje już rozstrzygnięty Outcome. Nie ustala zwycięzcy na
podstawie uniwersalnego wyniku meczu. PUSH oznacza remis z linią, VOID unieważnienie;
mogą mieć ten sam mnożnik wypłaty, ale inne przyczyny i zasady podatkowe.
Dead heat, cashout, akumulatory, exchange LAY i netting nie są zaimplementowane.

Przed włączeniem konkretnego operatora trzeba ustalić dogrywkę, przerwania,
przełożenia, zwroty, half outcomes, zmianę zawodnika, krecz, minimalny udział i źródło
oficjalnego wyniku. Nieznany przepis oznacza `unknown_rules`, bez domyślnego zwrotu.
