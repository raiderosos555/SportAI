# Raport końcowy TASK 006 — 01.11.2026

**Wynik w jednym zdaniu:** Sports Edge AI ma nowy, spójny interfejs w 7 sekcjach i prostym
języku. Pokrycie wzrosło z 5 do 12 lig piłkarskich — w ofercie STS na 02–07.11 prognozę ma
**79 meczów zamiast 22** (×3,6). Kupon dnia działa dla zakresów dziś / jutro / 3 / 7 dni i
zawsze wyjaśnia, dlaczego kupon nie powstał.

Skuteczności kuponów nadal nie znamy: w bazie użytkownika nie ma jeszcze żadnego rozliczonego
kuponu kreatora (sekcja F).

Zasady pracy (§31):
- **Bez commitów, push, PR i publikacji** — zmiany są tylko w plikach roboczych.
- **Kopia zapasowa przed zmianami:** `backups/2026-11-01_18-22-42/` (baza, konfiguracja,
  ustawienia, manifest SHA-256). Schemat bazy się nie zmienił (v5), więc migracji nie było.
- **Dane:** dodano 12 149 wyników z bezpłatnych, legalnych źródeł (40 zapytań: 37 PitchAPI, 3
  API-Sports). Nic nie usunięto; historyczne prognozy i wersje modeli są nienaruszone.
- **Prywatność:** klucze API pozostają wyłącznie w `.env`. Zrzuty ekranów są lokalnie w
  `data/screenshots/task006/` (katalog poza repozytorium).

Oznaczenia danych: **[H]** historyczne rzeczywiste, **[P]** prospektywne rzeczywiste (PDF STS),
**[S]** syntetyczne (tylko testy).

## A. Redesign

**Nowy układ — 7 sekcji:**

| Sekcja | Zawartość |
|---|---|
| Dashboard | kluczowe liczby dnia, najlepsze prognozy (karty), podgląd kuponów, najbliższe mecze, ostatnie kupony, status danych i ograniczenia |
| Kupon dnia | zakres Dziś / Jutro / 3 dni / 7 dni, przycisk „ANALIZUJ MECZE I GENERUJ KUPONY”, 3 profile osobno dla każdego dnia, powody braku kuponu, „Najlepsze typy do rozważenia” (A/B/C, filtry), szczegóły techniczne |
| Analiza meczów | podsumowanie, ocena modelu, najbardziej prawdopodobny typ, „Dlaczego model tak ocenia ten mecz?”, tryb przedmeczowy lub pomeczowy, 6 zakładek szczegółów |
| Wydarzenia i wyniki | terminarz, zakończone mecze z wynikami, pełna oferta STS z historią kursów |
| Historia i skuteczność | podsumowanie, ostatnie kupony jako karty, wykres wyniku w czasie, dotychczasowe zakładki szczegółowe |
| Modele i statystyki | widok podstawowy (prostym językiem), pokrycie oferty, widok zaawansowany (metryki, bramki lig, wersje, dotychczasowe badania) |
| Źródła i ustawienia | panel źródeł, pliki STS, ustawienia, podatki i reguły, dane lokalne, diagnostyka |

**Pasek boczny:** nazwa aplikacji, wiek oferty STS, aktualność wyników lig, aktywne zakresy
modeli, czas ostatniej analizy, tryb online/offline, przełącznik DEMO i wymagana atrybucja
football-data.org.

**Zmiany graficzne** (motyw w `.streamlit/config.toml` i tokeny w `app/ui.py`):
- **Kolory:** tło `#0B1120`, karty `#111C30` / `#17243B`, kolor główny `#22C55E`, informacja
  `#38BDF8`, tekst `#F8FAFC` / `#94A3B8`, ostrzeżenie `#F59E0B`, błąd `#EF4444`.
- **Kolor statusu zawsze coś znaczy:** zielony — potwierdzone lub trafione, czerwony — błąd lub
  przegrana, bursztynowy — ostrzeżenie, niebieski — informacja.
- **Bez emoji:** w nawigacji spójne ikony Material.
- **Wygląd:** wspólna typografia (Segoe UI / Inter), zaokrąglone karty.
- **Liczby:** procenty po polsku („71,2%”), kursy z przecinkiem, „Brak danych” zamiast `None`.

