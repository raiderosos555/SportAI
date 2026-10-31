# Audyt istniejących projektów i badań

Data dostępu: **31.10.2026**. Przegląd źródeł pierwotnych przed wyborem architektury.
Nie uruchomiono cudzych modeli, nie pobrano zbiorów kursowych ani nie odtworzono ROI.
Inspekcja kodu ≠ reprodukcja eksperymentu. Wynik całego audytu: **brak wystarczających
dowodów na rentowność Sports Edge AI**; są komponenty i falsyfikowalne hipotezy.

Klasy dowodów: **A** deklaracja autora/repo; **P** publikacja (osobno preprint/recenzja);
**I** niezależna reprodukcja przez wskazany zespół; **R** deklarowany rachunek rzeczywisty;
**S** symulacja/paper. Nie są to kolejne stopnie jednej skali: P może opisywać S,
R może być nieaudytowaną deklaracją A. W TASK 001 własnych I i R jest zero.

## Market Efficiency Lab — kandydat do audytu

Repozytorium [Volodymyr4K/market-efficiency-lab](https://github.com/Volodymyr4K/market-efficiency-lab).
Commit potwierdzony endpointem gałęzi `main`: **1901d4b3b1e810af9d5bfa934d4c542c3f1c31ea**.
Kod walidacji odczytano przez ten ref; README i licencję z main podczas tego samego audytu.

| Pytanie | Ustalenie / granica dowodu |
|---|---|
| Sport/rynki | MLB moneyline, run line, totals, first-5; tenis winner i analizy dodatkowych rynków |
| Dane | Autor wskazuje The Odds API, Statcast/MLB, tenis-data, Sackmann, pogodę; raw kursy nie są dołączone |
| Algorytmy | XGBoost i cechy MLB; surface Elo/serve w tenisie; porównanie LLM |
| Walidacja | Deklarowany walk-forward; w kodzie trening fold < test, mediana imputacji tylko z treningu; osobny dobór progów |
| ROI / n | README: MLB **+9,3%**, CI **[4,0%;20,9%]**, **1215 zakładów**, 2023–2026. To A+S, nie nasz wynik |
| Wynik negatywny | README: 75 044 meczów tenisowych, Brier ATP Elo 0,2032 vs rynek 0,1949; LLM 1700 meczów, brak wykazanej przewagi nad Elo |
| Niezależność | Brak naszej replikacji; retrospektywny projekt nie dowodzi nietkniętej historii decyzji badawczych |
| Kursy | Deklarowane rzeczywiste closing odds; nie zweryfikowano dostępności w chwili historycznej decyzji |
| Koszty | Marża jest zawarta w użytych kursach; polskie podatki/STS i pełne koszty nie zostały potwierdzone |
| CLV | Brak niezależnie odtworzonego szeregu CLV; porównanie z closing nie oznacza dodatniego CLV wejścia |
| Reprodukcja | Kod jest dostępny; brak raw kursów blokuje pełną reprodukcję ekonomiczną z samego repo |
| Realne pieniądze | Opisany virtual ledger nie jest dowodem obstawiania realnego; rachunku nie audytowano |
| Prawo użycia | Odczytany LICENSE: MIT dla kodu, z zastrzeżeniem osobnych praw dostawców danych |

Źródło deklaracji: [README](https://github.com/Volodymyr4K/market-efficiency-lab/blob/main/README.md).
Licencja: [LICENSE](https://github.com/Volodymyr4K/market-efficiency-lab/blob/main/LICENSE).

### Ustalenia z kodu, nie tylko README

1. [core/validation/walkforward.py](https://github.com/Volodymyr4K/market-efficiency-lab/blob/1901d4b3b1e810af9d5bfa934d4c542c3f1c31ea/core/validation/walkforward.py)
   rzeczywiście izoluje wcześniejsze foldy i mediany. Samo `fold < F` nie waliduje
   jednak czasu publikacji cech ani późniejszych korekt źródeł. Zależność od XGBoost
   ogranicza użyteczność jako uniwersalnego silnika modeli.
2. [nested_wf_validation.py](https://github.com/Volodymyr4K/market-efficiency-lab/blob/1901d4b3b1e810af9d5bfa934d4c542c3f1c31ea/sports/mlb/scripts/nested_wf_validation.py)
   opisuje pozostające ograniczenia: filtr `bias_suspect` wyprowadzony z obecnego stanu,
   brak części filtrów produkcyjnych i wybór projektu modeli po obejrzeniu wszystkich danych.
   To istotne zastrzeżenie wobec interpretacji 2026 jako w pełni niezależnego holdoutu.
   Kierunku wpływu nieobecnych filtrów nie przyjmujemy bez testu, choć autor go sugeruje.
3. [core/validation/stats.py](https://github.com/Volodymyr4K/market-efficiency-lab/blob/1901d4b3b1e810af9d5bfa934d4c542c3f1c31ea/core/validation/stats.py)
   ma dwustopniowy bootstrap: lata i następnie zakłady. Cztery roczne klastry to mało
   dla oceny zmian reżimu. Resampling zakładów wewnątrz roku nie zachowuje korelacji
   wielu selekcji z jednego meczu. `mean(bootstrap_mean > 0)` nie jest bayesowskim
   prawdopodobieństwem przewagi ani uniwersalnym p-value. Reality-check jest określony
   jako SPA-style; niezależne resamplowanie zakładów między rynkami zachowuje mniej
   zależności niż wspólne resamplowanie wydarzeń. Wymaga osobnego testu kalibracji.

Wniosek audytora: warto zaczerpnąć rozdzielenie polityki i sygnałów, zachowywanie
wyników zerowych oraz wersjonowanie. **Nie przenosimy deklarowanego +9,3% jako dowodu**,
nie kopiujemy kodu ani bieżących filtrów. Następny audyt powinien odtworzyć pojedynczy
fold na legalnych danych z pełnym lineage, a potem przejść do przyszłych zdarzeń.

## Inne repozytoria

Zakres przeglądu poniżej: README, spis katalogu/licencja tam, gdzie dostępne.
Nie jest to pełny audyt implementacji ani wykonanie treningu.

| Projekt | Sport / dane / algorytmy | Wyniki, walidacja, liczebność | Kursy, koszty, niezależność, odtwarzalność | Licencja i decyzja |
|---|---|---|---|---|
| [penaltyblog](https://github.com/martineastwood/penaltyblog) | Futbol, modele Poissona/DC/bayesowskie, rankingi, devig; adaptery danych | Biblioteka, brak zweryfikowanego ROI/n/CLV w odczytanym README | Nie dowodzi OOS ani realnego zysku; użytkownik musi zbudować walidację i zabezpieczenia czasu; prawa do danych osobne | [LICENCE](https://github.com/martineastwood/penaltyblog/blob/master/LICENCE) odczytana, MIT; kandydat do późniejszego baseline DC |
| [NBA ML Sports Betting](https://github.com/kyleskom/NBA-Machine-Learning-Sports-Betting) | NBA moneyline/totals; statystyki NBA i odds SBR, SQLite; XGBoost/NN/logistic, EV/Kelly | Aktualny README nie podaje audytowalnego ROI, n ani CLV; opisuje trening/splity/kalibrację | Nie zweryfikowano ochrony time-as-of, podatków, CI, niezależnego holdoutu ani rzeczywistego rachunku; kompletność danych niepotwierdzona | Brak LICENSE w sprawdzonym katalogu głównym, 404 dla LICENSE; brak zgody na kopiowanie kodu; inspiracja do audytu, bez importu |
| [flumine](https://github.com/betcode-org/flumine) | Framework zdarzeniowy, Betfair/Betdaq i inne venues; symulacje historycznych streamów i paper | Narzędzie, nie strategia; ROI/n/CLV nie dotyczy frameworka | Model wykonania nie gwarantuje fill; wymaga danych o płynności/prowizji i prawa do streamów; brak dowodu rentowności | Odczytany [MIT LICENSE](https://github.com/betcode-org/flumine/blob/master/LICENSE); wzorzec symulatora. Nie instalujemy modułów rzeczywistych zleceń |

Nie znaleziono w tym przeglądzie projektu z niezależnie przez nas potwierdzonym,
wielosezonowym ROI netto i jednocześnie udokumentowaną wykonalnością kursów oraz CLV.
Nie jest to twierdzenie, że taki projekt nigdzie nie istnieje.

## Publikacje i badania

| Praca / status | Metoda i zgłoszony wynik | Ocena dowodu i użyteczność |
|---|---|---|
| [Kaunitz, Zhong, Kreiner 2017](https://arxiv.org/pdf/1710.02824), odczytany preprint | Futbol, konsensus kursów i wykrywanie odstających cen. Tabela 1: closing history 56 435 zakładów/3,5%; ciągła historia 6994/9,9%; paper 407/5,5%; rzeczywiste według autorów 265/8,5% | P-preprint+A+S oraz A+R. Nie nasza replikacja. Mała próba realna, ograniczenia kont, brak potwierdzenia kosztów PL. Porównanie z losowym wyborem nie jest testem wszystkich decyzji projektowych. Źródło hipotezy konsensusu, nie obietnica wyniku |
| [Clegg, Cartlidge, Not feeling the buzz, v4 2024](https://arxiv.org/html/2306.01740v4), odczytany preprint replikacyjny | WTA, Wikipedia buzz i kursy; reprodukcja wyników Ramirez/Reade/Singleton (IJF 2023), korekta błędnego kursu jednej selekcji usuwa dużą część zysku. Pozostała strategia nie zarabia dalej w rozszerzeniu 2020–2023 | I wykonana przez autorów korekty, nie przez nas; S. Trening 2016–18, pierwotny test 2019–II 2020. Różne n zależą od strategii, nie przypisujemy jednej liczby. Nie audytowano realnego rachunku ani PL podatków. Najważniejsza lekcja: outlier audit i nowy okres |
| [Dixon–Coles 1997, oryginalna praca](https://ajbuckeconbikesail.net/wkpapers/Airports/MVPoisson/soccer_betting.pdf), publikacja Applied Statistics | Zależny model wyników futbolu; historyczna analiza przewidująca dodatni zwrot | Klasyczny baseline, nie dowód przewagi w 2026. W tym audycie nie zweryfikowano tabel ROI/n ani pełnego kodu/danych; nie cytujemy liczby rentowności |
| [ForecastBench](https://arxiv.org/abs/2409.19839), v5 2025 | Dynamiczny benchmark przyszłych pytań, przewaga ekspertów nad najlepszym badanym LLM w opisanym badaniu | Nie jest dowodem ekonomicznym ze sportu; ROI/CLV nie dotyczy. Przydatny protokół prognoz przed rozstrzygnięciem; nie przenosimy wyników na Astrę |
| [WC2026-Agents, Ding i in. 2026](https://arxiv.org/abs/2607.17765), preprint | 104 mecze, 416 prognoz czterech modeli, wirtualne zakłady; autorzy raportują ROI od −18% do +10%, żaden model nie wygrywa Brier z rynkiem | A+P-preprint+S; nie replikowano. Mecze jednego turnieju są zależne. Wzmianka o ground truth uwzględniającym karne wymaga audytu zgodności z rynkiem 1X2/90 minut. Cutoff i nazwa „contamination-free” nie zastępują logów czasu |

Nie utożsamiamy preprintu z recenzowanym artykułem. Dostępność kodu/danych w publikacji
nie oznacza automatycznie prawa do ich dalszego przetwarzania: dla projektów paperowych
licencje danych i kodu wymagają osobnego sprawdzenia przed importem. Nie skopiowano ich.

## Co wykorzystać i co odrzucić

Wykorzystać: sprawdzone biblioteki statystyki i ramek danych, później penaltyblog
po testach zgodności; ogólny wzorzec stream replay; niezależny benchmark rynku;
prerejestrację i publikację zerowych wyników. Do przetestowania: sygnał konsensusu,
Elo/DC versus rynek, dopiero później cechy tekstowe i LLM.

Odrzucić jako dowód: dodatni backtest po wielu progach, accuracy bez kalibracji,
symulowane kursy −110, maksimum cen z wielu bukmacherów bez wspólnego czasu,
P(bootstrap ROI>0) jako pewność, CLV bez definicji zamknięcia, deklarację „live” bez
niezmiennego dziennika. Nie ma podstaw do wyboru dyscypliny na podstawie obcego ROI.

Ograniczenia dostępu: część witryn zwracała 403/429/timeout; `JeffSackmann/tennis_atp`
zwracało 404 zarówno przez GitHub connector, jak i web. StatsBomb przeniesiono do
`hudl/open-data`. Nie interpretujemy chwilowego 404 jako dowodu trwałego usunięcia.
