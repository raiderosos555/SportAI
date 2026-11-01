# Raport końcowy TASK 005 — 01.11.2026

**Wynik w jednym zdaniu:** przycisk **GENERUJ KUPONY DNIA** przechodzi teraz 14 etapów: od
oferty STS przez wyniki, statystyki PitchAPI, cechy as-of, modele Elo i Dixon–Coles z
kalibracją, aż po trzy profile kuponów. Każdy kupon:
- maksymalizuje **ostrożnie oszacowaną** szansę trafienia przy warunku realnego zysku po
  współczynniku 0,88;
- ma oznaczony poziom walidacji;
- zapisuje się jako kupon wirtualny i rozlicza automatycznie.

**Skuteczności kuponów jeszcze nie znamy** — żaden kupon kreatora nie został rozliczony
(sekcja E).

Zasady pracy (§28):
- bez commitów, push, PR i publikacji — zmiany są wyłącznie w plikach roboczych;
- kopie zapasowe: pełna `backups/2026-11-01_14-53-36/` oraz automatyczna
  `backups/pre-migration-v5_2026-11-01_14-01-05.db` przed migracją bazy v4 → v5;
- klucze PitchAPI i OpenScores podane przez użytkownika zapisano wyłącznie w `.env` (plik w
  `.gitignore`); aplikacja pokazuje tylko, czy klucz jest obecny;
- historycznych prognoz nie zmieniano; każdy eksperyment to nowa wersja modelu;
- bez scrapingu, bez płatnych zapytań, bez zawierania zakładów.

Rodzaje danych: **[H]** historyczne rzeczywiste, **[P]** prospektywne rzeczywiste (PDF STS),
**[S]** syntetyczne (wyłącznie testy). Wszystkie liczby skuteczności poniżej pochodzą z
**holdoutu historycznego [H]**, nie z rozliczonych kuponów.

## A. Źródła danych

Sprawdzone 01.11.2026 rzeczywistymi zapytaniami na kluczach użytkownika:

| Źródło | Dostęp i koszt | Zakres | Statystyki | Wynik testu | Decyzja |
|---|---|---|---|---|---|
| **PitchAPI v1** | darmowy klucz (`PITCHAPI_KEY`); brak stałego limitu, ochrona przed seriami (429 + Retry-After) | 77 lig, wszystkie z flagą `is_free`; top 5 lig od 2021/22, **Ekstraklasa** 2025/26–2026/27 | xG, strzały (celne), rzuty rożne, kartki, faule, posiadanie, duże okazje, podania, pojedynki — osobno dla meczu i połów; składy z flagą „potwierdzony”; endpointy zdarzeń i strzałów zwróciły puste dane | Ekstraklasa 2026/27: 112 zakończonych meczów do 31.10; pobrano 65 wyników (okno 60 dni) i 60 statystyk meczowych | **Wdrożone**: wyniki (priorytet 3), statystyki, składy na żądanie |
| **OpenScores** (MCP) | klucz użytkownika | 9 narzędzi: ligi, sezony, mecze, szczegóły meczu (składy, zdarzenia, statystyki), tabele, drużyny, zawodnicy | bogate | top 5 lig, ale **tylko sezon 2025/26** — brak bieżącego sezonu | Niewdrożone: brak aktualnych danych do prognoz; możliwe źródło historii statystyk |
| **TheSportsDB** | publiczny klucz testowy `123` | wiele dyscyplin | podstawowe | darmowy klucz zwraca **obcięte listy** | Niewdrożone |
| **Big Balls Sports Data** | wymaga klucza | — | — | klucz nie został podany | Niesprawdzone |
| **API-Sports** (TASK 004) | darmowy plan, 100 zapytań na dobę | piłka, koszykówka, hokej | — | darmowy plan obejmuje **tylko sezony 2022–2024** | Adapter gotowy, nieaktywny dla bieżącego sezonu |
| **football-data.org** (TASK 004) | darmowy klucz | 5 lig, bieżący sezon | brak | ostatni wynik: 31.10–01.11 | Wdrożone (priorytet 2) |
| Flashscore / Sofascore | — | — | — | regulaminy zabraniają pobierania automatycznego | **Nie używamy** (bez scrapingu) |

**Licencje:**
- PitchAPI nie publikuje regulaminu (SDK odsyła do „warunków PitchAPI”). Przyjęto użycie
  prywatne, niekomercyjne, bez redystrybucji.