**Nowe komponenty** (`app/ui.py`):
- nagłówki: AppHeader (`app_header`), SectionHeader (`section`);
- liczby i statusy: MetricCard (`metric_cards`), StatusIndicator (`pill`), RiskIndicator
  (`risk_pill`), DataFreshnessIndicator (`freshness_pill`), ProbabilityBar
  (`probability_bars`), paski porównawcze (`comparison_rows`);
- karty: MatchCard / PredictionCard (`match_card`), selekcje kuponu (`legs_html`);
- stany i wyjaśnienia: ExplanationPanel (`explanation`), EmptyState / ErrorState
  (`empty_state`, `error_state`);
- pasek boczny: `brand`, `side_rows`.

Cały tekst trafiający do HTML jest escapowany, a `unsafe_allow_html` służy wyłącznie do
układu. Kolory widżetów Streamlit pochodzą z motywu, nie z CSS.

**Responsywność:**
- siatki kart układają się automatycznie, a rzędy kart kuponu zawijają się (minimum 290 px);
- na telefonie metryki idą po dwie w rzędzie, a menu boczne jest zwijane;
- nazwy drużyn zawijają się zamiast ucinania;
- sprawdzone przy 1440 px, ok. 560 px (laptop z panelem) i 375 px (telefon).

**Testy wizualne:** 8 zrzutów w `data/screenshots/task006/`:
- dashboard;
- Kupon dnia na szerokim ekranie, wąskim i telefonie;
- analiza pomeczowa Getafe – Sevilla;
- modele;
- pokrycie;
- dashboard na telefonie.

Zrzuty zrobiono na kopii bazy w trybie offline z limitem świeżości podniesionym do 24 h
(konfiguracja testowa, opis w sekcji B).

**Wydajność:**
- **Analiza jednego dnia:** **1,96 s → 0,13 s**. Prognozy są filtrowane w SQL (aktywne wersje i
  dni oferty), a JSON walidacji jest parsowany raz na wersję, nie raz na prognozę (11 619 →
  kilka parsowań).
- **Cache:** widoki są przechowywane per minuta i „odcisk” bazy (`common.data_version`), więc
  przełączenie strony nie liczy analizy od nowa.
- **Strony z ciężkimi danymi:** pokazują tylko wybrany widok — Streamlit wykonuje wszystkie
  zakładki naraz. Strona modeli zeszła z **28,9 s do 0,3 s**.
- **Pierwsze renderowanie** każdej strony na kopii prawdziwej bazy: 0,3–2,0 s.

## B. Kupon dnia

**Jak działa generator:**
- Kroki wspólne dla zakresu wykonują się raz: oferta STS → wyniki → statystyki → dopasowanie →
  cechy → prognozy → kalibracja.
- Potem **każdy dzień osobno**: ranking → kreator → prawdopodobieństwa → wypłaty → ryzyko →
  zapis → prezentacja.
- Kupon nigdy nie łączy dni (`services.run_analysis`).
- Użytkownik widzi 5 zrozumiałych faz zamiast 14 etapów: pobieranie danych, analizowanie
  wydarzeń, obliczanie prawdopodobieństw, wybór najlepszych typów, przygotowywanie kuponów.
- Pełny log jest w panelu „Szczegóły techniczne”.

**Kreator** maksymalizuje ostrożną szansę całego kuponu przy limitach profilu i wypłacie netto
po współczynniku 0,88. Nowości:
- do 2 **alternatyw** na profil, bez wspólnych meczów z lepszą propozycją;
- **kody odmowy** (`no_candidates`, `not_validated`, `low_probability`, `payout`).

**Zależność między meczami:** dwóch rynków jednego meczu nadal nie łączymy. Niezależność
różnych meczów sprawdzono teraz na holdoucie [H] (faworyci Elo z szansą ≥ 55%, pary meczów
jednego dnia):

