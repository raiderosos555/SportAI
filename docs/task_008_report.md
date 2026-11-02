# TASK 008 — raport końcowy

Stan odniesienia: commit `2ebd2d6` (TASK 007) i lokalne zmiany po nim. Raport opisuje wyłącznie
to, co zostało zbudowane i sprawdzone w tej sesji. Gdzie czegoś nie dało się sprawdzić,
jest to napisane wprost.

**Najważniejsze w skrócie**

* Całą automatyzacją steruje strona **Automatyzacja AI**: przyciski uruchamiają osobny proces
  workera, a nie zadania w Harmonogramie Windows. Cykl uruchom → wstrzymaj → wznów → zatrzymaj
  sprawdzono ręcznie w przeglądarce na prawdziwym procesie workera.
* Silnik przedmeczowy pobiera składy z PitchAPI według harmonogramu względem godziny meczu.
  Zapisuje historię obserwacji, wykrywa zmiany i tworzy alerty.
  * Kontuzji i zawieszeń **nie zgaduje**: nie ma bezpłatnego, zweryfikowanego źródła, więc
    aplikacja mówi to wprost.
* **Advanced Football Prediction Engine** sprawdził na prawdziwych danych (2 zakresy lig,
  5 rynków, 18 wariantów i ablacja), czy forma, siła rywali, dom/wyjazd i odpoczynek poprawiają
  prognozy.
  * Wynik: **żaden wariant nie dał potwierdzonej poprawy** w oknie selekcji.
  * Elo i Dixon–Coles pozostają aktywne; szczegóły w rozdziale „Advanced Football Prediction
    Engine”.
* Testy: **299 testów pytest przechodzi** (w tym 31 nowych z TASK 008), Ruff bez uwag,
  22 ścieżki QA interfejsu na danych syntetycznych bez błędów.
* CI w GitHub Actions nie był jeszcze uruchomiony dla tych zmian — wymaga commita i pusha, a na
  to nie było Twojej zgody.

---

## A. Audyt i poprawki TASK 007

| Problem | Naprawa | Test |
|---|---|---|
| Odpowiedź lokalnego modelu (Ollama) mogła się pojawić przed walidacją | Odpowiedź jest pokazywana wyłącznie, gdy przeszła kontrolę (schemat JSON, liczby tylko z faktów); w przeciwnym razie zostaje odpowiedź szablonowa i powód odrzucenia | `test_task007.py` (fałszywy serwer Ollama) |
| Metryki prognoz zbiorcze, bez podziału | Osobno dla rynków (1X2, podwójna szansa, Over/Under, BTTS, gole drużyny), SOLO/AKO i profili; liczba **niezależnych meczów** i przedziały 95% (bootstrap całych meczów, Wilson dla trafności kuponów) | `test_metrics_are_reported_per_market…`, `test_coupons_are_split…` |
| Jeden znacznik czasu dla przeglądu, treningu, walidacji i aktywacji | Cztery osobne znaczniki w Uczeniu AI; przegląd zakończony „za mało danych” liczy się jako wykonany (nie powtarza się przy każdym odświeżeniu) | `test_a_review_that_skipped_training_counts_as_done` |
| Warunek „150 nowych meczów” liczył rekordy źródeł, nie mecze | Liczone są **unikalne mecze** (silnik wyników łączy źródła) | `test_new_matches_count_each_match_once…` |
| Nie dało się dodać rozpoczętego meczu do kuponu postawionego wcześniej w STS (Twój problem) | Tryb **„Wpis po fakcie”** w Mój kupon: tylko kupony prawdziwe (STS), tylko kursy zaobserwowane **przed** rozpoczęciem meczu; zapis jako osobny profil `my_coupon_after`, nie miesza się ze statystykami prospektywnymi | `test_task007.py` (wpis po fakcie) |
| **Wykryte w TASK 008:** metryki jakości liczyły także prognozy challengerów z trybu cienia (ten sam mecz mógł wejść dwa razy) | Do raportów jakości wchodzi tylko prognoza wersji, która **w chwili prognozy była serwowana** (z historii ról modeli) | `test_quality_reports_count_only_served_forecasts` |
| Asercja testu TASK 007 oczekiwała starego komunikatu („nowych wyników”) | Zaktualizowana do nowej, poprawnej treści („nowych unikalnych meczów”) | — |

Wcześniej w tej sesji, na Twoją prośbę: przywrócono modele protokołu v2 we wszystkich zakresach
(„Nowe v2 wszędzie”) i dodano trening z aplikacji („Trenuj teraz” w Uczeniu AI). Płatne API
Anthropic zastąpiono bezpłatnym lokalnym modelem (Ollama + Bielik), uruchamianym wyłącznie na
`127.0.0.1`.

## B. Pre-Match Intelligence

* **Źródło składów:** PitchAPI `/v1/matches/{id}/lineups` (zweryfikowane na prawdziwym kluczu).
  * `confirmed = false` oznacza skład **przewidywany** przez dostawcę (do 48 h przed meczem),
    `true` — potwierdzony.
  * Bramkarz: `position_id = 11` w wyjściowym składzie.
  * Ławka w kategoriach bramkarz / obrona / pomoc / atak; identyfikator zawodnika
    `player_id`.
* **Ligi:** 12 lig obsługiwanych przez PitchAPI w aplikacji — Premier League, Bundesliga, LaLiga,
  Serie A, Ligue 1, Ekstraklasa, Championship, Eredivisie, Liga Portugal, First Division A,
  2. Bundesliga, Süper Lig.
* **Dopasowanie meczu STS ↔ PitchAPI:** zweryfikowane nazwy drużyn (ta sama hierarchia
  dopasowań co dla wyników) i godzina rozpoczęcia ±36 h. Wynik trafia do tabeli
  `provider_fixtures`, więc kolejne kontrole nie szukają meczu ponownie.
