# TASK 019 — Reprezentacje z kompletnymi danymi i codzienny przebieg bez martwych punktów

**Pomiar kopii danych:** kopia prawdziwej bazy z 12.11.2026, ok. 20:45 (czas polski), wykonana
przez `storage.snapshot_file`, w trybie offline, z poleceniem zatrzymania workera zapisanym w
kopii. Prawdziwej bazy nie otwierano nowym kodem. Po pomiarach kopie usunięto.
**Stan wyjściowy:** commit `adf0665` (TASK 018), schemat v12. **Po zmianie:** schemat v13.

Progi profili, bramka per rynek i protokoły v2/v3 — bez zmian. Jedyna nowa reguła, która może
zmienić skład kuponów, jest decyzją użytkownika w panelu „Decyzje do podjęcia” i do czasu tej
decyzji niczego nie zmienia (§3).

## 1. Źródła wyników i terminarzy reprezentacji

Sprawdzone 12–13.11.2026 na kluczach użytkownika (wartości kluczy nie były drukowane).

| Źródło | Zakres (sprawdzony w odpowiedzi API) | Bieżące wyniki IX–XI 2026 | Limity | Licencja | Opóźnienie |
|---|---|---|---|---|---|
| PitchAPI (`/v1/leagues`) | 77 lig; spośród reprezentacji tylko **World Cup** (sezony 2022, 2026; 104 mecze 2026, ostatni 19.07). Brak Ligi Narodów, eliminacji i towarzyskich | **nie** | uczciwe korzystanie | regulamin nieopublikowany, użycie prywatne | — |
| football-data.org (`/v4/competitions`) | 13 rozgrywek planu użytkownika; reprezentacje: `EC` (EURO 2024) i `WC` (2026). `UNL` → **HTTP 403** („restricted … check your subscription”), `WCQ` → 404 | **nie** | 10 zapytań/min | Free Tier, atrybucja | — |
| API-Sports (`/status`, `/fixtures?league=5&season=2026`) | plan **Free**, 100 zapytań/dzień; Liga Narodów 2026: „Free plans do not have access to this season, try from 2022 to 2024” | **nie** | 100/dzień (zużyto 1 zapytanie; `/status` nie jest liczony) | regulamin przy rejestracji | — |
| openfootball/internationals (GitHub) | ostatni push **24.08.2026**; brak pliku `2026_uefa_nations_league.txt`; `2026_friendly.txt` kończy się 31.03; `2026_fifa_world_cup_qualification.txt` bez meczów UEFA jesienią | **nie** | brak | CC0 | tygodnie–miesiące |

**Wniosek:** żadne bezpłatne źródło dostępne na kluczach użytkownika nie zwraca wyników ani
terminarzy reprezentacji z bieżącego okna. Zgodnie ze specyfikacją **nie wdrożono nowego
adaptera** i nie wpisano niezweryfikowanych identyfikatorów rozgrywek. Bieżące wyniki są
dostępne przez **ręczny wpis ze źródłem** (§2). Oficjalnym źródłem do wpisu jest organizator:
UEFA publikuje terminarz i wyniki fazy ligowej Ligi Narodów 2026/27 na uefa.com (strona
„fixtures and results”) — aplikacja tej strony nie pobiera; to źródło do przepisania wyniku.

**Opcje płatne (nic nie kupiono; ceny z zestawień zewnętrznych — do potwierdzenia na stronie
dostawcy):**

| Opcja | Cena | Co daje | Uwagi |
|---|---|---|---|
| API-Football **Pro** | ok. 19 USD/mies. (RapidAPI / zestawienia) | 7 500 zapytań/dzień, bieżące sezony wszystkich rozgrywek, w tym Liga Narodów (`league=5`), eliminacje, towarzyskie; `score.fulltime` = wynik po 90 min, `fixture.venue` | **najtańsza opcja**; klucz już używany przez aplikację (ten sam adapter, inne sezony) |
| football-data.org **Standard** | ok. 49 EUR/mies. (zestawienia) | 30 rozgrywek | nie potwierdzono, czy obejmuje Ligę Narodów |