| Zakres | Pary | Trafione razem | Iloczyn osobnych trafności | Stosunek | Iloczyn prognoz |
|---|---|---|---|---|---|
| 5 lig — ta sama liga i dzień | 903 | 38,5% (95%: 35,4–41,8%) | 38,4% | 1,004 | 43,5% |
| 5 lig — różne ligi | 3816 | 41,4% | 41,3% | 1,003 | 44,1% |
| 7 nowych lig — ta sama liga i dzień | 1016 | 39,7% | 39,2% | 1,011 | 44,1% |
| 7 nowych lig — różne ligi | 5235 | 38,1% | 38,7% | 0,985 | 44,8% |

- **Wniosek 1:** zależności nie widać (stosunek ≈ 1,00), więc założenie niezależności ma
  pokrycie w danych.
- **Wniosek 2:** surowe Elo jest zbyt pewne przy faworytach — 38,5% zamiast prognozowanych
  43,5%. To problem kalibracji pojedynczych prognoz, nie zależności (zadanie dla TASK 007).

Test jest w `probability_lab.pair_dependence`.

**Zmiany UX:**
- 3 karty profili (Bezpieczny, Zrównoważony, Agresywny), każda z opisem ryzyka;
- typy opisane po ludzku („Powyżej 1,5 gola”, „Atalanta lub remis (1X)”);
- przy każdym typie: kurs, numer zakładu STS, szansa i jej wersja ostrożna, status;
- kurs łączny, stawka, wypłata i zysk netto;
- sekcja „Dlaczego ten kupon?”;
- propozycja eksperymentalna oznaczona jako **SYMULACJA**;
- przy meczach za 2 i więcej dni ostrzeżenie o wyższej niepewności.

**Powody braku kuponu** (`insights.explain_no_coupon`) — zamiast samego „BRAK KUPONU”:

| Przypadek | Komunikat (przykład) | Działanie |
|---|---|---|
| A — stare kursy | „Analiza meczów zakończona, ale kursy STS wymagają odświeżenia. Najnowszy dokument ma 19,8 h, a limit wynosi 3 h.” | przycisk ponownego sprawdzenia oficjalnej oferty, import ręczny |
| B — brak modeli | „Znaleziono 120 wydarzeń, ale modele potrafią obecnie analizować tylko 15…” | raport pokrycia |
| C — niska szansa | „Przeanalizowano N kandydatów. Żaden nie osiągnął minimalnej ostrożnej szansy…” | lista typów do rozważenia |
| D — wypłata | „Dostępne typy mają wysokie prawdopodobieństwo, ale nie pozwalają uzyskać wymaganej wypłaty netto…” | — |
| E — niezwalidowany model | „Znaleziono K potencjalnych selekcji, ale modele ich rynków nie przeszły jeszcze walidacji…” | ranking eksperymentalny |

Przy każdym przypadku jest liczba typów, których dotyczy, oraz liczba typów odrzuconych z innych
powodów i lista tych powodów. Zakończenie analizy bez kuponu nie jest pokazywane jako sukces.

**Status świeżości STS — diagnoza problemu 19,8 h [P]:**
- STS generuje plik „na dziś” często (01.11: 06:45, 11:35, 12:05, 16:55 UTC), ale **w każdym z
  czterech pobrań był pusty** (0 wydarzeń; prawdopodobnie święto).
- Najnowsze kursy na 01.11 pochodziły więc z dokumentu „na jutro” z 31.10 22:15 (czas
  polski), czyli sprzed 19,8 h. Aplikacja pisała wtedy tylko „pobrano plik”.
- **Teraz** każda próba ma jawny status:

  | Status | Znaczenie |
  |---|---|
  | nie wykonano | tryb offline lub pobieranie wyłączone |
  | zablokowane | limit 10 minut, błąd serwera, brak połączenia |
  | pusty plik | serwer zwrócił PDF bez wydarzeń |
  | ten sam dokument | identyczny PDF — kursy *nie* zostają potwierdzone na nowo |
  | bez wydarzeń tych dni | nowy dokument nie obejmuje analizowanych dni |
  | nowy dokument | pobrano nową ofertę |

- Pusty plik lub plik bez danego dnia powoduje sprawdzenie pozostałych oficjalnych wariantów
  (na jutro, kolejne dni).