* **Statusy:** przewidywany, potwierdzony, potwierdzony przez oficjalne źródło, jeszcze
  nieopublikowany, brak danych, błąd źródła.
* **Historia:** każda obserwacja to nowy wiersz `lineup_observations` (append-only).
  * Wiersz zapisuje: hash treści, `previous_id`, czas obserwacji i dostępności oraz flagę
    `known_before_start`.
  * Identyczna obserwacja nie jest zapisywana drugi raz.
  * Korekta dostawcy zachowuje poprzednią wersję.
* **Wykrywane zmiany:** publikacja potwierdzonego składu, zmiana bramkarza, nowy zawodnik
  w wyjściowej jedenastce, zawodnik wypadł lub przesunięty na ławkę, zmiana formacji. Zmiana to
  fakt, nie ocena: aplikacja nie twierdzi, że zmiana jest „zła”.
* **Ważność zawodnika** (opisowo, eksperymentalnie): jak często zawodnik wychodził
  w potwierdzonych składach drużyny. Gdy obserwacji jest mniej niż 5, aplikacja pokazuje
  „niewystarczające dane”. **Nie zmienia żadnych procentów.**
* **Wpływ na prognozy:** Etap A — wyłącznie ostrzeżenia. Etap B (model ze składami) jest
  zablokowany do czasu zebrania historii; obecnie żaden mecz nie ma składu znanego przed meczem.
  Patrz rozdział Advanced Engine, wariant F.

## C. Kontuzje i zawieszenia

* PitchAPI nie ma endpointu kontuzji. Nie znaleziono bezpłatnego źródła, które jest legalne
  i weryfikowalne.
* Interfejs `AbsenceProvider` jest gotowy na takie źródło. Obecna implementacja
  `NoVerifiedAbsenceSource` zwraca jawny status „brak zweryfikowanego źródła”.
* Mecze z Twoich kuponów dostają jeden alert o braku źródła na mecz, bez powtórzeń.
* Model danych (`player_absence_observations`) przechowuje dla każdego wpisu:
  * powód: kontuzja potwierdzona lub zgłoszona, zawieszenie, występ niepewny, niedostępny,
    powrót;
  * źródło i jego URL;
  * czas publikacji i obserwacji;
  * status dopasowania: zweryfikowany albo do sprawdzenia;
  * autora: dostawca albo użytkownik;
  * flagę `known_before_start`.
* **Zasady:**
  * Zawodnik jest dopasowywany tylko w obrębie jego drużyny — po `player_id` albo dokładnym
    nazwisku.
  * Niepewne dopasowanie trafia do sprawdzenia, nie do prognozy.
  * Transfer i konflikt źródeł są wykrywane.
  * Powtórzony wpis niczego nie dubluje.
* **Aplikacja nie wymyśla absencji** i nie zmienia prawdopodobieństw arbitralnymi
  współczynnikami.

## D. Automatyzacja AI przez WWW

**Architektura workera**

* **Osobny proces:** `python -m sports_edge.automation run --parent <PID serwera>`. Proces nie
  zależy od sesji Streamlit ani od karty przeglądarki.
* **Jedna instancja:** pilnuje jej blokada systemowa pliku `data/automation/worker.lock`
  (`msvcrt` na Windows, `fcntl` na Linuksie).
  * Drugi start kończy się komunikatem „inny worker już działa”.
  * `start_worker` nic nie robi, gdy blokada jest zajęta.
  * Blokada systemowa znika razem z procesem, więc „wygasła blokada” po awarii nie może
    zablokować kolejnego startu.
* **Heartbeat:** osobny wątek co 5 s zapisuje atomowo `data/automation/runtime.json`.
  * Plik zawiera: stan, PID, bieżące zadanie, ostatni sukces i błąd, następne terminy.
  * Status w panelu powstaje z blokady i świeżości heartbeat, nie z zapamiętanego przełącznika.
  * Heartbeat starszy niż 30 s daje status „błąd — worker nie działa”. Panel nigdy nie pokazuje
    fałszywego „aktywna”.
* **Polecenia i ustawienia w SQLite (append-only):**
  * `automation_commands` przechowuje polecenia: start, pause, resume, stop, run_now,
    check_coupons, check_events, run_job.
  * `automation_settings` przechowuje kolejne wersje ustawień; obowiązuje najnowsza.
  * `automation_events` zapisuje start, nadrabianie, przetworzenie polecenia, zniknięcie serwera
    i awarię (z fragmentem śladu).
  * Każde polecenie jest przetwarzane dokładnie raz.
* **Historia zadań:** `job_runs` z prefiksem `auto:` — start, koniec, status, powód
  i statystyki. Błąd zapisuje się jako krótka przyczyna, bez tracebacku w interfejsie.

**Jak użytkownik go uruchamia**

* **URUCHOM AUTOMATYZACJĘ** zapisuje polecenie `start` i startuje proces.
* **URUCHOM KONTROLĘ TERAZ** wykonuje wszystkie włączone zadania od razu. Gdy worker nie działa,
  jednorazowy proces wykona je i się zakończy.
* **SPRAWDŹ WSZYSTKIE MOJE KUPONY TERAZ** (Dashboard) oraz **Sprawdź składy meczów tego kuponu
  teraz** (Mój kupon) zlecają polecenie workerowi.
  * Gdy worker nie działa, startuje proces w trybie „tylko polecenia”.
  * Wykonuje on wyłącznie tę kontrolę, bez pozostałych zadań harmonogramu.

**Pauza i stop**

* **Pauza:** worker żyje i wysyła heartbeat, ale nie uruchamia zadań z harmonogramu.
  * „Kontrola teraz” działa także w pauzie.
  * WZNÓW przywraca harmonogram.