- PitchAPI nie podaje czasu publikacji danych, więc dostępność to moment obserwacji przez
  aplikację (`observed_at_fetch`).
- football-data.org wymaga atrybucji (jest w aplikacji); openfootball to CC0.

**Koszty:** 0 zł. Nie wykonano żadnego płatnego zapytania.

**Dyscypliny:** modele obejmują wyłącznie piłkę nożną (5 lig). Ekstraklasa ma wyniki i
statystyki, ale za krótką historię dla modelu. Koszykówka i hokej nie mają legalnego,
bezpłatnego źródła bieżących wyników (API-Sports Free: 2022–2024).

## B. Sports Intelligence

**Nowe moduły:**

| Moduł | Rola |
|---|---|
| `datasources/pitchapi.py` | wyniki, statystyki i składy PitchAPI; parsery `parse_stats` i `parse_lineups`; limit 30 zapytań na minutę |
| `sports_intel.py` | wspólna baza statystyk, `features_as_of(event, decision_time)`, składy na żądanie |
| `models/goals.py` | Dixon–Coles: macierz wyników i wszystkie rynki z jednej macierzy |
| `probability_lab.py` | eksperyment walk-forward, kalibracja, walidacja predykcyjna, prognozy |
| `analysis/coupon_builder.py` | Intelligent Coupon Builder |
| `performance.py` | skuteczność kuponów i selekcji kreatora |
| `config/probability_policy.toml` | profile, progi walidacji predykcyjnej, wersja polityki |
| `config/team_identities.toml` | przejrzane zmiany nazw klubów |

**Baza danych — migracja v4 → v5** (automatyczna kopia przed migracją):
- `stat_observations` — każda obserwacja statystyk (rodzaj, encja, data meczu, źródło,
  `fetched_at` / `observed_at` / `available_at` z uzasadnieniem, jakość, wersja danych). Tabela
  jest tylko do dopisywania: wyzwalacze blokują UPDATE i DELETE;
- `feature_snapshots` — migawka cech przedmeczowych z czasem decyzji, zestawem cech i listą
  użytych obserwacji;
- `coupons` + `profile`, `policy_version`, `predicted_probability`, `proposal_json` — pełny
  zapis propozycji kreatora.

**Rekordy w bazie [H] (01.11.2026):**

| Liga | Archiwum (openfootball/CSV) | football-data.org | PitchAPI | Ostatni wynik |
|---|---|---|---|---|
| Premier League | 5370 | 48 | — | 31.10.2026 |
| Bundesliga | 4320 | 43 | — | 31.10.2026 |
| LaLiga | 5379 | 47 | — | 31.10.2026 |
| Serie A | 4980 | 54 | — | 01.11.2026 |
| Ligue 1 | 4281 | 40 | — | 31.10.2026 |
| Ekstraklasa | — | — | 65 | 31.10.2026 |

Statystyki meczowe: 60 obserwacji (Bundesliga 42, Premier League 18). Limit to 60 zapytań na
jedno uruchomienie, więc Ekstraklasa i pozostałe ligi uzupełnią się przy kolejnych
uruchomieniach.

**Dopasowanie do STS (oferta 02.11, kopia bazy):**
- 10 wydarzeń w ligach z danymi; 8 piłkarskich dopasowanych w pełni.
- 4 nazwy niedopasowane to drużyny NHL — brak historii hokeja.
- Wcześniej wykryty błąd tożsamości (np. „Real Sociedad” jako drużyna zamrożona w 2020 r.) usunięto:
  - pisownie z tym samym znormalizowanym kluczem to jeden klub;
  - zmiany nazw łączy ręcznie przejrzana lista (14 par w 4 ligach);
  - różne kluby (Paris FC / PSG, Chievo / Hellas, Gazélec / AC Ajaccio) nie są łączone.

**Cechy as-of (`features_as_of`):**
- forma z 5 i 10 meczów oraz u siebie / na wyjeździe;
- punkty na mecz, bramki, BTTS, powyżej 2,5;
- punkty względem oczekiwań Elo, czyli siła rywali;
- dni odpoczynku, mecze w 7 i 14 dniach;
- średnie xG, strzałów, rożnych i kartek z 5 meczów.

Reguła: wyłącznie dane z `available_at ≤ decision_time` i mecze sprzed dnia decyzji. Na
kopii bazy dla 02.11 zapisano 8 migawek.