- Świeżość liczona jest wyłącznie od czasu wygenerowania dokumentu; czas pobrania nigdy nie
  staje się czasem obserwacji starego kursu.
- Stare kursy mogą trafić tylko do grupy B („kurs historyczny, niepotwierdzony”), nigdy do
  aktywnego kuponu.

**Przykładowe propozycje** — test wizualny na kopii bazy (offline), utworzone 01.11 19:02:
- **Źródło kursów:** PDF STS „oferta na jutro” wygenerowany 01.11 12:35 (czas polski) [P].
- **Prognozy:** utworzone 01.11 ok. 18:40 z wyników dostępnych do 01.11.
- **Modele:** Dixon–Coles, wersje `a0572027fd84` (5 lig) i `d3941731c98d` (7 nowych lig).
- **Uwaga:** w tej kopii limit świeżości kursów podniesiono do 24 h tylko na potrzeby testu.
  W aplikacji z limitem 3 h te kursy są już niepotwierdzone, a kupon na 02.11 powstanie dopiero
  po pobraniu aktualnej oferty.

| Profil | Selekcje (nr STS) | Kurs | Szansa model / ostrożnie | Wypłata / zysk netto (10 zł) | EV (diagnostyka) |
|---|---|---|---|---|---|
| Bezpieczny | Lorient – Brest, powyżej 1,5 gola @1,36 (65305) | 1,36 | 79,4% / 75,7% | 11,97 / 1,97 zł | −0,049 |
| Zrównoważony | Radomiak – Śląsk, powyżej 1,5 gola @1,18 (60198) + Lorient – Brest, powyżej 1,5 @1,36 | 1,60 | 65,9% / 60,7% | 14,12 / 4,12 zł | −0,069 |
| Agresywny | Radomiak – Śląsk, powyżej 1,5 @1,18 + Atalanta – Parma, poniżej 3,5 @1,52 (45892) + Zagłębie – Korona, 1X @1,47 (2189) | 2,64 | 47,5% / 43,3% | 23,20 / 13,20 zł | +0,102 |

Dwie z trzech propozycji korzystają już z meczów Ekstraklasy, ligi dodanej w TASK 006.

## C. Analiza meczu

**Nowa prezentacja:**
- **Karta wydarzenia:** drużyny, data i godzina (Europe/Warsaw), liga oraz statusy: przed
  rozpoczęciem / trwa / zakończony, prognoza, potwierdzenie oferty, tryb.
- **„Ocena modelu”:** faworyt i charakter meczu („mecz wyrównany”, „lekka przewaga: X”,
  „wyraźny faworyt: X”), poziome paski 1X2 i nazwa modelu prostym językiem.
- **„Najbardziej prawdopodobny obsługiwany typ”:** szansa, wersja ostrożna, kurs, wiarygodność i
  informacja, czy typ kwalifikuje się do kuponu.
- Gdy żaden typ nie ma ostrożnej szansy co najmniej 55% przy kursie zwracającym stawkę,
  aplikacja pisze: „Brak wystarczająco wiarygodnego typu dla tego meczu”.

**Uzasadnienia prostym językiem** — dwa panele:
- **„Dane wykorzystane przez model”:** np. dla Elo siła drużyn z wyników, z liczbą meczów i
  przewagą boiska; dla Dixon–Coles średnie gole z 2 lat; do tego kalibracja i źródła wyników.
- **„Dodatkowe informacje o meczu (poza modelem)”:** forma, mecze w danej roli, odpoczynek,
  gęstość terminarza.

Jawnie zaznaczono, że składy, kontuzje i statystyki meczowe nie wpływają na prognozę.

**Oddzielenie danych przed- i pomeczowych:**
- **Tryb przedmeczowy:** forma i statystyki liczone na chwilę prognozy; w tabeli formy tylko
  mecze znane przed prognozą.
- **Tryb po meczu:** oryginalna prognoza sprzed rozpoczęcia, z dowolnej wersji modelu (wersja
  serwowana wtedy może już nie być aktywna), wynik i wynik do przerwy, trafność faworyta,
  wynik każdego prognozowanego typu oraz lista tego, czego model nie wiedział.