* **Stop:** worker kończy bieżącą operację, zapisuje zdarzenie, zwalnia blokadę i kończy proces.

**Konfiguracja** — przełączniki procesów, częstotliwości, punkty kontroli przed meczem, limit
kandydatów i „Automatycznie wznów po starcie serwera”:

* zapisywana przyciskiem ZAPISZ USTAWIENIA jako nowy wiersz SQLite;
* przetrwa odświeżenie, zamknięcie przeglądarki i restart komputera.

**Zamknięcie przeglądarki, serwera i restart**

* **Zamknięcie karty lub przeglądarki:** worker działa dalej, dopóki działa serwer aplikacji.
* **Zamknięcie serwera:** worker co cykl sprawdza, czy proces serwera (`--parent`) żyje.
  * Na Windows sprawdza to przez `OpenProcess`; nigdy przez `os.kill`, które na Windows kończy
    proces.
  * Gdy serwera nie ma, worker zapisuje zdarzenie „serwer zakończył pracę” i kończy się.
* **Restart:** `start.bat` / `start.ps1` uruchamiają `python -m sports_edge.launch`. Launcher
  startuje Streamlit, a następnie automatyczne wznowienie.
  * Worker wraca, jeśli jest włączone „Automatycznie wznów po starcie serwera” i ostatnim
    poleceniem użytkownika nie był stop.
  * Zabezpieczenie dla startu bez launchera: `main.py` wywołuje wznowienie raz na proces serwera
    (`st.cache_resource`).
  * Odświeżenie strony nie startuje drugiego workera.

**Nadrabianie zaległości**

* Termin zadania liczony jest od ostatniego zakończonego uruchomienia.
* Po przerwie każde zaległe zadanie wykonuje się **raz**, bez lawiny powtórzeń. Lista zaległych
  zadań trafia do zdarzenia `catch_up` i do panelu.
* Punkty kontroli przed meczem, które minęły przy wyłączonej aplikacji, są zapisywane jako
  „pominięte”. Nie są odtwarzane wymyślonymi obserwacjami z przeszłości.

**Błędy i limity**

* Błąd zadania odracza jego ponowienie wykładniczo: 5, 10, 20… minut, maksymalnie do zwykłej
  częstotliwości.
* Zajęta baza SQLite: zapis historii zadania jest ponawiany 3 razy z rosnącą przerwą.
* HTTP 429: `Retry-After` obsługuje warstwa źródeł danych.
* Brak klucza lub tryb offline: jeden jasny powód pominięcia zadania. Wcześniej był to błąd
  i alert dla każdego meczu — wykryte i naprawione w tej sesji.

**Zadania automatyczne (domyślnie)**

| Zadanie | Domyślnie |
|---|---|
| Aktualizacja wyników sportowych | co 180 min |
| Rozliczanie prognoz | co 60 min |
| Rozliczanie kuponów | co 60 min |
| Monitoring składów (względem godziny meczu) | sprawdzanie co 5 min, kontrola w punktach T-48h…T-15min |
| Weryfikacja kontuzji i zawieszeń | razem ze składami (brak zweryfikowanego źródła) |
| Pobieranie oferty STS | **wyłączone** (włącz świadomie), co 360 min |
| Ponowna kontrola kuponów | co 30 min |
| Analiza jakości prognoz | co 24 h |
| Przegląd potrzeby retreningu | co 7 dni |
| Trening challengera | **wyłączony**; po włączeniu tylko trening, **nigdy aktywacja** |

Automatyczne wznowienie jest domyślnie **wyłączone** — o pracy workera decydujesz w panelu.
Harmonogram zadań Windows nie jest używany ani wymagany.

## E. Monitoring kadrowy

* **Punkty kontroli względem rozpoczęcia meczu:** T-48h, T-24h, T-6h, T-90min, T-60min,
  T-30min, T-15min. Każdy można wyłączyć w panelu.
* **Co się dzieje w punkcie kontroli:**
  * Wykonuje się tylko **najnowszy** należny punkt; wcześniejsze, które minęły, są zapisywane
    jako pominięte.
  * Mecz po rozpoczęciu nie jest już sprawdzany.
* **Kolejność:**
  1. Najpierw wszystkie mecze z niezakończonych kuponów — Twoich, automatycznych, tasiemek
     i prawdziwych.
  2. Potem do 30 kandydatów z prognozą w horyzoncie 48 h (limit ustawiany w panelu).
* **Koszt zapytań:**
  * Pierwsza kontrola meczu: lista meczów sezonu (raz na przebieg, cache) i składy.
  * Kolejne kontrole: tylko składy, bo dopasowanie jest w `provider_fixtures`.
  * PitchAPI nie ma stałego limitu dziennego (fair use). Zapytania są dawkowane, a 429 jest
    obsługiwane.
* **Historia:** wynik każdej kontroli (`prematch_checks`) zapisuje status, wykryte zmiany,
  liczbę zapytań i czas trwania. Zmiany tworzą alerty (`prematch_alerts`, bez duplikatów)
  z priorytetem i oznaczaniem jako przeczytane.

## F. Ponowna ocena kuponów

* **Statusy:** **bez zmian**, **wymaga uwagi** (np. zmiana bramkarza lub nowy skład w meczu
  kuponu), **nieaktualny** (np. mecz już się rozpoczął albo kurs zniknął z oferty).
* **Zapis:** każda ocena to nowy wiersz `coupon_reassessments` z poprzednim wynikiem, powodami,
  ryzykami i źródłami. Zmiana statusu tworzy alert.
* **Oryginał nie jest ruszany:** kupon i jego prognozy pozostają bez zmian, a ponowna ocena
  jest tylko informacją.