Źródła cen: [api-football.com/pricing](https://www.api-football.com/pricing),
[RapidAPI — API-Football](https://rapidapi.com/api-sports/api/api-football/pricing),
[zestawienie football-data.org](https://www.thestatsapi.com/blog/football-data-org-free-tier-limits-2026).
Terminarz i wyniki UEFA: [uefa.com — Liga Narodów 2026/27](https://www.uefa.com/uefanationsleague/news/02a2-1fea7079900a-801b2c5f6c9e-1000--2026-uefa-nations-league-league-phase-fixtures-and-resul/).

**Częstotliwość zadania `national_results`:** w dniu meczu reprezentacji (nagłówek STS
rozgrywek zakresu `national`) i dzień po nim zadanie uruchamia się tak często jak wyniki lig
(`results`, domyślnie co 3 h); w pozostałe dni — jak dotąd co 3 dni
(`automation.national_matchday_next`).

## 2. Ręczny wynik ze źródłem

* Formularz **„Wpisz wynik ręcznie (ze źródłem)”**: Wydarzenia i wyniki → Zakończone i wyniki
  (lista meczów po czasie gry bez wyniku, z zaznaczeniem tych z kuponów) oraz zakładka
  „Wynik” w szczegółach kuponu, który czeka na wynik. Pola: wynik po 90 minutach, źródło (tekst
  lub link — obowiązkowe), data wpisu.
* Dodatkowo „Mecz reprezentacji spoza oferty STS” (np. wrzesień–październik): drużyny wybiera
  się z historii wyników, miejsce meczu nie jest wpisywane (bez przewagi gospodarza).
* Zapis append-only: wiersz `results` ze źródłem `manual_entry` (rozlicza kupony i prognozy
  tak samo jak wynik dostawcy) i — gdy obie drużyny są w historii — wiersz `historical_results`
  dostawcy `manual_entry` z `available_at` = chwila wpisu, więc wynik nie trafia do prognoz
  sprzed wpisu (test).
* Wpis jest możliwy dopiero po typowym czasie meczu; wynik od dostawcy, jeśli już jest,
  blokuje wpis.
* **Pierwszeństwo dostawcy:** `manual_entry` ma najniższy priorytet w hierarchii źródeł.
  Późniejszy wynik dostawcy jest zapisywany jako nowy wiersz (wpis zostaje w historii) i
  ponownie rozlicza kupony i prognozy; inny wynik tworzy alert „Wynik dostawcy różni się od
  wpisu ręcznego”. Różnica nie jest traktowana jako konflikt źródeł (konflikt wstrzymywałby
  rozliczenie).
* Statystyki: kolumna **„Źródło wyniku”** (dostawca / ręczny / dostawca + ręczny) w liście
  kuponów Historii i w tabeli zapisanych wyników.

## 3. Kompletność danych drużyny i decyzja użytkownika

Miara per drużyna (`team_gaps.py`): data ostatniego meczu w bazie, liczba meczów, które na pewno
się odbyły i nie mają wyniku (archiwum oferty STS po czasie gry bez statusu
przełożony/odwołany oraz zapisane terminarze), oraz okres bez żadnych danych przed pierwszym
dniem objętym archiwum lub terminarzem. Okres bez danych dłuższy niż 45 dni
(`[completeness] unknown_gap_days` w `config/national_scope.toml`) to „brak danych”, nigdy 0.

Plakietki: przy typie (kolumna „Dane drużyn” w Najlepszych typach, notatka nogi kuponu) i w
Analizie meczu, np. „Model nie zna 1 ostatniego meczu drużyny (Armenia) — ostatni mecz w
bazie: 29.03.2026; o okresie do 11.11.2026 brak danych (227 dni)”.

Decyzja **„Reprezentacje z brakującymi wynikami ostatniego okna: dopuszczać do profili
wymagających walidacji?”** — odpowiedzi: „dopuszczać (jak dotąd) — tylko ostrzeżenie”
(domyślnie) albo „nie dopuszczać” (zwalidowany typ takiej drużyny traktowany jak
eksperymentalny: tylko WYŻSZA WYPŁATA jako symulacja). Każda decyzja to nowy wpis
`user_decisions`.

**Na kopii (archiwum STS reprezentacji zaczyna się 11.11.2026, brak terminarzy):**
12.11 o 17:00: 94 drużyny „brak danych”, 1 z meczem bez wyniku; po meczach 12.11 każda
drużyna, która grała, ma „Model nie zna 1 ostatniego meczu”. Ostrzeżenie dotyczy wszystkich
typów reprezentacji z prognozą (12.11: 294, 13.11: 353, 14.11: 311, 15.11: 111, 16.11: 60).

| Dzień (chwila pomiaru) | Zwalidowane typy reprezentacji: dopuszczać → nie dopuszczać | Kupony: dopuszczać | Kupony: nie dopuszczać |
|---|---|---|---|
| 12.11 (15:40) | 48 → 0 | WYSOKA TRAFNOŚĆ (1 noga, kurs 1,28, 80% / ostrożnie 76%), STANDARDOWY (2, 1,85, 60% / 54%), WYŻSZA WYPŁATA (2, 2,57, 56% / 47%), TASIEMKA (7, 8,16, 14% / 8%), PEWNY SOLO (1, 1,22, 87% / 77%) | tylko WYŻSZA WYPŁATA (te same nogi, status „SYMULACJA — model eksperymentalny”) |
| 13.11 (12.11, 20:50) | 60 → 0 | brak (kursy starsze niż 180 min) | brak |
| 14.11 | 54 → 0 | brak (jw.) | brak |
| 15.11 | 47 → 0 | brak (jw.) | brak |
| 16.11 | 60 → 0 | brak (jw.) | brak |

Przy „dopuszczać” kandydaci i kupony są identyczne jak przed zmianą (12.11: te same profile,
nogi i szanse co w aplikacji; test regresji z TASK 018). 13–16.11 kupony nie powstają przy
żadnej odpowiedzi, bo ostatnie dokumenty STS tych dni miały ponad 180 minut — decyzja zmienia
tam tylko status 221 zwalidowanych typów.

## 4. Nierozliczone kupony — przyczyna i powód na ekranie

**TASIEMKA z 08.11** (7 nóg): brakowało nogi **Elche – Real Betis**. Wynik był w bazie z
football-data.org od 08.11, ale STS pisze „Real Betis”, a źródło „Real Betis Balompié” — bez
aliasu noga nie dostała wyniku. Dodano zweryfikowany alias (dowód: ten sam dzień, rozpoznany
przeciwnik Elche CF, jedyny mecz) do `config/team_aliases.toml`. Na kopii: wynik przeniesiony,
TASIEMKA rozliczona.

Każdy kupon nierozliczony ponad 24 h po ostatnim meczu pokazuje brakującą nogę, oczekiwany
wynik („wynik po 90 minutach”) i źródło z datą najnowszego wyniku tych rozgrywek, powód (np.
„źródło nie ma jeszcze wyników tych rozgrywek z tego dnia”, „nazwa drużyny bez potwierdzonego
aliasu: …”) i ostatnią próbę (zadanie i jego wynik). Dashboard: jedna pozycja „Kupony czekające
na wynik: N” z przejściem do listy i formularza.

**Kupony z 12.11 na kopii (§6.2):** 4 oficjalne kupony dnia na Ligę Narodów czekały na wynik
(powód: openfootball — najnowszy wynik Ligi Narodów 08.06.2025). Brakujące 5 meczów wpisano
ręcznie **wynikami testowymi** (źródło „TEST na kopii bazy — wynik nieprawdziwy”; prawdziwych
wyników nie zgadywano i nie pobierano). Wynik: wszystkie kupony z 12.11 rozliczone (rozliczenia
ogółem 36, czekających 0), 5 wierszy historii `manual_entry`. Wyników finansowych tych
rozliczeń nie podaję — dotyczą wyników testowych.

## 5. Liczby z kopii (§6)

1. Mecze reprezentacji z wynikiem, wrzesień–listopad 2026: **0 przed i 0 po** (ze źródeł);
   najnowszy wynik puli 19.07.2026. Po zmianie bieżące wyniki można wpisać ręcznie.
2. Kupony z 12.11: rozliczone na kopii ręcznym wynikiem (§4).
3. Prognozy 13–16.11: nowych wyników ani terminarzy nie było, więc prognozy przed i po są
   identyczne. Elo 1X2 (zakres reprezentacji) obok kursów STS bez marży (wybór; pełna lista:
   37 meczów, wszystkie z ostrzeżeniem o brakach):

| Mecz | Model (1 / X / 2) | STS bez marży |
|---|---|---|
| Serbia – Niemcy (13.11) | 19 / 25 / 56 | 10 / 15 / 75 |
| Szkocja – Macedonia Płn. (13.11) | 51 / 26 / 22 | 80 / 14 / 6 |
| Holandia – Grecja (13.11) | 59 / 24 / 18 | 62 / 21 / 17 |
| Rumunia – Polska (14.11) | 23 / 27 / 50 | 25 / 26 / 49 |
| Portugalia – Dania (14.11) | 46 / 28 / 26 | 63 / 21 / 16 |
| Hiszpania – Anglia (15.11) | 53 / 26 / 21 | 60 / 21 / 19 |
| Francja – Turcja (15.11) | 56 / 25 / 19 | 79 / 13 / 8 |
| Finlandia – San Marino (15.11) | 90 / 7 / 3 | 92 / 6 / 2 |
| Grecja – Serbia (16.11) | 37 / 29 / 34 | 65 / 21 / 14 |
| Niemcy – Holandia (16.11) | 32 / 29 / 39 | 51 / 24 / 25 |

   Przy wyraźnym faworycie-gospodarzu model daje gospodarzowi o 10–30 pkt mniej niż rynek:
   bez terminarza nie liczy przewagi gospodarza, a rating nie zna meczów z września–października
   (to samo ograniczenie co w TASK 018).
4. TASIEMKA z 08.11: brak aliasu „Real Betis” (§4).
5. Zrzuty z kopii (Kupon dnia, Analiza meczu reprezentacji, Automatyzacja) obejrzano lokalnie;
   nie są w repozytorium (prawdziwe dane, repozytorium publiczne).

## 6. Kupon dnia i zadanie „Kupon dnia”

* Kolejność: kupony → „Reprezentacje — rozgrywki bez danych” jako **zwinięty panel pod
  kuponami** z jedną linią („24 mecze z 5 rozgrywek bez historii”), bez powtórzonych meczów,
  pogrupowany po rozgrywkach.
* „Ryzyko dnia”: domyślnie dwa zdania (szansa przegrania co najmniej dwóch kuponów, wspólne
  mecze); metoda, macierz wyników i „starszy zapis” w panelu „Szczegóły ryzyka dnia”.
* „Zakres analizy”: po przełączeniu od razu zapisane propozycje wybranych dni albo komunikat
  „Na ten dzień nie ma jeszcze kuponów — kliknij Analizuj”.
* Plakietki „W grze od HH:MM” i „Czeka na wynik” (Kupon dnia i Dashboard). Kupon z
  rozpoczętym meczem nie ma przycisku „Dodaj do mojego kuponu”; w Najlepszych typach
  rozpoczętych nie da się dodać.
* Zamiast „kontrola: jeszcze nie wykonana” — faktyczny powód: „brak źródła składów dla tych
  rozgrywek” (reprezentacje), „pominięta — automatyzacja nie działała…”, „kupon zapisany po
  ostatnim punkcie kontroli”, „zaplanowana przed meczem”.
* Najlepsze typy: gdy grupy A i B są puste, bo mecze się rozpoczęły — komunikat i najbliższy
  dzień z meczami przed startem.

**Zadanie „Kupon dnia”** (Automatyzacja → Harmonogram → „Kupon dnia automatycznie”):
domyślnie **wyłączone**. Po włączeniu: o stałej godzinie (domyślnie 09:00) albo N minut przed
pierwszym meczem dnia z prognozą; raz dziennie. Ta sama ścieżka co przycisk (`run_analysis` z
zapisem, tożsamość kuponu i reguła kuponu oficjalnego z TASK 010 — zadanie i kliknięcie tego
samego dnia dają jeden kupon oficjalny na profil, test). W trybie offline nic nie pobiera;
online ofertę STS tylko za zgodą użytkownika i w jej limitach. Wynik: wpis w Historii zadań i
jeden alert „Kupony dnia gotowe …” albo „Brak potwierdzonej przewagi — brak kuponu dnia”.

## 7. Zmiany interfejsu

| Ekran | Zmiana |
|---|---|
| Analiza meczu (reprezentacje) | jedno źródło dni odpoczynku **i formy (5)** dla kart i tabeli „Porównanie drużyn” („wszystkie mecze reprezentacji w bazie”, bez „tylko ligowe”; przegląd zrzutu na kopii pokazał też sprzeczną formę — Mołdawia „W W W P W” w tabeli wobec „P P P P R” na kartach — poprawione; miary z innego źródła: „brak danych”); polskie nazwy z istniejących aliasów (STS i `team_aliases.toml`); wynik z perspektywy drużyny („P 1:9”); „Wyniki rozgrywek do …” + „Ostatni mecz drużyny: …”; plakietki braków; domyślnie najbliższy dzień z meczem przed startem |
| Status wydarzenia | po czasie gry bez wyniku: „zakończony — czekamy na wynik”; „status nieznany” tylko bez godziny startu |
| Wydarzenia i wyniki | „Rozgrywki” tylko dla piłki (inne: „—”); polskie nazwy dyscyplin; sekcje „Kupony czekające na wynik”, „Zakończone — czekamy na wynik”, formularz ręcznego wyniku, kolumna „Źródło wyniku” |
| Mój kupon | selekcje rozpoczęte oznaczone, „Usuń rozpoczęte (N)”, pominięte w szansie (poza „Wpis po fakcie”); pierwsze wydarzenie na liście ma prognozę |
| Tasiemka | własny opis i odróżnienie od „Kupon na kurs”; adres `/tasiemka` działa |
| Alerty | alert meczu rozpoczętego archiwizowany nowym wpisem (`alert_states`, schemat v13); licznik tylko alertów, na które można zareagować |
| Automatyzacja | statusy po polsku (wykonane / częściowo / pominięte / błąd / —); kody przyczyn jako zdania; „Najważniejsze błędy” bez błędów naprawionych późniejszym udanym przebiegiem; tylko przyciski możliwe w danym stanie; kalendarz z krótkimi nazwami i zawijaniem |
| Historia | wykres „zysk/strata narastająco, zł” na osi czasu z opisem osi; jedna lista kuponów (z kolumnami „Relacja” i „Źródło wyniku”), druga zakładka to szczegóły zaznaczonego kuponu; „Kupon wirtualny — propozycja aplikacji” |
| Język | bez „TASK 006”, „(Etap A)”, „raport TASK 006”; `beats_market: false` tylko w szczegółach; skrócone identyfikatory wersji; polski placeholder „wybierz…” zamiast „Choose an option”; Uczenie AI — zasady challengerów raz, przy zakresach tabela |
| Okno „Help agents write better apps” | `[logger] hideWelcomeMessage = true` w `.streamlit/config.toml` (Streamlit 1.66 nie ma osobnej opcji; okno jest wstrzymywane, gdy powitanie jest ukryte — `streamlit/web/skills.py`). Launcher sam drukuje adres interfejsu |

## 8. Testy

* `tests/test_task019.py`: 22 testy (brak niezweryfikowanego adaptera, brak przecieku wpisu
  ręcznego, wpis append-only i rozliczenie, pierwszeństwo dostawcy i alert, odmowa przy
  istniejącym wyniku dostawcy, miara kompletności i plakietka, kandydaci bez decyzji identyczni
  poza ostrzeżeniem, decyzja w panelu, kupon nierozliczony > 24 h z nogą i powodem, zadanie
  „Kupon dnia” wyłączone domyślnie, jeden kupon oficjalny po zadaniu i kliknięciu, offline bez
  pobrań, zakres pokazuje zapisane propozycje bez analizy, „W grze” / „Czeka na wynik” i brak
  przycisku koszyka, tabela braków bez duplikatów i pod kuponami, „Ryzyko dnia” w dwóch zdaniach,
  jedna wartość odpoczynku i polskie nazwy, `event_status`, kategorie i dyscypliny, koszyk,
  archiwum alertów, statusy i przyczyny po polsku, częstotliwość `national_results`, konfiguracja
  Streamlit).
* Dostosowane asercje istniejących testów (żaden test nie został wyłączony): `test_task004`
  (nowy stan „czekamy na wynik”), `test_task010` (jedna lista kuponów z kolumną „Relacja”),
  `test_task015` (siódma decyzja), `test_task016` (migracja v11 → v13, licznik alertów do
  reakcji).
* Pełny `pytest`: **537 zaliczonych, 4 pominięte** (brak lokalnych autentycznych PDF-ów STS), 0 błędów.
* `ruff check .`, `ruff format --check .`: bez uwag.
* Ścieżki QA (`python -m sports_edge.qa`): 40 / 0 błędów; `sports_edge.qa_browser`: 22 zrzuty /
  0 błędów; `sports_edge.qa_visual` (5 rozdzielczości, nowe scenariusze: Automatyzacja →
  Przegląd i Harmonogram, Wydarzenia → Zakończone, kontrola braku okna „Help agents…” na każdej
  stronie): **118 kontroli / 0 błędów**. Przegląd na kopii prawdziwej bazy (1366×768: Dashboard,
  Kupon dnia, Analiza meczu reprezentacji, Automatyzacja, Wydarzenia): 5 / 0 błędów.

## 9. Ograniczenia i proponowany TASK 020

* Brak bezpłatnego źródła bieżących wyników i terminarzy reprezentacji — do czasu wpisów
  ręcznych (albo płatnego planu) rating nie zna jesiennych okien, a mecze są liczone bez
  przewagi gospodarza.
* Analiza meczu reprezentacji nie ma scenariusza Playwright na danych DEMO (DEMO nie zawiera
  reprezentacji); sprawdzono ją na kopii prawdziwej bazy.
* Archiwum oferty STS reprezentacji zaczyna się 11.11.2026 — wcześniejsze okna są „brak danych”.

Proponowany TASK 020: (1) decyzja użytkownika o planie API-Football Pro (ok. 19 USD/mies.) i —
po niej — adapter bieżących wyników i terminarzy reprezentacji z miejscem meczu; (2) import
ręczny CSV z terminarzem/wynikami ze strony organizatora (UEFA) z obowiązkowym źródłem;
(3) ocena prospektywna reprezentacji po 17.11 (rozliczone prognozy, porównanie z kursami STS).

## Instrukcja dla użytkownika

1. Po aktualizacji zrestartuj aplikację i workera (pierwszy start nowego kodu zrobi kopię
   `pre-migration-v13_*.db` i migrację do v13).
2. Wyniki meczów reprezentacji: Wydarzenia i wyniki → Zakończone i wyniki → „Wpisz wynik
   ręcznie (ze źródłem)”, np. z uefa.com.
3. Modele i statystyki → Decyzje do podjęcia → „Reprezentacje z brakującymi wynikami ostatniego
   okna” — wybierz odpowiedź (do tego czasu działa jak dotąd).
4. Opcjonalnie: Automatyzacja AI → Harmonogram → „Kupon dnia automatycznie”.