**Brakujące dane:**
- kontuzje i zawieszenia — brak bezpłatnego zweryfikowanego źródła;
- dane zawodników — brak w modelach;
- składy — tylko na żądanie, około godziny przed meczem;
- inne sporty;
- historia Ekstraklasy — za krótka na model.

## C. Modele

**Algorytmy (wersje w `model_versions`; holdout nietknięty przy wyborze parametrów):**
1. **Elo (ordered draw), 1X2** — benchmark z TASK 003/004. Wersja `141c07acdd7a` (po scaleniu
   tożsamości).
2. **Dixon–Coles** — ważona regresja Poissona (IRLS) z parametrem ρ; okno 730 dni,
   ponowne dopasowanie co tydzień as-of. Okres półtrwania wybrany na **walidacji** spośród
   120/240/480 dni: 480. Wersja `a0572027fd84`.
3. **Kalibracja izotoniczna** dla każdej rodziny rynków, dopasowana na walidacji.
   „Ostrożnie” = dolna granica 90% przedziału Wilsona dla bloku kalibracji.
4. **Elo + forma** i **ensemble — nie zbudowano.** Cechy formy i statystyk są liczone i
   zapisywane, ale bez eksperymentu dowodzącego poprawy nie wchodzą do prognoz (TASK 006).

**Próbki:** walidacja 3561 meczów, holdout 3886 meczów, 5 lig.

**Porównanie z Elo (1X2, ten sam zbiór meczów):**

| Zbiór | Model | Log loss | Brier | ECE | Trafność |
|---|---|---|---|---|---|
| Walidacja | Elo | **0,98473** | 0,58708 | 0,0057 | 53,2% |
| Walidacja | Dixon–Coles | 0,98983 | 0,59045 | 0,0071 | 52,4% |
| Holdout | Elo | **0,98797** | 0,58910 | 0,0074 | 52,3% |
| Holdout | Dixon–Coles | 0,99371 | 0,59205 | 0,0088 | 52,2% |

Decyzja zapadła na walidacji: **1X2 i podwójną szansę liczy Elo**. Dixon–Coles liczy rynki
bramkowe. Holdout tylko potwierdza ten wybór.

**Walidacja predykcyjna (holdout)** — wszystkie warunki muszą być spełnione:
- n ≥ 300;
- ECE po kalibracji ≤ 0,03;
- log loss niższy niż częstość bazowa;
- żaden sezon z ECE > 0,06;
- żaden przedział z nadmierną pewnością (n ≥ 30).

| Rynek | Model | Log loss | Bazowy | Przewaga | ECE | Status |
|---|---|---|---|---|---|---|
| 1X2 | Elo | 0,57765 | 0,62389 | 0,046 | 0,0074 | ✅ zwalidowany |
| Podwójna szansa | Elo | 0,57765 | 0,62389 | 0,046 | 0,0074 | ✅ zwalidowany |
| Powyżej/poniżej 1,5 | Dixon–Coles | 0,53241 | 0,53319 | 0,0008 (minimalna) | 0,0096 | ✅ zwalidowany |
| Powyżej/poniżej 3,5 | Dixon–Coles | 0,60926 | 0,61944 | 0,010 | 0,0080 | ✅ zwalidowany |
| BTTS | Dixon–Coles | 0,68731 | 0,69011 | 0,0028 (minimalna) | 0,0084 | ✅ zwalidowany |
| Powyżej/poniżej 2,5 | Dixon–Coles | 0,68090 | 0,68799 | 0,0071 | 0,0177 | 🧪 eksperymentalny — nadmierna pewność w 60–70% |
| Podwójna szansa z macierzy bramek | Dixon–Coles | — | — | — | 0,0126 | 🧪 eksperymentalny — nadmierna pewność w 90–100% |
| Sumy drużyn, gol drużyny, linie 0,5 i 4,5 | Dixon–Coles | — | — | — | — | bez kalibracji — nie wchodzą do kuponów |

Log loss w tej tabeli to błąd binarny każdej selekcji (jeden typ przeciw reszcie). Dla 1X2
to 0,57765, a log loss 3-klasowy (0,98797) jest w tabeli porównania. Podwójna szansa Elo ma
identyczne miary, bo jej selekcje są dopełnieniami selekcji 1X2 (1X = nie 2), a log loss,
Brier i ECE dopełnienia są takie same; różnią się przedziały kalibracji.

**Kalibracja Elo — podwójna szansa (holdout):**

