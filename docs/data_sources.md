# Źródła danych i warunki użycia

Sprawdzono 31.10.2026. Ceny to publiczny cennik w dniu odczytu, nie oferta dla konta;
podatki, waluta rozliczenia, zakres sezonów i retencja umowna wymagają potwierdzenia.
Nie zarejestrowano kont, nie aktywowano subskrypcji i nie pobrano zbiorów sportowych.
„Niepotwierdzone” oznacza brak dowodu w sprawdzonych materiałach, nie brak funkcji.

## Pokrycie, historia i czas

| Źródło | Dyscypliny / ligi / rynki | Historia i kursy otwarcia/zamknięcia | Timestamp / API / ocena |
|---|---|---|---|
| [football-data.co.uk](https://football-data.co.uk/data.php) | Futbol europejski i inne ligi, w tym Ekstraklasa; 1X2, wybrane totals/AH | Wyniki od 1993/94, kursy od 2000/01; dwa zestawy od 2019/20, wcześniej ograniczony closing Pinnacle | CSV, czas zebrania nie jest dokładnym timestampem każdej oferty; pre-closing nie znaczy opening. Ostrzeżenie o nieaktualnych kursach Pinnacle od 23.07.2025. Blokada prawna użycia w projekcie niżej |
| [football-data.org](https://www.football-data.org/pricing) | Futbol, 12/30/50 rozgrywek zależnie od planu; wyniki/terminarze/tabele, głębsze dane płatne | ML Pack Light wymienia 10 sezonów; nie potwierdzono archiwum historycznych zmian kursów ani O/C | API, metadane aktualizacji nie gwarantują dawnego czasu dostępności; dobre do terminarzy/wyników po sprawdzeniu praw |
| [StatsBomb Open Data / hudl](https://github.com/hudl/open-data) | Wybrane rozgrywki/sezony futbolu, events, składy, 360 dla części meczów; brak kursów | Lista `competitions.json` określa realny zakres; nie cały świat i nie ciągłe live | JSON/API eksport, zegar akcji w meczu ≠ data publikacji; dobry materiał do badania cech, wymaga połączenia z legalnymi kursami |
| [Understat](https://understat.com/) | EPL, La Liga, Bundesliga, Serie A, Ligue 1, RFPL; xG/strzały, nie kursy | Dokładny zakres niezweryfikowany w tym audycie; O/C nie dotyczy | Nie potwierdzono wspieranego publicznego API ani historycznych czasów publikacji; scraper biblioteki nie jest licencją |
| [ClubElo API](https://clubelo.com/API) | Rankingi klubów futbolowych; brak kursów | Historia i wersje archiwalne niezweryfikowane | Odczyt 403; warunki, limity i cennik niepotwierdzone. Nie bazujemy na założonej swobodzie pobierania |
| [API-Football](https://www.api-football.com/documentation_v3) | Futbol; leagues/coverage zależne od sezonu, statystyki, składy, absencje, pre/in-play odds | Prematch zwykle 1–14 dni przed meczem; **7 dni historii**, in-play bez archiwum | API i `update`; prematch aktualizacja co ok. 3 h, za mało do precyzyjnego CLV. Nie zastępuje wielosezonowego archiwum |
| [The Odds API — historia](https://the-odds-api.com/historical-odds-data/) | Wiele sportów: futbol, NBA/NCAA/Euroliga, MLB, NHL, duży tenis; h2h/spreads/totals, wybrane props/periods | Główne rynki od 06.06.2020, interwał 10 min, od IX 2022 5 min; dodatkowe od 03.05.2023 | API zwraca snapshot <= żądany czas i last_update. Pierwszy/ostatni snapshot to przybliżenie O/C, nie dowód pierwszej/ostatniej transakcji |
| [SportsDataIO — historia](https://sportsdata.io/help/historical-data-integration-guide) | Liga i feed osobno; m.in. NFL/NBA/MLB/NHL/NCAA, futbol/tenis zależnie od produktu; props/futures/game lines | Historia od początku pokrycia danego feedu; ruch linii O/C dostępny, props/futures mogą wymagać osobnego archiwum | API; korekty statystyk nadpisywane, brak ogólnego as-of. Odds line movement jest wyjątkiem. Dokładny zakres sezonów wymaga uzgodnienia |
| [Sportradar](https://developer.sportradar.com/odds/reference/intro) | Multisport, osobne Prematch/Futures/Live/Player Props/Core; liga/rynek według coverage matrix | Wieloletnie archiwum i konkretny zakres O/C niepotwierdzone na odczytanej stronie | API istnieje; semantykę timestampów i retencję trzeba zweryfikować na wybranym feedzie/umowie |
| [tennis-data.co.uk](https://www.tennis-data.co.uk/data.php) | ATP/WTA, głównie zwycięzca meczu i wyniki | Strona deklaruje 26 lat wyników/kursów; aktualizacja po tygodniu turniejowym | Arkusze, nie potwierdzono dokładnych czasów ofert/O/C; `notes.txt` zwrócił 403. Ryzyko błędnego pojedynczego kursu wykazane w badaniu Clegga/Cartlidge'a |
| Oficjalne ligi/federacje | Np. NBA/NHL/MLB, ATP/WTA, PZPS/PZPN, wydawcy gier; terminarze, wyniki, statystyki | Każdy feed osobno, kursów zwykle nie można z tych danych wywnioskować | Publiczna strona nie daje ogólnego prawa do API/scrapingu. Nie przeprowadzono audytu każdej federacji; potrzebna lista endpointów i warunków |
| Publiczne zbiory badawcze / Sackmann | Możliwe dane tenisowe i dane do replikacji prac | Konkretnego zrzutu Sackmanna nie zweryfikowano (404); nie zakładamy aktualnego zakresu | Relacja market-efficiency-lab wspomina CC BY-NC-SA; to wtórna informacja, nie weryfikacja licencji. Nie pobierać kopii z przypadkowego mirrora |

Słownik kolumn futbolowych rozróżnia kursy pre-closing oraz kolumny z `C`:
[notes.txt](https://football-data.co.uk/notes.txt). Pokrycie trzeba badać per
`sport × competition × season × bookmaker × market`, nie samą nazwą dostawcy.

## Koszt, licencja, retencja i decyzja

| Źródło | Darmowe / płatne | Prawa i decyzja |
|---|---|---|
| football-data.co.uk | Pliki bez opłaty, brak osobnego potwierdzonego cennika dla naszego użycia | Strona ogranicza użycie do osób prywatnych i wyklucza komercyjne/data-training produkty botów/AI. **Nie używać do tego projektu bez wyraźnych praw**; nie redystrybuować |
| football-data.org | 0 EUR, 10 wywołań/min, 12 rozgrywek i opóźnione wyniki; livescores 12 EUR/mies.; ML Light/Deep Data 29 EUR; Standard 49 EUR; Advanced 99 EUR | [Warunki](https://www.football-data.org/about): klucz dla jednej aplikacji, fair use, atrybucja; po anulowaniu brak prawa do prezentowania danych na własnej stronie/usłudze. Zakres treningu i retencji offline nie jest jasno rozstrzygnięty, wymaga potwierdzenia |
| StatsBomb Open Data | Bezpłatny wybrany zbiór, komercyjnego feedu nie wyceniono | README dopuszcza badania i żąda atrybucji/logo przy publikacji. Pełne warunki przechowywania/dalszego produktu trzeba sprawdzić; nie traktować jak MIT |
| Understat / ClubElo | Brak potwierdzonego płatnego cennika lub limitów w audycie | Status praw treningu, bulk i retencji nieustalony; wstrzymane |
| API-Football | [Cennik](https://www.api-football.com/): free 100 zapytań/dzień, ograniczone sezony; 19/29/39 USD miesięcznie, odpowiednio 7500/75000/150000 dziennie | Nie uruchamiano klucza. Zakres przechowywania/ML/redystrybucji wymaga przeglądu [terms](https://www.api-football.com/terms) przed pobraniem |
| The Odds API | [Cennik](https://the-odds-api.com/): free 500 kredytów/mies., bez historii; 20k/100k/5M/15M za 30/59/119/249 USD miesięcznie | [Warunki 31.08.2026](https://the-odds-api.com/terms-and-conditions.html) pozwalają przechowywać bezterminowo i trenować modele; zabraniają samodzielnego produktu z surowych danych. Dobry kandydat, ale brak potwierdzenia STS i wykonalności oferty |
| SportsDataIO | [Trial](https://sportsdata.io/help/scrambled-data) zmienia część wartości; cena produkcji/archiwum niepotwierdzona, kontakt handlowy | Trial nie nadaje się do badań ROI. Replay testuje integrację, nie zastępuje pełnego archiwum rewizji. Retencja i redystrybucja według umowy |
| Sportradar | Trial zależny od produktu; dokładne limity/cena niepotwierdzone | Osobna oferta/licencja na feed i magazynowanie, nie przyjmujemy „wszystkie ligi w trial” |
| tennis-data / źródła oficjalne / zbiory badań | Publiczny dostęp nie oznacza darmowej licencji; koszty konkretnego feedu nieustalone | Sprawdzenie praw per zbiór, brak redystrybucji bez zgody; data lineage i warunki wersjonowane |

Nie pomylić **the-odds-api.com** z podobnie nazwanymi domenami. Kwota historycznego
zapytania jest zależna od regionu/rynku, dla endpointu event także wydarzenia; budżet
liczyć przed backfillem. Płatne źródło wymaga zgody użytkownika na konkretny zakup.

## Rekomendacja i luki blokujące

Pierwszy etap: niewielka, legalna próbka kursów i wyników z manifestami, najlepiej
prospektywnie. Kandydat kursowy: The Odds API (free do oceny schematu i pokrycia);
wyniki: football-data.org po sprawdzeniu warunków, ewentualnie licencjonowany feed.
StatsBomb dopiero do osobnego badania cech, jeśli legalnie można połączyć wybrane mecze.
Nie rekomendujemy obecnie płatnego zakupu bez listy wydarzeń/rynków, próbki i kosztorysu.

Luki: archiwalne timestampy wyników/absencji, dostępność rzeczywistego kursu i limitu,
STS w feedzie, historia korekt, reguły konkretnych props, niszowe ligi/ITF/PlusLiga/esport,
pełne ceny archiwów, prawa do publikacji danych. Brak timestampu nie jest zerowym
opóźnieniem. Brak notowania nie jest przegraną ani zakładem po sztucznym kursie.

## TASK 002 — źródła ofert STS i Superbet (sprawdzono 31.10.2026)

| Źródło | Ustalenia | Status w aplikacji |
|---|---|---|
| STS — [sts.pl/oferta-pdf](https://www.sts.pl/oferta-pdf) | Oficjalne linki `retail-api.sts.pl/printed-offer?day=today/tomorrow/week`; HTTP 200, PDF z warstwą tekstową, odświeżany co ok. 5 min (`max-age=300`); brak `robots.txt` na hoście retail-api; regulaminu pod kątem automatycznych odczytów nie przeanalizowano | Import ręczny + pobranie na kliknięcie (opt-in, limit 1/10 min), bez harmonogramu |
| Superbet PL | Brak oferty PDF na superbet.pl (strona, sitemap, wiki); `robots.txt` blokuje parametry URL i agentów AI | Tylko ręczny import PDF użytkownika; parser niezweryfikowany |
| ArbiScan API | Agregator (nie API operatora), deklaruje STS i Superbet; 349 / 499 / 799 zł mies.; prematch, historia wyłączona; brak zapisów licencyjnych w dokumentacji | Adapter gotowy, zablokowany bez klucza i zgody; nie wykonano zapytań |
| PropLine | Format the-odds-api; Superbet PL w katalogu, STS niepotwierdzony; free 1 000/dzień, 9–79 USD | Nie zintegrowano |
| JoJ API / ArbPlay | Rynek grecki (Superbet GR), 0,01 USD/wywołanie lub RapidAPI 29–399 USD | Nie dotyczy PL |

Szczegóły, koszty i decyzje: [raport TASK 002](task_002_report.md), sekcje 4–8.