- **Przykład — Getafe – Sevilla 01.11 [H/P]:** prognoza z 01.11 12:37 wskazywała gospodarzy
  39,9% (remis 28,8%, goście 31,3%); wynik 3:1 (do przerwy 0:1) — trafiona.
- **Składy:** zaobserwowane po rozpoczęciu są oznaczone jako informacja pomeczowa.
- Wynik nigdy nie zmienia prognozy.

**Perspektywa wyniku (błąd z TASK 005):** kolumna „Wynik” pokazywała „2:1” w kolejności
gospodarze–goście, a W/R/P z perspektywy drużyny — mecz wyjazdowy wyglądał na sprzeczny.
Teraz są dwie jednoznaczne kolumny: „Mecz” („Real Madrid CF 1:3 FC Barcelona”) i „Wynik
drużyny” („3:1” dla Barcelony) z W/R/P zgodnym z tą perspektywą.

**Statystyki:**
- prezentowane porównawczo (gospodarze / goście) z paskiem udziału;
- przy każdej wartości wielkość próby („dane z 1 / 4 meczów”);
- zakresy: forma z 5 i z 10 meczów, mecze w danej roli (dom / wyjazd);
- brak danych pokazany jako „Brak danych”, nie zero;
- ostrzeżenie, że średnia z 1–2 meczów nie jest reprezentatywna.

## D. Pokrycie

**Audyt — dlaczego tylko 15 prognoz (oferta 01.11) [P]:**

Lejek dla 01.11 (stan na 07:00 UTC):

| Krok | Wydarzenia |
|---|---|
| Wydarzenia w ofercie | 764 |
| Przed rozpoczęciem | 654 |
| Piłka nożna | 413 |
| Liga z legalnym źródłem wyników | 10 |

Główne powody braku prognozy:
- 403 mecze piłkarskie z lig bez legalnego, bezpłatnego źródła wyników;
- 241 wydarzeń z dyscyplin bez modelu;
- 110 wydarzeń już rozpoczętych.

Modele obejmowały wtedy wyłącznie 5 czołowych lig, a prognozy dla 01.11 powstały dopiero później
(użytkownik widział 15).

**Tydzień 01–07.11:**
- 1205 wydarzeń: piłka 724, koszykówka 180, tenis 95, hokej 89, futbol amerykański 36,
  siatkówka 26 i inne.
- Najczęstsze ligi piłkarskie poza modelem: Segunda Federación 36, **Championship 24**, LaLiga 2
  13, Serie B 12, Primera Federación 11, Allsvenskan 11, **Süper Lig 10**, Ukraina 10, **Pro
  League 10**, **Liga Portugal 10**, Brasileirão 10, **2. Bundesliga 9**, **Ekstraklasa 9**,
  **Eredivisie 8**.

**Rozszerzenie zrealizowane** — 7 lig wybranych według liczby meczów w ofercie STS i dostępności
legalnej historii:

| Liga | Źródło historii | Sezony | Wyniki |
|---|---|---|---|
| Championship | PitchAPI | 2021/22–2026/27 | 2939 |
| Eredivisie | PitchAPI | 2021/22–2026/27 | 1649 |
| Liga Portugal | PitchAPI | 2021/22–2026/27 | 1573 |
| Pro League (Belgia) | PitchAPI | 2021/22–2026/27 | 1679 |
| 2. Bundesliga | PitchAPI | 2021/22–2026/27 | 1611 |
| Süper Lig | PitchAPI | 2022/23–2026/27 | 1427 |
| Ekstraklasa | API-Sports Free (2022/23–2024/25) + PitchAPI (2025/26–2026/27) | 4,3 sezonu | 918 + 418 |

Szczegóły rozszerzenia:
- **Niedopasowane nazwy:** 0. Naprawiono przy tym błąd silnika wyników: przyjmował nazwy klubów
  tylko z pierwszego sezonu, więc kluby, które awansowały później, odpadały (w próbie 1766
  odrzuconych rekordów). Teraz źródło definiujące nazwy w lidze może wprowadzać nowe kluby, a
  inne źródło musi się dopasować lub mieć przejrzany alias.