| Przedział | Prognozy | Średnia prognoza | Trafione | 95% |
|---|---|---|---|---|
| 70–80% | 4801 | 74,0% | 73,5% | 72,2–74,7% |
| 80–90% | 1823 | 84,3% | 83,3% | 81,5–85,0% |
| 90–100% | 361 | 92,4% | 93,9% | 91,0–95,9% |

Ulubieńcy 1X2 w przedziale 70–80% wygrali 75,1% meczów (n = 353).

**Ograniczenia:**
- modele widzą tylko wyniki — bez składów, kontuzji i motywacji;
- O/U 1,5 i BTTS mają minimalną przewagę nad częstością bazową;
- kalibracja izotoniczna jest schodkowa, więc wiele selekcji dostaje tę samą wartość (np.
  0,794);
- status predykcyjny **nie oznacza przewagi nad STS** — modele nadal mają ekonomiczny status
  `experimental`.

## D. Generator kuponów

**Algorytm** (`analysis/coupon_builder.py`):
1. **Selekcje dopuszczone:** jest prognoza z ostrożnym oszacowaniem; mecz przed startem;
   kurs świeży i obecny w najnowszej ofercie; poprawny odczyt; zweryfikowane reguły
   rozliczenia; wygrana pokrywa stawkę po 0,88; kurs ≤ 4,0.
2. **Pula:** 25 najlepszych selekcji według ostrożnej szansy, do 3 alternatyw z jednego meczu.
   Poprawka polityki p2: w wersji p1 brana była tylko najlepsza selekcja meczu, co ukrywało
   lepsze kombinacje.
3. **Wybór:** pełne przeszukanie kombinacji do limitu nóg profilu. Zwycięża najwyższy iloczyn
   ostrożnych szans; przy remisie mniej nóg, potem wyższa szansa modelu. Warunki to kurs
   łączny i zysk netto z `settle()`: współczynnik 0,88 i podatek od wygranej.
4. **Zapis:** kupon wirtualny z profilem, wersją polityki, prawdopodobieństwem i pełnym
   uzasadnieniem. Klucz jest idempotentny, więc ponowne kliknięcie nie dubluje kuponu.

**Profile** (`config/probability_policy.toml`, wersja `2026-11-01.p2`):

| Profil | Nóg | Min. szansa nogi (ostrożnie) | Min. kurs łączny | Min. zysk netto | Wymaga walidacji predykcyjnej |
|---|---|---|---|---|---|
| Bezpieczny | ≤ 2 | 75% | 1,25 | 8% stawki | tak |
| Zrównoważony | ≤ 3 | 65% | 1,60 | 35% stawki | tak |
| Agresywny | ≤ 4 | 55% | 2,50 | 100% stawki | nie (wtedy poziom „eksperymentalny — symulacja”) |

**Prawdopodobieństwo kuponu:** iloczyn szans selekcji z **różnych** meczów przy założeniu
niezależności. Podajemy dwie wartości: skalibrowaną (model) i ostrożną (iloczyn dolnych
granic).

**Zależności:**
- dwóch rynków jednego meczu nigdy nie mnożymy, bo bez modelu łącznego taki kupon jest
  odrzucany;
- korelacja między różnymi meczami (np. ta sama kolejka) nie jest modelowana — kupon ma o tym
  ostrzeżenie.

**Liczba wygenerowanych kuponów:**
- prawdziwa baza: 0, bo przycisk nie był jeszcze użyty na danych użytkownika;
- kopia bazy, oferta 02.11 (pula 15 selekcji z 5 meczów): **3 kupony**, średnio 2,0 selekcji,
  średni kurs 1,86.

**Przykłady [P]** — dane dostępne przed meczami: PDF STS z 01.11 12:35, wyniki do 01.11,
prognozy z 01.11. Stawka 10 zł:

| Profil | Selekcje (kurs, P model / ostrożnie) | Kurs | P kuponu model / ostrożnie | Wypłata | Zysk netto | EV netto (diagnostyka) | Poziom |
|---|---|---|---|---|---|---|---|
| Bezpieczny | Lorient – Brest, powyżej 1,5 gola (1,36; 79% / 76%) | 1,36 | 79% / 76% | 11,97 zł | 1,97 zł | −0,049 | ✅ zwalidowany predykcyjnie |
| Zrównoważony | Atalanta – Parma, 1X (1,16; 86% / 82%) + Deportivo – Osasuna, powyżej 1,5 (1,40; 74% / 71%) | 1,624 | 64% / 58% | 14,29 zł | 4,29 zł | −0,084 | ✅ zwalidowany predykcyjnie |
| Agresywny | Lorient – Brest, powyżej 1,5 (1,36) + Atalanta – Parma, poniżej 3,5 (1,52; 78% / 76%) + Deportivo – Osasuna, poniżej 3,5 (1,25; 78% / 76%) | 2,584 | 49% / 43% | 22,74 zł | 12,74 zł | +0,106 | ✅ zwalidowany predykcyjnie |

