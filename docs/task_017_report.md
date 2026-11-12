# TASK 017 — Prognozy reprezentacji w trybie cienia

**Pomiar kopii danych:** 12.11.2026, 12:00 UTC  
**Stan rynku:** niezwalidowany i zablokowany  
**Aktywacja:** brak decyzji zapisanej w bazie; nie utworzono prognozy kuponowej ani kuponu

## Przyczyna i zmiany

Katalog rozgrywek nie zawierał dwóch nagłówków reprezentacyjnych zaobserwowanych w ofercie STS. Proces prognoz i kuponów nie miał odrębnej, wersjonowanej ścieżki `national · 1X2 (90 min)`, a brak aktywnego modelu nie pokazywał konkretnego powodu blokady. Pozostałe rozgrywki międzynarodowe nie były potwierdzone w katalogu, więc nie zostały automatycznie dopisane.

TASK 017 dodaje dokładne wpisy dla Ligi Narodów UEFA i meczów towarzyskich, walk-forward reprezentacji, osobny worker cieniowy oraz widoki pokrycia, walidacji i przyczyn blokad. Prognozy badawcze są append-only w `stat_observations`; nie trafiają do tabeli prognoz obsługiwanej przez generator. Nowa ścieżka 1X2 po 90 minutach korzysta z istniejących prognoz i warunków kuponu dopiero po przejściu walidacji rejestru, decyzji dla dokładnej wersji i standardowych kontroli oferty. Odmowa lub wycofanie zgody jest nowym wpisem decyzji.

Nie zmieniono profili, progów, definicji rynków ani polityki walidacji. W szczególności rejestr modelu nadal wymaga porównania z benchmarkiem rynkowym na tych samych wydarzeniach. Dla reprezentacji benchmark powstaje wyłącznie z kompletnych kursów STS 1X2 zapisanych przed startem tych samych meczów.

## Zakres źródeł i pokrycie kopii

W katalogu obsługiwane są seniorskie mecze oficjalne: Liga Narodów, EURO i kwalifikacje, Mistrzostwa Świata i kwalifikacje; oddzielnie mecze towarzyskie. W pomiarze historii użyto dostępnych w projekcie dostawców `openfootball_int` i `football_data_org`. Nie użyto Flashscore, Meczyków, scrapingu ani płatnych API.

| Rozgrywki | Wyniki w historii* | Ostatni mecz | Najpóźniejsza dostępność wyniku | Dostawca w kopii |
|---|---:|---|---|---|
| Liga Narodów UEFA | 658 | 08.06.2025 | 09.06.2025 | `openfootball_int` |
| EURO | 186 | 14.07.2024 | 23.07.2024 | `football_data_org`, `openfootball_int` |
| Kwalifikacje EURO | 1 016 | 26.03.2024 | 27.03.2024 | `openfootball_int` |
| Mistrzostwa Świata | 356 | 19.07.2026 | 02.08.2026 | `football_data_org`, `openfootball_int` |
| Kwalifikacje MŚ | 1 008 | 31.03.2026 | 01.04.2026 | `openfootball_int` |
| Towarzyskie | 1 091 | 31.03.2026 | 01.04.2026 | `openfootball_int` |

*Liczby przed wykluczeniem wyników z niepewną semantyką 90 minut. Do oficjalnego walk-forward weszło 3 212 wyników po wykluczeniu 12 niepewnych; konfliktów źródeł nie było. Wyniki towarzyskie są oceniane osobno. Neutralne (437) jest podzbiorem 3 061 spotkań ze znanym miejscem.*

W bazie STS znaleziono 44 wydarzenia Ligi Narodów UEFA i 10 meczów towarzyskich. Wszystkie 54 mają terminarz. Dokładne lub zatwierdzone mapowanie obu uczestników jest możliwe dla 2 wydarzeń, z których oba dotyczą Ligi Narodów; 0 nazw oznaczono jako niejednoznaczne. Dla 52 pozostałych wydarzeń nazwy nie są dopasowywane podobieństwem. Wśród poprawnie dopasowanych, już rozegranych wydarzeń nie wykryto braku wyniku tego samego dnia.

Poza zakresem pozostaje 95 wydarzeń z 8 nieobsługiwanych nagłówków: m.in. CONCACAF Nations League (13), kwalifikacje Pucharu Narodów Afryki (24), SAFF (5), King's Cup (2) oraz rozgrywki młodzieżowe i kobiece (51). Są pokazywane jako wymagające mapowania; nie są klasyfikowane jako seniorskie mecze reprezentacji.