* **Gdzie to widać:** Dashboard (kupony wymagające uwagi), Kupon dnia (status składu przy każdej
  selekcji) i Mój kupon (fakty o składach w ocenie AI).

## G. Uczenie modeli

* **Zbierane dane (as-of):**
  * obserwacje składów i kontroli przedmeczowych;
  * statystyki meczowe (od jesieni 2026);
  * prognozy prospektywne z rozliczeniami;
  * historia ról modeli.
* **Jakość prospektywna:** osobno dla każdego rynku, z liczbą niezależnych meczów i przedziałami.
  Na razie próby są **małe** (kilkanaście rozliczonych meczów na rynek) i panel to pokazuje.
* **Champion–challenger bez zmian zasad:** nowa wersja nigdy nie zastępuje aktywnej sama.
* **Eksperymenty cech:** rozdział Advanced Football Prediction Engine poniżej.
* **Ograniczenia:**
  * brak historii xG i strzałów w oknach protokołu;
  * brak składów znanych przed meczem w historii;
  * brak źródła kontuzji;
  * mała próba prospektywna.

## H. UI/UX

* **Nowa strona: Automatyzacja AI (Ustawienia):**
  * status pracy z blokady i heartbeat;
  * przyciski sterujące;
  * procesy i częstotliwości, punkty kontroli, automatyczne wznowienie;
  * historia zadań z POKAŻ SZCZEGÓŁY i URUCHOM PONOWNIE, błędy, alerty, jakość.
  * W trybie DEMO strona tylko informuje — worker pracuje wyłącznie na danych rzeczywistych.
* **Dashboard — sekcja „Kontrola przed meczem”:**
  * monitorowane mecze (w tym z Twoich kuponów);
  * składy potwierdzone, przewidywane i bez danych;
  * nowe zmiany, kupony wymagające uwagi, ostatnia kontrola, alerty;
  * przycisk SPRAWDŹ WSZYSTKIE MOJE KUPONY TERAZ.
* **Analiza meczów — zakładka „Składy i absencje”:**
  * składy obu drużyn z bramkarzem, formacją, źródłem i czasem obserwacji;
  * zmiany i absencje;
  * przycisk kontroli jednego meczu;
  * odpowiedź na pytanie „Czy model wykorzystał tę informację? Nie”.
* **Analiza meczów — „Dlaczego model tak ocenia ten mecz?”** rozdzielone na trzy części (patrz
  rozdział Advanced Engine).
* **Kupon dnia:** pod każdą selekcją status składu, zmiany, absencje i czas ostatniej kontroli.
* **Mój kupon:**
  * ocena AI zawiera fakty o składach (stan, zmiany, absencje, „model uwzględnia skład: nie”);
  * przycisk kontroli składów meczów kuponu;
  * 7 nowych pytań: oficjalne składy, nowe absencje, bramkarz, co się zmieniło, który mecz
    wymaga uwagi, czy prognoza uwzględnia składy, jakich danych brakuje.
  * Lokalny model AI dostaje te same fakty i zakaz wymyślania zawodników i kontuzji.
* **Uczenie AI:** panel eksperymentu cech z wynikami, kryteriami, ablacją, pokryciem danych
  i decyzją o challengerze.
* **Pasek boczny:** status automatyzacji i liczba nieprzeczytanych alertów.
* **Sprawdzenie wizualne:** wykonane ręcznie w przeglądarce aplikacji na kopii bazy:
  Analiza meczów, Uczenie AI, Dashboard, Automatyzacja AI. Widok telefonu (375 px) sprawdzono
  dla Analizy meczów.
* **Zrzuty ekranu:** powstają w CI (Playwright, desktop 1366 px i telefon 390 px) — szczegóły
  w sekcji I.

## I. Testy i QA

* **Pytest: 299 testów przechodzi lokalnie** (Windows, Python 3.12). Nowe w TASK 008:
  * `test_task008.py` (18): P0, składy i absencje, kontrola kuponu, worker;
  * `test_task008_advanced.py` (13): silnik cech, challenger, składy w kuponie i objaśniaczu,
    przyciski workera, zablokowane źródło, panel przedmeczowy, środowisko QA.
* **Ruff:** `ruff check .` i `ruff format --check .` — bez uwag.
* **Ścieżki QA interfejsu:** `python -m sports_edge.qa` — 22 ścieżki na danych syntetycznych,
  0 błędów. Nowe ścieżki: Automatyzacja AI — status i zapis ustawień.
* **Playwright** (`python -m sports_edge.qa_browser`):
  * Środowisko: tymczasowy katalog domowy (tylko kopia `config/`, bez bazy, `.env` i kluczy),
    oferta DEMO z fikcyjnymi drużynami, tryb offline.
  * Strony (desktop i telefon): Dashboard z kontrolą przed meczem i alertami, Kupon dnia,
    Mój kupon, Tasiemka, Symulator budżetu, Analiza meczu (składy: brak danych), Uczenie AI,
    Automatyzacja AI.
  * Dodatkowo: analiza bez internetu, zapis ustawień oraz pełny cykl workera z panelu
    (uruchom, wstrzymaj, wznów, zatrzymaj) na prawdziwym procesie.
  * **Lokalnie Playwright nie był uruchamiany:** wymaga pobrania przeglądarki, a o to nie
    prosiłeś. Ten sam przebieg sprawdzono ręcznie w przeglądarce aplikacji na identycznym
    środowisku QA.
* **GitHub Actions:** dla Pythona 3.12 na Windows i Ubuntu workflow:
  * uruchamia ścieżki QA;
  * instaluje Playwright i Chromium;
  * wykonuje zrzuty;
  * publikuje artefakty `qa-report-Windows` i `qa-report-Linux` z raportem ścieżek, wersjami,
    syntetycznym PDF i zrzutami.
  * Artefakt jest sprawdzany pod kątem plików bazy i kluczy.
  * **CI nie był jeszcze uruchomiony** dla tych zmian, więc kryterium „CI przechodzi” pozostaje
    niepotwierdzone do pierwszego pusha.