Uwagi do przykładów:
- **Ujemne EV** kuponów bezpiecznego i zrównoważonego jest pokazane wprost: wysoka szansa
  nie oznacza zysku.
- **Dodatnie EV** agresywnego to wynik modelu bez potwierdzonej przewagi nad STS, a nie
  obietnica.
- **Wiek oferty:** po południu 01.11 ten sam dokument STS miał 4,8 h. Kreator odrzucił wtedy
  wszystkie profile i pokazał komunikat, by pobrać aktualną ofertę — kursy starsze niż 3 h
  nie wchodzą do kuponów.

**Widok „Kupon dnia”:**
- nagłówek SPORTS EDGE AI — NAJLEPSZE KUPONY DNIA i przycisk GENERUJ KUPONY DNIA;
- 3 karty profili. Każda karta pokazuje:
  - szanse kuponu (model i ostrożnie), kurs, wypłatę i zysk netto;
  - selekcje z godziną, ligą, numerem zakładu STS i wiekiem kursu;
  - status walidacji;
  - sekcję „DLACZEGO WYBRALIŚMY TEN KUPON?”;
- poprzednia analiza EV jest w panelu diagnostycznym.

**Centrum analizy meczu** — 7 paneli:
1. mecz i kursy STS;
2. forma i terminarz;
3. statystyki;
4. składy i kontuzje;
5. prawdopodobieństwa AI ze statusem i kalibracją;
6. ryzyko — czy selekcja może wejść do kuponu;
7. wyjaśnienie.

## E. Skuteczność

**Rozliczone kupony kreatora: 0.** Nie podajemy więc trafności kuponów, ROI ani wyników
profili. Wszystkie te miary liczy już zakładka **Historia i wyniki → Skuteczność kreatora
kuponów**:
- trafność z przedziałem Wilsona;
- średnia przewidywana szansa wobec trafności;
- ROI po kosztach, maksymalne obsunięcie i serie;
- kalibracja selekcji według sportu, ligi, rynku i przedziału.

**Trafność pojedynczych typów (holdout [H]):**
- tabela kalibracji w sekcji C, np. podwójna szansa Elo 80–90%: 83,3% trafionych
  (n = 1823);
- powyżej 1,5 gola, przedział 70–80%: 75,8% (n = 1617).

**Niepewność statystyczna** wyników, które dopiero się pojawią:
- przy prawdziwej trafności 77% i 30 rozliczonych kuponach przedział 95% to mniej więcej
  59–88%;
- przy 100 kuponach mniej więcej 68–84%.

Ocena, czy kupony trafiają tak, jak przewiduje model, wymaga **co najmniej 100 kuponów na
profil** — kilku tygodni codziennego generowania (TASK 006).

## F. Problemy

**Brakujące funkcje:**
- model Elo + forma i ensemble;
- wykorzystanie statystyk w modelach;
- kontuzje i dane zawodników;
- model łączny dla rynków jednego meczu;
- inne sporty;
- model dla Ekstraklasy;
- zasada zaokrąglania kursu AKO przez STS jest nieopisana — liczymy dokładny iloczyn.

**Problemy źródeł:**
- PitchAPI: brak opublikowanego regulaminu i znaczników publikacji;
- OpenScores: tylko sezon 2025/26;
- API-Sports: plan darmowy tylko 2022–2024;
- TheSportsDB: obcięte listy;
- Big Balls: brak klucza;
- plik STS „na dziś” bywa pusty (TASK 004).

**Licencje:** PitchAPI tylko do użytku prywatnego (dane nie trafiają do repozytorium ani
eksportów publicznych).

**Ryzyka data leakage:**
- Statystyki PitchAPI mają `available_at` równe momentowi obserwacji. To bezpieczne
  prospektywnie, ale backtest z cechami statystyk wymaga osobnego, jawnego założenia o
  dostępności — inaczej historyczne cechy byłyby puste. Test pokazuje, że minutę przed
  obserwacją statystyki są niewidoczne.