W pierwotnym 72-godzinnym pomiarze kopii worker zobaczył 91 wydarzeń kandydackich: 34 z rozpoznanych rozgrywek i 57 z nieobsługiwanych nagłówków. Wtedy wszystkie 34 rozpoznane wydarzenia wymagały mapowania uczestników. Wynik: `stored=0`, `unmatched=34`, `invalid=0`, `unmapped_competition=57`. Nie powstały prognozy do rozliczenia: `forecasts=0`, `settled=0`. W tej kopii nie było wcześniejszego uruchomienia zadania `national_shadow`.

Neutralne boisko jest jawnie rozpoznawane po kraju stadionu. Brak potwierdzonego miejsca nie dodaje przewagi gospodarza. Niepewne wyniki po 90 minutach i konflikty źródeł są wykluczane: w historii oficjalnej wyłączono 12 niepewnych wyników, konfliktów było 0. Rynek benchmarkowy STS jest nieznany: tabela `benchmark_odds` w kopii zawiera 0 wierszy.

## Walk-forward 1X2

Okna dla oficjalnych spotkań: fit 2 197, kalibracja 232, selekcja 453, holdout 330. Warianty N1, N2, N3 i N_goals oceniane są bez wyboru na holdoucie. Mecze towarzyskie pozostają oddzielnym raportem: 117 w selekcji i 60 w holdoucie; ich wyniki nie wybierają wariantu oficjalnego. W badanej historii było 3 061 spotkań ze znanym miejscem, 151 z nieznanym oraz 437 na potwierdzonym neutralnym boisku.

| Wariant oficjalny | Selekcja log loss | Δ względem bazy, 95% CI | ECE selekcji | Holdout log loss / ECE |
|---|---:|---|---:|---:|
| Baza | 1,06855 | — | 0,01273 | 1,05325 / 0,00734 |
| N1 | 0,86803 | −0,20052 [−0,23158; −0,16806] | 0,04228 | 0,82901 / 0,04165 |
| N2 | 0,86391 | −0,20464 [−0,23645; −0,17176] | 0,04123 | 0,82436 / 0,03784 |
| N3 | 0,86418 | −0,20437 [−0,23591; −0,17106] | 0,04288 | 0,82397 / 0,04102 |
| N_goals | 0,84066 | −0,22789 [−0,28621; −0,16991] | 0,03739 | 0,80777 / 0,03996 |

Najniższy log loss w selekcji ma N_goals. Dwa warunki przechodzą: liczebność minimum 300 i przedział poprawy względem bazy poniżej zera. ECE wynosi 0,03739 przy limicie 0,03, więc rynek pozostaje **niezwalidowany**. ECE holdoutu również nie wpływa na werdykt. Żaden z wariantów N1–N3/N_goals nie spełnia progu ECE w selekcji. Towarzyskie oceniono osobno; w selekcji ECE dla N1/N2/N3 wynosi odpowiednio 0,03732 / 0,03388 / 0,04645.

Oprócz tych kryteriów badana wersja nie ma benchmarku STS na tych samych meczach. Ponieważ nie zmieniono polityki rejestru modeli, nawet przyszłe przejście kryteriów walk-forward nie wystarczy do rejestracji, dopóki nie będzie wymaganych obserwacji rynkowych.

## Worker, decyzja i Kupon dnia

Worker `national_shadow` jest domyślnie włączony co 6 godzin i działa na wcześniej zapisanych danych, bez pobierania nowych źródeł. Raportuje zapisane prognozy, powtórzenia, pominięcia, niedopasowania i nieprawidłowe wyniki. Każda prognoza ma wersję eksperymentu, wariantu i kodu, snapshot danych, czas informacji, prawdopodobieństwa 1X2 i status boiska. Powtórzenie jest idempotentne; nowa wersja eksperymentu lub kodu tworzy osobny wpis z powodem i odnośnikiem do poprzedniego wpisu.

Zgoda wyrażona w rozmowie nie została zapisana jako aktywna decyzja modelu. Wymagany jest aktualny, zwalidowany raport, wersja zatwierdzona przez niezmienioną politykę rejestru oraz append-only decyzja `national · 1X2 · FT_90` dla tej wersji. Aktualny model nie przeszedł ECE i benchmark STS jest nieznany, dlatego nie ma aktywnej wersji, nie wygenerowano prognoz do kuponu i nic nie zostało automatycznie włączone. Po spełnieniu bramek kandydat nadal podlega świeżości kursu, regule rozliczenia i istniejącym progom profilu. Nowe nogi przechowują `prediction_id`; stare rekordy nie są zmieniane.