* **Problemy wykryte i naprawione w tej sesji:**
  * prognozy z trybu cienia w metrykach jakości;
  * zalew błędów i alertów przy braku klucza do źródła składów;
  * objaśniacz odpowiadał „brak danych” zamiast „brak zapisanych obserwacji”;
  * podwójna informacja o odpoczynku w „Dlaczego…”;
  * Dashboard pod adresem `/dashboard` pokazywał „Page not found” (strona domyślna jest pod
    `/`) — poprawione w QA;
  * serwer QA nie wczytywał motywu aplikacji;
  * testowe nazwy drużyn różniące się tylko cyfrą były scalane przez normalizator nazw
    (poprawione dane testowe).

**Pokrycie scenariuszy §27 (automatyzacja WWW)**

| Scenariusz | Jak sprawdzone |
|---|---|
| Uruchomienie, wstrzymanie, wznowienie, zatrzymanie | testy workera + ręcznie w przeglądarce na prawdziwym procesie + krok Playwright w CI |
| Jednorazowe uruchomienie | test polecenia `run_now` i trybu „tylko polecenia” |
| Zapis konfiguracji | test trwałości (nowe połączenie z bazą) + ścieżka QA + Playwright |
| Odświeżenie, zamknięcie i ponowne otwarcie przeglądarki, dwa okna | worker jest osobnym procesem (test prawdziwego procesu); status z blokady, nie z sesji; drugi start nic nie robi |
| Restart serwera, wznowienie włączone / wyłączone | testy `server_gone` i `autostart` |
| Brak duplikowania workerów i zadań | blokada systemowa, test drugiego startu, test restartu minutę później (zero powtórzeń) |
| Przerwanie zadania, awaria workera | stop po bieżącej operacji (test); awaria zapisywana jako zdarzenie — **bez osobnego testu wymuszonej awarii** |
| Nieaktualny heartbeat, wygasła blokada | test (status „błąd”); blokada systemowa znika z procesem |
| Konflikt zapisu SQLite | ponowienie zapisu w kodzie — **nie testowane bezpośrednio** |
| Brak internetu, limit API | tryb offline (ścieżka QA, Playwright), zablokowane źródło (test), 429 w warstwie HTTP |
| Nadrabianie, historia zadań, czas następnego uruchomienia | testy (zaległe zadania raz, backoff 5 min, następny termin) |

## J. Następne kroki (propozycja TASK 009)

Wyłącznie na podstawie wyników tej pracy:

1. **Rekalibracja serwowanego Dixona–Colesa dla BTTS i Over/Under 1,5.**
   * W 5 czołowych ligach BTTS ma w oknie selekcji log loss 0,6997 — gorzej niż stała 50%.
   * W ligach rozszerzonych sama rekalibracja, bez nowych cech, poprawia holdout O/U 1,5 o ok.
     0,018.
   * Ponieważ holdout był już oglądany, poprawka wymaga **nowego okna prospektywnego** przed
     aktywacją.
2. **Historia statystyk meczowych (xG, strzały, duże okazje).**
   * Pobieranie w tempie darmowego planu PitchAPI, z jawnie zapisaną regułą dostępności (koniec
     meczu + opóźnienie dostawcy).
   * Potem powtórka ablacji „Elo + xG” — dziś niewykonalna: 0 meczów w oknach dopasowania.
3. **Gromadzenie składów znanych przed meczem** przez workera. Po ≥ 300 meczach można uruchomić
   wariant F (składy) w protokole as-of.
4. **Źródło absencji:** ręczny wpis ze wskazaniem źródła (komunikaty klubów, ligi) albo
   legalne, weryfikowalne API. Do tego czasu absencje pozostają „brak źródła”.
5. **Ocena prospektywna:** zbieranie ≥ 300 rozliczonych prognoz na rynek przed decyzjami
   o statusach.
6. **Pierwszy przebieg CI** po pushu: przejrzenie artefaktów `qa-report-*` (zrzuty).

---

# Advanced Football Prediction Engine — wpływ dodatkowych informacji

**Pytanie:** czy informacje pokazywane w „Dlaczego model tak ocenia ten mecz?” — forma, siła
rywali, dom/wyjazd, odpoczynek, statystyki i składy — poprawiają prognozy, jeśli model
naprawdę ich używa?

**Odpowiedź na dziś: nie w sposób potwierdzony.**

* Żaden wariant nie przeszedł kryteriów w oknie selekcji, w żadnym zakresie i na żadnym z
  5 rynków.
* Elo (1X2, podwójna szansa) i Dixon–Coles (gole) **pozostają aktywne**.
* System nie utworzył challengera.

## Cechy użyte w eksperymentach

Wszystkie cechy liczone są **as-of**: do cechy meczu wchodzą tylko mecze z wcześniejszych dni.
Wynik meczu jest dostępny od następnego dnia — tak samo jak w walk-forward Elo.

Brak historii **nie jest zastępowany zerem ani typową wartością**: cecha jest wtedy pusta,
a mecz bez kompletu cech nie wchodzi do porównania.

Wersja definicji: `advanced-f1`.