- Mecze z dnia decyzji są wykluczone z cech.
- Kalibracja i wybór okresu półtrwania odbywają się na walidacji; holdout tylko raportuje.

**Ryzyka nadmiernej pewności:**
- O/U 2,5 (60–70%) i podwójna szansa z Dixona–Colesa (90–100%) są wyłączone z profili
  wymagających walidacji.
- Wybór maksimum z wielu kandydatów zawyża szanse najlepszych kuponów (efekt „winner's
  curse”). Granica ostrożna łagodzi efekt, ale go nie usuwa — to musi zmierzyć badanie
  prospektywne.
- Założenie niezależności meczów.

**Naprawione w TASK 005:**
- **A:** kolizja identyfikatorów API-Sports — każdy dostawca ma unikalny `id`, a hierarchia
  używa rodziny źródła;
- **B:** benchmark liczył ten sam mecz wielokrotnie, po razie na operatora — teraz jeden
  konsensus na mecz;
- **C:** po błędzie źródła następuje przejście do kolejnego legalnego dostawcy;
- **D:** nieaktualne kursy są oddzielone od rankingu;
- błąd tożsamości drużyn.

Błędy znalezione przy wdrożeniu i naprawione:
- prognozy Elo nie niosły statusu walidacji predykcyjnej, więc 1X2 i podwójna szansa trafiały
  do kreatora jako eksperymentalne;
- pula kreatora miała tylko jedną selekcję na mecz;
- zdublowane identyfikatory statystyk w migawce cech;
- wybór meczu dla składów brał pierwszy, a nie najbliższy termin.

## G. Testy

**pytest:** 196 testów zaliczonych (było 171). Nowy plik `tests/test_task005.py` (25 testów)
obejmuje:
- profile kreatora, kombinacje SOLO/AKO, odmowy, kontrolę zysku netto po 0,88;
- zakaz łączenia rynków jednego meczu i wykluczenia (kurs > 4, stary kurs, mecz w trakcie);
- kalibrację, walidację predykcyjną, Dixon–Coles (bez danych z dnia dopasowania);
- tożsamości drużyn, wybór modelu 1X2 i status pochodnej podwójnej szansy;
- poprawki A–D;
- parsery i dostawcę PitchAPI na sztucznym serwerze;
- statystyki zapisywane raz i cechy bez przyszłości;
- składy na żądanie oraz serie i kalibrację selekcji.

**Test integracyjny [S]** przechodzi cały łańcuch: PDF STS → wyniki → statystyki → cechy →
Elo i Dixon–Coles → prawdopodobieństwa → ranking → kreator → zapis (idempotentny) → wynik →
automatyczne rozliczenie → raport skuteczności.

**Ruff:** `ruff check` i `ruff format --check` bez uwag.

**Aplikacja:**
- wszystkie 7 stron renderuje się bez błędów na kopii prawdziwej bazy w trybie offline
  (Streamlit AppTest);
- widok „Kupon dnia” sprawdzono wizualnie w przeglądarce na kopii bazy.

**Windows 11, Python 3.12, `start.bat`:** bez zmian w uruchamianiu.

## H. TASK 006

1. **Trafność modeli:**
   - eksperyment Elo + forma i Dixon–Coles + xG z jawną regułą dostępności statystyk;
   - historia statystyk PitchAPI od 2021/22, pobierana partiami w limicie;
   - ensemble tylko przy poprawie na walidacji.
2. **Rynki:**
   - rzuty rożne i kartki z danych PitchAPI;
   - rynki 1. połowy;
   - Ekstraklasa — model po zebraniu sezonów 2025/26–2026/27.
3. **Kalibracja:** kalibracja per liga; cykliczna rekalibracja na ruchomej walidacji;
   monitorowanie dryfu.
4. **Optymalizacja kuponów:**
   - model łączny rynków jednego meczu z macierzy wyników Dixona–Colesa, np. 1X + poniżej
     3,5 z dokładnym prawdopodobieństwem łącznym;
   - kontrola korelacji w ramach kolejki;
   - korekta efektu „winner's curse”.
5. **Badanie prospektywne:**
   - protokół zamrożony przed startem;
   - codzienne kupony trzech profili przez 8–12 tygodni;
   - rozliczenia automatyczne;
   - porównanie przewidywanej szansy z trafnością (Wilson);
   - ROI netto i decyzja o progach polityki na podstawie danych z nowych, niezależnych
     wydarzeń.