Pomiary wykonano wyłącznie na lokalnych kopiach SQLite. Oryginalną bazę odczytano tylko przez SQLite online-backup API; nie otwierano jej przez aplikacyjny `Store` i nie zmieniano żadnego jej rekordu. Liczby kuponów, nóg kuponów, decyzji użytkownika i ustawień automatyzacji na pierwszej kopii przed/po pomiarze pozostały odpowiednio 31 / 85 / 6 / 3. W tej pierwszej kopii nie zapisano kuponu ani prognozy cieniowej. Nazw drużyn, nazw meczów i zrzutu bazy nie zapisano w tym raporcie.

## Uzupełnienie po zgłoszeniu braku propozycji — 12.11.2026

Przyczyną braku widocznych prognoz były polskie nazwy reprezentacji w ofercie STS, które nie miały jawnych aliasów do nazw użytych w historii wyników. Dodano obserwowane tłumaczenia tylko dla drużyn obecnych w danych; dopasowanie podobieństwem nadal nie tworzy prognozy. Prognozy cieniowe są teraz generowane również po kliknięciu „Analizuj mecze i generuj kupony”, a tabela Kuponu dnia pokazuje prawdopodobieństwa najlepszego wariantu w kolumnie „Prognoza cieniowa (1/X/2)”. Są opisane jako eksperymentalne i poza kuponami.

Na osobnej kopii aktualnej oferty z 12.11.2026 zapisano 32 obserwacje cieniowe — cztery warianty dla każdego z 8 mapowalnych seniorskich meczów tego dnia. Widok Kuponu dnia pokazał prognozy dla wszystkich 8. W tym 72-godzinnym oknie 26 dalszych wydarzeń wymagały mapowania nazw, a 57 były z nieobsługiwanych rozgrywek. Baza robocza użytkownika nie była zmieniana. Model pozostaje niezwalidowany, więc te wartości nie stają się typami kuponowymi.

## Testy i QA

- `tests/test_task017.py`: 19 testów przechodzi na danych syntetycznych, w tym mapowanie nazw polskich, wybór właściwego wariantu przy równym czasie, pozytywna bramka, odmowa bez benchmarku, wycofanie decyzji, niejednoznaczne aliasy, brak uczestników, idempotencja, porównywanie czasu między strefami oraz brak zmian kuponów.
- `tests/test_task009_international.py` i `tests/test_task015.py`: 36 testów przechodzi; 1 test AppTest został pominięty z powodu blokady lokalnego socketpair w Windows.
- Bieżąca regresja tych trzech plików: 55 zaliczonych, 1 pominięty.
- Ruff `check .` i `format --check .` przechodzą; `git diff --check` nie wykazuje błędów.
- Pełny `pytest` nie zakończył się: AppTest oraz Playwright zawieszają się podczas tworzenia pętli asyncio na `socket.socketpair()` / `accept` w środowisku Windows. Próba bez przypadków UI dotarła do TASK 014, gdzie proces zakończył się błędem dostępu NumPy podczas dopasowania modelu bramek. Nie przypisuję tego błędu zmianom TASK 017.
- `sports_edge.qa` uruchomione z zapisywalnym katalogiem tymczasowym również zatrzymało się przy AppTest z tym samym problemem socketpair. Test Playwright potwierdził tę samą blokadę przy inicjalizacji runtime.

## Ograniczenia i następny krok

Najbliższy praktyczny krok to ręcznie zweryfikować nazwy uczestników i dodać je do katalogu zatwierdzonych aliasów; nie należy uzupełniać ich fuzzy matchingiem. Następnie worker może zapisywać prospektywne obserwacje i rozliczać wyłącznie jednoznaczny, dostępny wynik 90-minutowy tego samego meczu. Do odblokowania rynku potrzebne są ponadto ECE ≤ 0,03 w oknie selekcji oraz benchmarki STS przedmeczowe na tej samej liczebności wymaganej przez bieżącą politykę rejestru. Kopia zawiera tylko jeden uchwycony terminarz, więc nie daje podstawy do wiarygodnej prognozy częstości przyszłych obserwacji ani terminu aktywacji.

`docs/task_016_report.md` nie istnieje; TASK 016 nie jest oznaczony jako ukończony. TASK 017 nie powiela jego zakresu. Nie twierdzi się, że pokryte są wszystkie federacje ani że model ma przewagę lub gwarantuje zysk.