- **Aliasy:** 21 przejrzanych aliasów STS (np. „Benfica Lizbona” = Benfica) i 2 aliasy
  API-Sports dla Ekstraklasy (Cracovia, Jagiellonia).
- **Pliki źródłowe:** naprawiono nadpisywanie surowych plików — kilka sezonów w tej samej
  sekundzie trafiało do jednego pliku.
- **Ekstraklasa:** wcześniej była za krótka do modelu. Progów minimalnej historii nie zmieniano —
  dodano starsze, legalnie dostępne sezony.

**Faktyczne zwiększenie** — oferta 02–07.11, stan 01.11 ok. 18:45:

| Liga | Mecze z prognozą |
|---|---|
| Championship | 24 |
| Ekstraklasa | 9 |
| LaLiga | 6 |
| Süper Lig | 6 |
| Premier League | 5 |
| Bundesliga | 5 |
| Pro League | 5 |
| Liga Portugal | 5 |
| Ligue 1 | 4 |
| Eredivisie | 4 |
| 2. Bundesliga | 4 |
| Serie A | 2 |
| **Razem** | **79** (wcześniej 22) |

Wszystkie 85 meczów lig z modelem ma dopasowane nazwy i aktualne wyniki. 6 nie ma prognozy, bo
drużyna ma za mało meczów w oknie modelu (beniaminkowie).

**Panel pokrycia** (Modele i statystyki → Pokrycie oferty, `coverage.py`):
- lejek, w którym każde wydarzenie odpada na pierwszym niespełnionym warunku;
- osobne liczby meczów, rynków i selekcji;
- podział według dyscypliny, ligi, daty, źródła wyników, modelu i powodu;
- liczba selekcji blokowanych **wyłącznie** przez niepotwierdzony kurs.

**Inne dyscypliny:**

| Dyscyplina | Wydarzenia STS (tydzień) | Legalne dane | Decyzja |
|---|---|---|---|
| Tenis | 95 | tennis-data.co.uk zabrania trenowania AI; repozytoria J. Sackmanna usunięte | brak modelu |
| Koszykówka | 180 | API-Sports Free: tylko sezony 2022–2024, bez bieżących wyników | brak modelu |
| Hokej | 89 | jak koszykówka | brak modelu |
| Siatkówka | 26 | brak zweryfikowanego źródła | brak modelu |

Bez bieżących wyników model by się zestarzał (reguła 21 dni). Pseudo-modeli opartych na kursach
STS nie tworzono.

**Kolejne ligi** (ranking dostępności danych):
1. LaLiga 2, Serie B, Ligue 2 — PitchAPI ma tylko 2 sezony; historię można uzupełnić z API-Sports
   2022–2024 jak w Ekstraklasie.
2. Allsvenskan, Eliteserien, Superligaen — sezony kalendarzowe wymagają rozszerzenia adaptera.
3. Brasileirão.
4. Liga Mistrzów i Liga Europy — wymagają modelu siły drużyn między ligami.

## E. Modele

**Obecne modele.** Każdy zakres ma własne wersje i własną walidację łączną.

| Zakres | Elo (1X2, podwójna szansa) | Dixon–Coles (gole, kalibracja izotoniczna) | Kto liczy 1X2 |
|---|---|---|---|
| 5 czołowych lig | `141c07acdd7a` (TASK 005) | `a0572027fd84` (TASK 005) | Elo |
| 7 lig dodanych w TASK 006 | `64c8e028c2db` | `d3941731c98d` | Dixon–Coles |

**Odrzucony wariant — jeden model dla 12 lig** (zachowany w historii, niewykorzystywany:
`b94599506f05`, `c37617125296`). Elo 1X2 i podwójna szansa straciły w nim status zwalidowanych:
nadmierna pewność w przedziałach 50–70% i 70–90%.

**Zakres rozszerzony — wyniki** [H]: walidacja 4742 mecze, holdout 5487 meczów od 01.07.2024.

**1X2, log loss:**

| Model | Walidacja | Holdout |
|---|---|---|
| Elo | 0,99881 | 1,01778 |
| Dixon–Coles | 1,00609 | 1,02330 |

- Elo ma niższy log loss, ale jest zbyt pewne siebie: przy szansie 60–70% trafiło 58,9% wobec
  średniej prognozy 64,4% — status eksperymentalny.