| Cecha | Grupa | Źródło | Obserwacja → dostępność | Wymagana historia |
|---|---|---|---|---|
| Punkty na mecz (5 i 10) | forma | wyniki ligowe | koniec meczu → od następnego dnia | 5 / 10 meczów |
| Gole zdobyte i stracone (5), bilans na mecz (10) | atak/obrona | wyniki ligowe | jw. | 5 / 10 |
| Wynik ponad oczekiwanie Elo (5) | siła rywali | wyniki + Elo sprzed meczu | jw. | 5 |
| Średnia siła Elo 5 ostatnich rywali | siła rywali | jw. | jw. | 5 |
| Punkty i bilans u siebie (gospodarz) / na wyjeździe (gość), 5 | dom/wyjazd | wyniki ligowe | jw. | 5 meczów w tej roli |
| Dni odpoczynku (maks. 21) i mecze w 14 dniach | odpoczynek | daty meczów ligowych (bez pucharów) | data meczu | 1 |
| xG, xGA, strzały, strzały celne, duże okazje | statystyki | `stat_observations` (PitchAPI) | po meczu → od zapisu | **niewykonane** |
| Zmiany w składzie, absencje | kadra | `lineup_observations`, absencje | przed meczem (`known_before_start`) | **niewykonane** |

## Źródła, pokrycie i wielkości prób

* **Dane:** wyniki z `historical_results` (football-data.org, PitchAPI, API-Sports,
  openfootball) do 2026-11-01. Prognozy A i B to dokładnie wartości serwowane przez aktywne
  wersje:
  * 5 czołowych lig: Elo `9ec4b5a37cdc`, Dixon–Coles `bf8d23e1c833`;
  * 7 lig rozszerzonych: Elo `601fd42cee3a`, Dixon–Coles `50d80cbb56a3`.
* **Okna protokołu v2:**
  * dopasowanie: walidacja i kalibracja;
  * wybór: selekcja 2023/24;
  * holdout od 2024-07-01, tylko raportowany.

| Zakres | Dopasowanie (komplet cech) | Selekcja: wszystkie → komplet | Holdout: wszystkie → komplet |
|---|---|---|---|
| 5 czołowych lig | 3541 | 1752 → 1717 | 3930 → 3806 |
| 7 lig rozszerzonych | 2054 | 2474 → 2285 | 5544 → 5148 |

* **Braki cech:** wynikają z drużyn bez historii, np. beniaminków spoza danych zakresu,
  i z lig o krótszej historii (Polska, Turcja od 2022/23).
* **Statystyki meczowe:** zapisane tylko dla meczów z jesieni 2026, czyli 131 i 54 mecze
  w holdoucie oraz **0 w oknach dopasowania**. Wariant „Elo + xG” i grupa statystyk zostały
  więc **pominięte z jawnym powodem**.
* **Składy znane przed meczem:** 0 meczów, więc **wariant F nie był uruchamiany**.

## Warianty

| | Wariant | Model | Rynki |
|---|---|---|---|
| A | Elo — aktywny | jak serwowany | 1X2, podwójna szansa |
| B | Dixon–Coles — aktywny | jak serwowany (z kalibracją) | wszystkie |
| C | Elo + forma + siła rywali + dom/wyjazd | wielomianowa regresja logistyczna (L2) | 1X2, podwójna szansa |
| D | Dixon–Coles + wskaźniki ofensywne i defensywne | regresja Poissona z offsetem λ, μ Dixona–Colesa | wszystkie (jeden rozkład wyników) |
| E1 | ML: regresja logistyczna (Elo + wszystkie grupy) | jw. | 1X2, podwójna szansa |
| E2 | ML: boosting płytkich drzew (te same cechy) | gradient boosting, głębokość 2, wczesne zatrzymanie | 1X2, podwójna szansa |
| E3 | ML: regresja Poissona goli bez Dixona–Colesa | Poisson (Elo + cechy) | wszystkie |
| F | Model z informacjami kadrowymi | — | nie uruchomiono (brak historii as-of) |

**Spójność rynków:** podwójna szansa liczona jest zawsze z tego samego 1X2 (1X = 1 − P(2)).
1X2, Over/Under 1,5 i 2,5 oraz BTTS modeli goli pochodzą z jednej macierzy wyników — tej samej
co w `models.goals`, zgodność sprawdzona testem. Linie goli nie mogą sobie przeczyć.

**Protokół dopasowania:**

1. Model uczy się na oknie walidacji.
2. Siłę regularyzacji (lub liczbę drzew) wybiera okno kalibracji.
3. Model jest dopasowywany ponownie na obu oknach.
4. Porównanie odbywa się w oknie selekcji.

Wszystkie warianty jednego rynku oceniane są **na tych samych meczach**.

## Wyniki baseline (okno selekcji)

| Zakres | Rynek | Aktywny model | Log loss | Brier | ECE | Holdout log loss |
|---|---|---|---|---|---|---|
| 5 lig | 1X2 | Elo | 0,9805 | 0,5842 | 0,019 | 0,9869 |
| 5 lig | Podwójna szansa | Elo | 0,5738 | 0,1947 | 0,019 | 0,5770 |
| 5 lig | O/U 1,5 | Dixon–Coles | 0,5103 | 0,1655 | 0,025 | 0,5372 |
| 5 lig | O/U 2,5 | Dixon–Coles | 0,6719 | 0,2396 | 0,038 | 0,6818 |
| 5 lig | BTTS | Dixon–Coles | 0,6997 | 0,2459 | 0,052 | 0,6889 |
| 7 lig | 1X2 | Elo | 1,0006 | 0,5975 | 0,019 | 1,0159 |
| 7 lig | Podwójna szansa | Elo | 0,5837 | 0,1992 | 0,019 | 0,5927 |
| 7 lig | O/U 1,5 | Dixon–Coles | 0,5356 | 0,1711 | 0,028 | 0,5520 |
| 7 lig | O/U 2,5 | Dixon–Coles | 0,6845 | 0,2457 | 0,031 | 0,6830 |
| 7 lig | BTTS | Dixon–Coles | 0,6922 | 0,2494 | 0,037 | 0,7060 |

## Wyniki modeli rozszerzonych

**Jak czytać tabele:**