- Dixon–Coles po kalibracji jest zwalidowany (przewaga nad częstością bazową 0,028). Przy
  szansie 80–90% trafiło jednak 78,5% wobec średniej 83,0% — na granicy tolerancji, do obserwacji
  prospektywnej.
- **Nowa reguła wyboru modelu 1X2, osobno dla każdego zakresu:** najpierw model zwalidowany,
  a gdy oba lub żaden nie przeszedł — niższy log loss na walidacji. Holdout nigdy nie decyduje.
  W 5 czołowych ligach nic się nie zmienia (oba zwalidowane, wygrywa Elo).

**Pozostałe rynki Dixon–Coles:**

| Rynek | Status |
|---|---|
| Powyżej/poniżej 1,5 | zwalidowany (przewaga minimalna: 0,0004) |
| Powyżej/poniżej 2,5 | zwalidowany (przewaga 0,0046) |
| Powyżej/poniżej 3,5 | eksperymentalny (sezon z ECE 0,066 > 0,06) |
| BTTS | eksperymentalny (gorszy od częstości bazowej) |

**Nowe bramki ligowe** — warunek dodatkowy do walidacji łącznej, więc standard jest wyższy, nie
niższy:
- liga musi mieć co najmniej 300 prognoz w holdoucie i log loss niższy od własnej częstości
  bazowej;
- przedziały nadmiernej pewności są raportowane per liga, a decyduje o nich próba łączna;
- ligi, które nie przeszły, zostają eksperymentalne:

| Liga | Rynki eksperymentalne |
|---|---|
| 2. Bundesliga | 1X2 i podwójna szansa z Dixona–Colesa, linie 1,5 / 2,5 / 3,5, BTTS |
| Championship | linie 1,5 i 3,5 |
| Pro League, Eredivisie, Süper Lig | BTTS |

  Ekstraklasa i Liga Portugal przeszły we wszystkich ocenianych rynkach.

**Kalibracja:**
- izotoniczna, dopasowana na walidacji;
- wersja ostrożna to dolna granica 90% przedziału Wilsona;
- niezależność meczów sprawdzona (sekcja B).

**Nowe modele i ich ograniczenia:**
- Nie powstał Elo + forma, Dixon–Coles + xG, regresja logistyczna ani ensemble.
- Statystyki meczowe PitchAPI to wciąż tylko kilkadziesiąt obserwacji z momentem dostępności =
  czas obserwacji. Uczciwy eksperyment wymaga historii statystyk z jawną regułą dostępności
  (TASK 007).
- Statystyki nie są przedstawiane jako argument modelu.
- Ograniczenia bez zmian: brak składów i kontuzji w modelach; surowe Elo w 5 czołowych ligach
  jest lekko zbyt pewne przy faworytach.

## F. Historia

- **Baza użytkownika (01.11):** 0 zapisanych kuponów kreatora i 0 rozliczeń, bo przycisk
  generatora nie był jeszcze użyty na prawdziwych danych. Trafności i ROI nie podajemy.
- **Panel historii jest gotowy:**
  - podsumowanie: zapisane, rozliczone, wygrane i przegrane, trafność z prognozowaną szansą,
    zysk netto;
  - ROI z ostrzeżeniem przy mniej niż 30 rozliczeniach;
  - średni kurs;
  - wykres wyniku narastająco;
  - karty ostatnich kuponów: zielony — trafiony, czerwony — nietrafiony, neutralny —
    nierozliczony, ostrzegawczy — częściowo rozstrzygnięty (kupon nie jest nazywany wygranym,
    dopóki wszystkie selekcje nie są rozliczone).
- Rozliczenia są automatyczne przy każdej aktualizacji wyników.

## G. Jakość

**Pytest:** 223 testy zaliczone (196 + 27 nowych w `tests/test_task006.py`). Nowe testy obejmują:
- formaty polskie i odmianę liczebników, opisy typów prostym językiem;
- ocenę wiarygodności, grupy kandydatów A/B/C, minimalny próg „wiarygodnego typu”;
- przypadki A–E braku kuponu, kody odmowy i alternatywy kreatora;
- statusy pobierania STS (pusty, duplikat, bez dnia, zablokowane, pominięte, sprawdzanie
  innych wariantów) i świeżość liczoną od dokumentu;