* Δ to różnica log loss: wariant minus aktywny model. **Ujemna wartość oznacza lepszy wynik.**
* W nawiasie podany jest 95% przedział bootstrap: 1000 losowań całych meczów, pary na tych
  samych meczach.
* Holdout jest tylko raportowany.

**1X2 — 5 czołowych lig**

| Wariant | Δ selekcja [95% CI] | Δ w danych dopasowania | Δ holdout |
|---|---|---|---|
| C Elo + forma + rywale + dom/wyjazd | −0,0009 [−0,0080; +0,0051] | −0,0026 | **+0,0062** |
| D DC + atak/obrona | +0,0000 [−0,0073; +0,0077] | +0,0012 | +0,0061 |
| E1 regresja logistyczna | −0,0004 [−0,0071; +0,0064] | −0,0052 | **+0,0083** |
| E2 boosting | **+0,0083 [+0,0020; +0,0139]** | **−0,0119** | +0,0091 |
| E3 Poisson bez DC | +0,0007 [−0,0042; +0,0059] | −0,0029 | +0,0013 |
| B Dixon–Coles (dla porównania) | +0,0006 [−0,0071; +0,0086] | −0,0009 | +0,0259 |

**1X2 — 7 lig rozszerzonych**

| Wariant | Δ selekcja [95% CI] | Δ w danych dopasowania | Δ holdout |
|---|---|---|---|
| C | +0,0027 [−0,0021; +0,0073] | −0,0020 | +0,0014 |
| D | +0,0038 [−0,0030; +0,0100] | +0,0021 | −0,0001 |
| E1 | +0,0028 [−0,0019; +0,0076] | −0,0031 | +0,0024 |
| E2 | **+0,0102 [+0,0048; +0,0160]** | **−0,0218** | +0,0076 |
| E3 | +0,0000 [−0,0035; +0,0037] | −0,0024 | −0,0005 |

**Rynki goli — najlepsze warianty**

| Zakres | Rynek | Najlepszy wariant | Δ selekcja [95% CI] | Δ holdout | Ta sama rekalibracja bez cech |
|---|---|---|---|---|---|
| 5 lig | O/U 1,5 | DC + wszystkie cechy | −0,0004 [−0,0046; +0,0038] | −0,0032 | +0,0002 / −0,0027 |
| 5 lig | O/U 2,5 | DC + atak/obrona | −0,0015 [−0,0059; +0,0030] | **+0,0040** | −0,0005 / +0,0049 |
| 5 lig | BTTS | D DC + atak/obrona | −0,0153 [−0,0482; +0,0030] | +0,0022 | −0,0149 / +0,0035 |
| 7 lig | O/U 1,5 | D DC + atak/obrona | −0,0111 [−0,0347; +0,0030] | −0,0181 | −0,0095 / −0,0179 |
| 7 lig | O/U 2,5 | E3 Poisson | −0,0030 [−0,0091; +0,0033] | +0,0026 | +0,0035 / +0,0037 |
| 7 lig | BTTS | E3 Poisson | −0,0028 [−0,0078; +0,0022] | −0,0171 | +0,0017 / −0,0134 |

Wariant „rekalibracja bez cech” (DC rekalibrowany regresją Poissona bez żadnej nowej cechy)
pokazuje, ile poprawy daje **sama kalibracja**, a ile nowe informacje. Prawie cała pozorna
poprawa w goli to rekalibracja, nie cechy.

## Ablacja cech (1X2, ta sama regresja logistyczna)

| Wariant | Δ 5 lig [95% CI] | Δ holdout 5 lig | Δ 7 lig [95% CI] |
|---|---|---|---|
| Elo rekalibrowany (bez cech) | −0,0009 [−0,0034; +0,0016] | 0,0000 | +0,0005 [−0,0026; +0,0038] |
| Elo + forma | −0,0032 [−0,0082; +0,0022] | +0,0029 | +0,0036 [−0,0002; +0,0076] |
| Elo + forma i siła rywali | −0,0001 [−0,0060; +0,0055] | +0,0064 | — |
| Elo + dom/wyjazd | −0,0028 [−0,0064; +0,0012] | +0,0002 | +0,0024 [−0,0011; +0,0061] |
| Elo + odpoczynek i terminarz | +0,0010 [−0,0029; +0,0051] | +0,0026 | — |
| Elo + forma + odpoczynek | −0,0016 [−0,0079; +0,0043] | +0,0054 | — |
| Sama forma, **bez Elo** | **+0,0210 [+0,0089; +0,0334]** | +0,0297 | **+0,0249 [+0,0167; +0,0334]** |
| Elo + statystyki xG | **niewykonane** — 0 meczów ze statystykami w oknach dopasowania | | |

Pełne tabele dla wszystkich rynków (Brier, ECE, ligi, półrocza, kalibracja) są w panelu
Uczenie AI → „Model rozszerzony — wpływ dodatkowych informacji”.

## Różnice w log loss, Brier i ECE

* **Log loss:** żadna poprawa nie ma 95% przedziału w całości poniżej zera.
  * Wariant o najlepszym wyniku punktowym w 1X2 (Elo + dom/wyjazd w 5 ligach, Δ −0,0028) ma
    przedział [−0,0064; +0,0012].
  * W holdoucie jego Δ wynosi +0,0002.
* **Brier:** różnice rzędu ±0,002, spójne co do znaku z log loss.
* **ECE:** warianty rozszerzone są skalibrowane podobnie albo lepiej niż aktywne modele (np.
  1X2 w 5 ligach: 0,013–0,027 wobec 0,019). Kalibracja nie była więc przeszkodą — przeszkodą
  był brak potwierdzonej poprawy trafności.

## Kontrola data leakage

* **Ponowne przeliczenie cech:** w każdym zakresie 12 losowych meczów z okna selekcji
  przeliczono od zera, wyłącznie z wyników wcześniejszych dni. **0 rozbieżności.**
* **Test braku wycieku:** późniejszy wynik nie zmienia cechy wcześniejszego meczu. Wyniki z
  tego samego dnia nie są znane.
* **Statystyki meczowe i składy:** wchodzą do eksperymentu tylko z obserwacji dostępnych przed
  meczem (`available_at`, `known_before_start`). Dziś nie ma ich w oknach dopasowania.
* **Podział ról okien:** hiperparametry wybiera okno kalibracji, model — okno selekcji.
  **Holdout niczego nie wybiera:** test odwraca wyniki holdoutu i sprawdza, że decyzje się nie
  zmieniają.
* **Zamrożone prognozy challengera:** liczone tym samym kodem (`predict_match`) i tymi samymi
  cechami as-of co backtest.

## Wniosek: co pomaga, a co nie

* **Forma (punkty, gole, bilans):** w dużej mierze **powtarza to, co wie Elo**.
  * Korelacja z różnicą Elo: 0,56–0,78 w 5 ligach, 0,61–0,86 w 7 ligach.
  * Sama forma bez Elo jest wyraźnie gorsza (Δ +0,021 / +0,025, istotnie).
  * Dodana do Elo nie daje potwierdzonej poprawy, a w holdoucie lekko pogarsza.
* **Siła rywali i wynik ponad oczekiwanie Elo:** niska korelacja z Elo (0,05–0,35), ale bez
  wartości predykcyjnej ponad Elo.
* **Dom/wyjazd:** najbliżej poprawy w 5 ligach (−0,0028), ale bez potwierdzenia i bez efektu
  w holdoucie.
* **Odpoczynek i zagęszczenie terminarza:** praktycznie zerowa korelacja z Elo (±0,03) i brak
  poprawy. Uwaga: liczone tylko z meczów ligowych — pucharów nie ma w danych.
* **Model drzewiasty (boosting):** **przeuczenie** — wyraźny zysk w danych dopasowania
  (−0,012 / −0,022) i wyraźna, istotna strata poza nimi (+0,008 / +0,010). Bardziej złożony
  algorytm nie pomógł.
* **Rynki goli:** pozorne korzyści, np. BTTS w 5 ligach i O/U 1,5 w 7 ligach, wynikają głównie
  z **rekalibracji** serwowanego Dixona–Colesa, nie z nowych informacji. W oknie selekcji nie są
  potwierdzone (przedziały obejmują zero). To wskazówka do osobnej pracy nad kalibracją
  (TASK 009, punkt 1), nie powód do dodawania cech.
* **xG, strzały, duże okazje, składy, kontuzje:** **nie dało się ich uczciwie ocenić** — brak
  historii sprzed meczów w oknach protokołu. Nie zostały wpisane do modeli „na wiarę”.

## Decyzja o challengerze

* Kryteria challengera, oceniane w oknie selekcji:
  * próba ≥ 300 meczów;
  * poprawa log loss z przedziałem 95% poniżej zera;
  * Brier nie gorszy;
  * ECE nie gorsze o więcej niż 0,01;
  * brak istotnego pogorszenia w żadnej lidze;
  * poprawa w każdym półroczu;
  * brak oznak przeuczenia;
  * decyzja użytkownika.
* **Żaden wariant nie spełnił kryterium potwierdzonej poprawy, więc żaden nie został
  zarejestrowany jako challenger i nie trafia do oceny prospektywnej.**
* Aktywne modele są bez zmian. Gdyby kiedyś wariant przeszedł kryteria:
  1. zostanie zarejestrowany jako wersja **eksperymentalna**;
  2. do trybu cienia trafi dopiero po kliknięciu „Dopuść do oceny prospektywnej”;
  3. jego zamrożone prognozy będą widoczne osobno w „Dlaczego…”, z wersją i statusem;
  4. promocja wymaga ≥ 300 rozliczonych prognoz i osobnej decyzji. Ta ścieżka jest
     przetestowana na danych syntetycznych z ukrytym efektem zmęczenia, który silnik poprawnie
     znalazł.

## „Dlaczego model tak ocenia ten mecz?” po zmianach

1. **Dane faktycznie użyte przez aktywny model:**
   * wersja Elo z liczbą meczów obu drużyn, współczynnikiem K i przewagą własnego boiska;
   * opis modelu Dixon–Coles;
   * kalibracja;
   * stan wyników ligi.
2. **Dodatkowe dane, których model jeszcze nie uwzględnia:**
   * forma, siła rywali, dom/wyjazd, odpoczynek;
   * status składów i absencji;
   * statystyki meczowe.
   * Ta część kończy się zdaniem, że te informacje **nie wpłynęły** na prawdopodobieństwa.
3. **Eksperymentalny model rozszerzony:**
   * gdy istnieje challenger w trybie cienia: jego osobna, zamrożona prognoza obok aktywnego
     modelu (różnica w punktach procentowych), wersja i status „eksperymentalny —
     niezwalidowany”;
   * w przeciwnym razie wniosek ostatniego eksperymentu.
   * Jeden procent nigdy nie zastępuje różnych prognoz.

## Jak powtórzyć

* **Gdzie:** Uczenie AI → „Model rozszerzony — wpływ dodatkowych informacji” → „Uruchom
  eksperyment cech” (osobno dla każdego zakresu).
* **Czas:** ok. 15–20 s na zakres.
* **Wpływ:** aktywne modele się nie zmieniają.
* **Dane w tym raporcie:** pochodzą z uruchomienia na **kopii** Twojej bazy z 2026-11-02;
  prywatna baza nie była przy tym zapisywana.
* **Powtarzalność:** przy tych samych danych wynik w aplikacji będzie identyczny (stałe ziarna
  losowania).