- zakresy dat i pliki STS, lejek pokrycia (zdarzenia ≠ rynki ≠ selekcje);
- zakresy modeli i nieserwowanie eksperymentu 12 lig, filtr aktywnych wersji, regułę „najpierw
  zwalidowany”;
- uzupełnianie historii (awanse, obce źródło, aliasy, osobne pliki);
- bramki lig, test zależności, analizę pomeczową (prognoza nienaruszona, wynik każdego typu);
- perspektywę W/R/P, analizę wielodniową (osobne kupony i analizy dni, kroki wspólne raz);
- scenariusze interfejsu (AppTest).

**Ruff:** `ruff check` i `ruff format --check` bez uwag.

**Testy interfejsu:**
- AppTest wszystkich 7 stron na kopii prawdziwej bazy (offline) — bez wyjątków; analiza w
  Kuponie dnia pokazuje przypadek A.
- Scenariusze testami automatycznymi lub ręcznie w przeglądarce:

| Scenariusz | Sprawdzenie |
|---|---|
| 1 — kupon powstaje | zrzut 02 |
| 2 i 9 — brak modelu / dyscypliny | DEMO |
| 3 — stara oferta | kopia bazy, przypadek A |
| 4 — pusty PDF | test statusów |
| 5 i 6 — mecz rozpoczęty / zakończony | Getafe – Sevilla |
| 7 — brak xG lub składów | „Brak danych”, informacja o źródłach |
| 8 — błąd API lub limit | status „zablokowane” |
| 10 — 7 dni | test UI |

**Windows 11, Python 3.12:** `start.bat` bez zmian; `sports-edge check` sprawdza nowe pliki
konfiguracji i klucz PitchAPI.

**Znane ograniczenia:**
- pierwsze renderowanie strony w nowej minucie liczy widoki od nowa (0,1–2 s);
- analiza pomeczowa pokazuje prognozę z dowolnej wersji sprzed meczu;
- zrzuty powstały z testowym limitem świeżości 24 h;
- domyślna strona działa pod adresem „/”, a nie „/dashboard”.

## H. TASK 007

**Najważniejsze problemy pozostałe po redesignie:**
- kursy STS szybko się starzeją (limit 3 h) — kupon wymaga pobrania oferty tuż przed analizą;
- 80% oferty piłkarskiej to ligi bez legalnych, bezpłatnych danych;
- inne sporty bez bieżących wyników;
- zero rozliczonych kuponów.

**Priorytetowe rozszerzenia danych:**
- LaLiga 2, Serie B, Ligue 2 — historia API-Sports 2022–2024 + PitchAPI bieżący, jak
  Ekstraklasa;
- obsługa sezonów kalendarzowych (Allsvenskan, Eliteserien);
- automatyczne pobieranie składów ok. godziny przed meczem (tylko na żądanie lub w trakcie
  analizy).

**Modele warte rozwijania:**
- **Kalibracja Elo** (izotoniczna, na walidacji) — test par pokazał nadmierną pewność
  faworytów: 38,5% zamiast 43,5%;
- **Dixon–Coles + xG** po zebraniu historii statystyk z jawną regułą dostępności;
- **model łączny jednego meczu** z macierzy wyników Dixona–Colesa (np. 1X + poniżej 3,5 z
  dokładnym prawdopodobieństwem łącznym);
- **model siły drużyn między ligami** dla europejskich pucharów.

**Rekomendowany eksperyment zwiększający trafność kuponów:**
1. Kalibracja Elo w obu zakresach i porównanie z obecną wersją na tej samej walidacji: log
   loss, ECE, trafność faworytów, test par.
2. Następnie badanie prospektywne zamrożonym protokołem: codzienne kupony trzech profili przez
   8–12 tygodni, automatyczne rozliczenia, porównanie przewidywanej szansy z trafnością
   (Wilson) i ROI netto.
3. Decyzja o progach polityki dopiero na nowych, niezależnych wydarzeniach.
