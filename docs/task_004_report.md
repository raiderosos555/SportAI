# Raport końcowy TASK 004 — 01.11.2026

**Wynik w jednym zdaniu:** aplikacja po jednym kliknięciu sama pobiera ofertę STS, uzupełnia
brakujące wyniki z legalnego źródła, generuje prognozy Elo i liczy EV. Dziś po raz pierwszy
pokazuje **rzeczywiste prognozy** (36 meczów 5 lig na 1–7.11) i ranking eksperymentalny, ale
nadal **nie ma dowodu przewagi**, więc wynik dnia to **BRAK POTWIERDZONEJ PRZEWAGI — BRAK
KUPONU DNIA**.

Zasady pracy:
- bez commitów, push i publikacji (§21) — zmiany są tylko w plikach roboczych;
- kopie zapasowe: pełna `backups/2026-11-01_08-36-04/` przed pracą oraz automatyczna
  `backups/pre-migration-v4_2026-11-01_11-37-13.db` przed migracją bazy v3 → v4;
- klucz football-data.org podany przez użytkownika zapisano wyłącznie w `.env` (plik w
  `.gitignore`, nigdy w repozytorium; aplikacja go nie wyświetla);
- nie użyto płatnych usług; zabezpieczeń stron (Cloudflare, robots.txt) nie obchodzono.

Rodzaje danych: **[H]** historyczne rzeczywiste (openfootball, football-data.org),
**[P]** prospektywne rzeczywiste (PDF STS z 31.10 i 01.11), **[S]** syntetyczne (wyłącznie testy).

## A. Diagnoza

**Dlaczego w TASK 003 było 0 prognoz** — przyczyny rozdzielone na kategorie:

| Kategoria | Przyczyna | Stan po TASK 004 |
|---|---|---|
| 1. Błędy techniczne | Analiza AI pokazywała dla rynków tylko „1X2”, bez okresu: dla Getafe – Sevilla jako 1X2 widniały 1848 („Mecz”), 61614 („1. połowa”), 45799 („2. połowa”) i 45798 („1. połowa lub wynik końcowy”). Parser był poprawny (sprawdzono w oryginalnym PDF), błąd leżał w prezentacji | Rynek zawsze z okresem i linią, numerem zakładu i oryginalnym opisem z PDF; test regresji (syntetyczny + autentyczny PDF) |
| 1. Błędy techniczne | Jeden status „nieaktualny” mieszał wiek dokumentu PDF ze stanem wydarzenia | Trzy niezależne statusy (sekcja „Świeżość”) |
| 1. Błędy techniczne | „Uruchom analizę” jedynie przeliczała EV na istniejących prognozach | 8 etapów od oferty do zapisu (sekcja F) |
| 1. Błędy techniczne | Wyniki z CSV były zapisywane z etykietą „openfootball” | Zapisywane jest rzeczywiste źródło |
| 2. Brak danych | Brak źródła wyników dla lig poza 5 czołowymi i dla innych sportów | Nadal: 6858 z 6903 selekcji 01.11 nie ma modelu (sekcja H) |
| 3. Przestarzałe dane | Ostatni wynik openfootball: 20.09.2026; luka 42 dni przy progu 21 dni | Rozwiązane źródłem football-data.org; ostatni wynik we wszystkich 5 ligach: **31.10.2026** |
| 4. Brak modelu | Wyłącznie Elo dla 1X2 (czas regulaminowy) w 5 ligach | Bez zmian — zgodnie z zasadą „najpierw podstawowy proces” |
| 5. Brak niezależnej walidacji | Brak legalnych kursów historycznych tych samych meczów | Bez zmian — status `experimental` |
| 6. Brak potwierdzonej przewagi | Konsekwencja punktów 4–5 | Bez zmian |

**Dlaczego kursy były „nieaktualne”:** wiek liczono od daty dokumentu (próg 180 min).
Dokumenty z 31.10 21:15 miały rano 01.11 już ponad 10 h. Do tego plik STS „na dziś” 01.11 był
**pusty dwukrotnie** (06:45 i 11:35 UTC; 0 wierszy). Hipoteza, niezweryfikowana: 1 listopada to
święto, a wydruki ofert służą punktom stacjonarnym. Teraz taki kurs ma status „Kurs
niepotwierdzony od X h” i nie blokuje wydarzenia, które jeszcze się nie zaczęło. Nigdy jednak
nie przechodzi walidacji, a pusty PDF niczego nie potwierdza.

**Dlaczego Kupon dnia nie miał kandydatów:** nie było żadnej prognozy, więc nie było EV.

**Rozwiązane problemy:**
- aktualne wyniki;
- automatyczny proces analizy;
- statusy kursów;
- prezentacja rynków;
- czytelna analiza wydarzenia;
- rozliczenia z rozróżnieniem „czeka na dane”;
- pochodzenie wyników;
- 2 błędy wykryte podczas implementacji: identyczna odpowiedź źródła blokowała import po
  potwierdzeniu aliasu, a druga obserwacja tego samego meczu kolidowała z kluczem wiersza.

## B. STS RESULTS

**Czy znaleziono dozwolone źródło wyników STS:** nie, nie ma dozwolonego automatycznego źródła.

| Pytanie | Ustalenie (01.11.2026) |
|---|---|
| Formaty JSON/XML/CSV/PDF | Wyniki są tylko na stronie (Regulamin R 14.5: „po przetworzeniu danych” STS publikuje wyniki zakończonych wydarzeń). PDF istnieje wyłącznie dla oferty |
| Udokumentowane API | Brak |
| Bez logowania | Strony wyników nie zidentyfikowano: `sts.pl/wyniki` przekierowuje na stronę główną, a rozliczenia własnych zakładów są na koncie klienta |
| Legalna automatyzacja | Nie: `robots.txt` blokuje adresy z `resultDate`; „Statystyki” (stats.sts.pl) przekierowują do Sportradar statshub, którego `robots.txt` zabrania wszystkiego |
| Zakres historii | Regulamin nie podaje go dla zakładów sportowych (5 lat wyłącznie dla sportów wirtualnych, R 12.8) |
| Opóźnienie wyniku | Nieustalone |
| Gole, kartki, rożne | Tylko pośrednio, przez rozliczone kupony na koncie — bez automatyzacji |
| Identyfikatory wydarzeń | W PDF jest numer zakładu; publicznych identyfikatorów wydarzeń brak |
| Przechowywanie lokalne / trening modeli | Regulamin tego nie reguluje; brak licencji. Wniosek: tylko ręczny wpis do rozliczeń |

R 14.6 posłużył jako wzór hierarchii przy rozbieżnościach: dostawca danych, potem oficjalni
organizatorzy (UEFA, FIFA), wiodące agencje i portale, na końcu nagranie wideo.

**Adapter:** `StsResultsProvider` jest **nieaktywny** i podaje powód. Wynik STS wpisuje się
ręcznie (Historia i wyniki) albo jako CSV „z oficjalnej strony organizatora”.

## C. SUPERBET RESULTS

- `www.superbet.pl/robots.txt` blokuje dla wszystkich robotów: `/wyniki`, `/tabela`, `/analizy`,
  `/statystyki-graczy` i każdy adres z parametrami; boty AI (np. AI2Bot) są zablokowane w całości.
- Nie ma udokumentowanego API wyników ani eksportu. Regulaminu serwisu dotyczącego
  automatycznego pobierania nie odnaleziono. Superbet ma osobną aplikację Superscore.
- Wyniku wydarzenia nie da się więc legalnie pobierać automatycznie, a rozstrzygnięcia zakładów
  (kartki, rożne, rozliczenia) tym bardziej.
- **Adapter `SuperbetResultsProvider` pozostał nieaktywny** z zapisanym powodem.
- Do aplikacji nie wróciło nic innego z Superbet: kursy, porównania, kupony, profil podatkowy ani
  zakładka.

## D. INNE ŹRÓDŁA

| Źródło | Legalność | Aktualność | Zakres i statystyki | Automatyzacja | Historia | Koszt | Decyzja |
|---|---|---|---|---|---|---|---|
| **football-data.org v4** | Warunki publiczne (atrybucja, 1 klucz na aplikację) | Bieżące; znacznik `lastUpdated` | 12 rozgrywek piłkarskich (5 lig TOP, bez Ekstraklasy); wynik i do przerwy | API, 10 zapytań/min | Bieżący sezon | 0 zł | **Wdrożone — bieżący sezon** |
| openfootball | CC0 | Bez wyników po 20.09.2026 (sprawdzono też repozytoria tekstowe) | Piłka | Pliki JSON | 2012– | 0 zł | Historia |
| API-Sports | Regulaminu nie dało się odczytać (Cloudflare) | Bieżące | Wiele dyscyplin, statystyki, kursy; w darmowym planie tylko ostatnie sezony | API, 100 zapytań/dzień na API | Ograniczona w planie darmowym | 0 zł (darmowy) | Kandydat na TASK 005 |
| STS | Brak API; robots.txt blokuje wyniki po dacie | Po rozliczeniu | Rynki STS | Niedozwolona | — | — | Nieaktywne, wpis ręczny |
| Superbet | robots.txt blokuje `/wyniki` | — | — | Niedozwolona | — | — | Nieaktywne |
| Flashscore | Regulamin zakazuje pobierania i kopiowania bazy | Na żywo | Szeroki | Niedozwolona | — | Licencja B2B | Wykluczone |
| football-data.co.uk, tennis-data | Ograniczenia użycia przez boty i AI | — | Wyniki i kursy | — | Długa | — | Wykluczone |

**football-data.org v4 w szczegółach:**
- **Aktualność:** pobranie 01.11 11:37 UTC zwróciło wyniki do 31.10.
- **Rozgrywki i sezon:** 12 rozgrywek Free Tier (`/coverage`), w tym bieżący sezon 5 lig.
- **Limity:** 10 zapytań na minutę po rejestracji (bez klucza wyłącznie listy, 100 na dobę).
- **Przechowywanie i AI:** warunki (aktualizacja 2018-06-01) nie regulują przechowywania ani
  użycia w modelach. To ryzyko akceptowalne tylko przy prywatnym, niekomercyjnym użyciu, a po
  rezygnacji z usługi danych nie wolno pokazywać.
- **Uwierzytelnienie:** nagłówek `X-Auth-Token`.
- **Godziny i statusy:** `utcDate` oraz 9 statusów. `lastUpdated` jest górną granicą dostępności
  wyniku i jest odrzucany, jeśli wypada wcześniej niż 95 minut po rozpoczęciu.

**API-Sports a football-data.org** (pytanie użytkownika): API-Sports nie jest lepsze jako
zamiennik dla 5 lig:
- 100 zapytań na dobę wobec 10 na minutę;
- darmowy plan obejmuje tylko ostatnie sezony;
- regulaminu nie zweryfikowano.

Jest za to najlepszym kandydatem do **rozbudowy**: Ekstraklasa i inne ligi, koszykówka,
siatkówka i hokej, a także statystyki meczu (rożne, kartki) potrzebne do rozliczania
dodatkowych rynków. Uwaga: 100 zapytań na dobę wystarcza na dzienne aktualizacje kilku lig, ale
nie na uzupełnianie historii, a pokrycia Ekstraklasy nie potwierdzono.

**Przyjęte rozwiązanie:**
- openfootball — historia;
- football-data.org — bieżący sezon;
- wpis „oficjalny organizator” — najwyższy priorytet;
- STS i Superbet — nieaktywne.

Hierarchia przy rozbieżnościach: oficjalny organizator > football-data.org > openfootball > CSV.

**Architektura** (`datasources/base.py`, `results_engine.py`):
- wspólny kontrakt `SportsResultsProvider` i standardowy `ResultRecord`;
- tylko brakujący okres z 3-dniowym zakładem na korekty; pierwsze uzupełnienie sezonu to
  jedno zapytanie na ligę;
- idempotencja: identyczne wiersze są pomijane, a identyczną odpowiedź przetwarza się rekord po
  rekordzie;
- ponowienia z przerwą przy błędach sieci, 429 i 5xx; odmowa 401/403 kończy pobieranie od razu;
- limiter zapytań;
- surowa odpowiedź zapisana lokalnie z SHA-256, a raport w `data_imports`;
- dane tylko dopisywane: korekta i druga obserwacja to nowe wiersze, a konflikt widać w
  interfejsie;
- migracja v4 dodaje wyłącznie nowe kolumny.

## E. MODELE

| Miara | Wynik [H] |
|---|---|
| Nowe mecze z football-data.org | **181** (en.1 38, de.1 34, es.1 35, it.1 43, fr.1 31); 5 zapytań |
| Potwierdzenia drugim źródłem | 50 meczów z okresu nakładania (17–20.09): pełna zgodność z openfootball, **0 konfliktów** |
| Nierozpoznane nazwy, niepełne rekordy, błędy | 0 / 0 / 0 |
| Ostatni wynik w lidze | Premier League, Bundesliga, LaLiga, Serie A, Ligue 1: **31.10.2026** |
| Prognozy przedmeczowe | **36**: 15 (01.11), 5 (02.11), 3 (06.11), 13 (07.11) |
| Pominięte | 3 mecze beniaminków z historią krótszą niż 10 meczów: Everton–Coventry, PSG–Troyes, Dortmund–Elversberg |
| Model | `elo-1x2-…-202611010647` bez ponownego treningu (hiperparametry z walidacji; ratingi aktualizuje odtworzenie historii as-of) |
| Walidacja (bez zmian) | holdout n=3733: log loss 0,9914, Brier 0,5915, ECE 0,0063; klimatologia 1,0746 / 0,6504 |
| Status | `experimental`: brak porównania z rynkiem na tych samych meczach |

**Ograniczenia:**
- Elo widzi wyłącznie wyniki: bez składów, kontuzji i terminarza.
- Beniaminkowie mają nieaktualne ratingi.
- Próg 21 dni może zablokować prognozy po przerwie zimowej, mimo kompletnych danych
  (do zmiany w TASK 005).
- Rozbieżności z rynkiem są systematycznie na korzyść gospodarzy, np. Atalanta–Parma: model
  80,3%, rynek bez marży 61,4%. To sygnał ostrożności, nie przewagi.

## F. KUPON DNIA

Pełny proces („Uruchom analizę” lub `sports-edge analyse`) przechodzi 8 etapów:
1. **Oferta STS** — data dokumentu i jego wiek; ewentualne pobranie oficjalnego PDF.
2. **Dane sportowe** — uzupełnienie brakującego okresu i raport aktualizacji.
3. **Modele** — bez zbędnego treningu; tylko brakujące prognozy.
4. **Dopasowanie** — aliasy i identyfikatory; rynek i okres.
5. **Matematyka** — prawdopodobieństwa, EV brutto i netto, niepewność, podatek STS.
6. **Walidacja** — jakość danych, status modelu, świeżość, reguły, polityka.
7. **Wynik** — rankingi i diagnoza.
8. **Zapis** — analiza, wersje modeli, migawki.

Postęp jest widoczny na pasku, z liczbami dla każdego etapu.

| Miara [P] | 01.11.2026 | 02.11.2026 |
|---|---:|---:|
| Wydarzenia w ofercie | 764 | 261 |
| Poprawnie odczytane rynki | 2637 / 2640 | 1141 / 1142 |
| Wydarzenia z aktualnymi statystykami | 15 | 5 |
| Rynki obsługiwane przez model | 15 | 5 |
| Przeanalizowane selekcje | 6903 | 2897 |
| Z prognozą | 45 | 15 |
| Kandydaci EV+ | 8 | 3 |
| Kandydaci eksperymentalni | 8 | 3 |
| **Zwalidowani** | **0** | **0** |

**Powody odrzuceń 01.11** (grupowane):
- kurs niepotwierdzony (dokument STS sprzed 14 h; plik „na dziś” pusty): 6903;
- brak prognozy modelu: 6858;
- niezweryfikowane reguły rynku STS: 1355;
- wydarzenie rozpoczęte: 1289;
- kurs powyżej limitu polityki: 521;
- dolna granica EV netto ≤ 0,02: 40;
- EV netto ≤ 0: 37.

**Powody odrzuceń 02.11** (dokument STS świeży):
- brak prognozy: 2882;
- niezweryfikowane reguły: 822;
- kurs powyżej limitu: 215;
- dolna granica EV: 14;
- EV netto ≤ 0: 12.

Każdy kandydat EV+ jest eksperymentalny z dwóch powodów: model nie jest zwalidowany, a dolna
granica EV netto nie przekracza progu 0,02. Komunikat „Brak potwierdzonej przewagi” nie jest
już jedynym wyjaśnieniem. Aplikacja pokazuje osobno:
- brak danych sportowych;
- brak prognoz;
- brak modelu dla danego rynku (2625 z 2640);
- brak walidacji;
- brak dodatniego EV;
- brak przewagi.

AKO pozostaje wyłączone. ROI: brak rozliczonych kuponów.

**Świeżość — trzy niezależne statusy:**
- **wydarzenie:** przed rozpoczęciem / w trakcie / zakończone / przełożone / anulowane / nieznany;
- **kurs:** świeża migawka / „Kurs niepotwierdzony od X h” / nieznany czas;
- **obecność rynku:** potwierdzony / wycofany / niepotwierdzony.

Przy każdym kursie widać datę dokumentu, datę pobrania, wiek migawki, godzinę startu i
informację, że ofertę można pobrać ponownie.

**Rozliczenia paper trading:**
- wyniki pochodzą z silnika, tylko przy pewnym dopasowaniu i bez rozbieżności między źródłami;
- przełożenie meczu podane przez źródło zmienia status wydarzenia STS;
- rynki, których nie rozstrzyga wynik końcowy (rożne, kartki, czerwona kartka, połowy bez
  wyniku do przerwy, linie całkowite), czekają z opisem przyczyny i nigdy nie są liczone jako
  przegrane.

## G. TESTY

- **pytest: 171 passed.** 69 testów TASK 001 jest bez zmian, 102 to testy TASK 002–004, w tym
  18 nowych w `tests/test_task004.py` i 6 w `tests/test_api_sports.py` (sekcja I).
- **Ruff:** `check` i `format --check` bez uwag.
- **Nowe testy obejmują:**
  - parser football-data.org (statusy, czas regulaminowy, niespójne `lastUpdated`);
  - ponowienia oraz odmowę 403 bez ponawiania;
  - limiter zapytań;
  - klucze tylko lokalnie i nieaktywne adaptery STS i Superbet;
  - okno brakujących danych i idempotencję;
  - potwierdzenia, konflikty i hierarchię źródeł;
  - korekty i widok as-of (przyszłość niewidoczna);
  - trwałość danych (UPDATE odrzucony);
  - przerwane pobieranie i wznowienie;
  - niepełne rekordy, rekordy z przyszłości i niedostępne;
  - alias potwierdzony przez użytkownika;
  - nieaktualną historię Elo i prognozy po uzupełnieniu (bez duplikatów);
  - rozdzielone statusy;
  - rynek „niepotwierdzony”;
  - rozróżnienie połów i rynków 1X2 (syntetyczny PDF i autentyczny Getafe – Sevilla);
  - rozliczenia czekające na dane;
  - przełożenie meczu;
  - blokadę przy konflikcie źródeł;
  - **pełny przepływ** PDF STS → aktualizacja wyników → baza → Elo → prognoza → EV → ranking →
    paper → wynik → rozliczenie → raport JSON [S];
  - przepływ bez aktualnych danych z diagnozą.
- **Dwa testy TASK 003 dostosowano do wymagań:**
  - eksport sprawdza rzeczywiste źródło wyniku, nie etykietę „openfootball”;
  - stary kurs ma komunikat „Kurs niepotwierdzony od 5 h” i poziom eksperymentalny.

  Żadnego testu nie usunięto.
- **Integracja na prawdziwych danych:**
  - rzeczywista aktualizacja wyników (5 zapytań);
  - 36 prognoz;
  - 2 analizy (01.11 i 02.11);
  - render wszystkich 7 stron na kopii prawdziwej bazy, z kliknięciem „Uruchom analizę” w
    trybie offline.
- **Uwaga:** przejście testów to dowód działania, nie rentowności.

## H. TASK 005

**Najważniejsze braki:**
1. Brak kursów historycznych tych samych meczów — bez nich nie ma walidacji rynkowej.
2. Brak gęstych migawek STS tuż przed meczem; plik „na dziś” bywa pusty.
3. Model tylko dla 1X2 w 5 ligach; brak źródła dla Ekstraklasy i innych sportów.
4. Beniaminkowie i przerwa zimowa (reguła 21 dni).

**Proponowane eksperymenty:**
1. **Prospektywny test Elo vs STS:**
   - protokół zarejestrowany i zamrożony przed startem;
   - prognozy już zapisują się przed meczem;
   - migawki oferty STS około 24 h i 1 h przed meczami;
   - ocena: log loss modelu vs STS bez marży na tych samych meczach;
   - minimum 300 meczów, czyli około 6–7 tygodni pięciu lig.
2. Kompletność terminarza zamiast samego progu 21 dni (dane z football-data.org zawierają
   terminarz).
3. Adapter API-Sports (po sprawdzeniu regulaminu przez użytkownika): Ekstraklasa, koszykówka,
   siatkówka, hokej, statystyki rożnych i kartek.
4. Rating startowy beniaminków.
5. Dopiero potem Poisson / Dixon–Coles dla sum goli i BTTS.

**Gotowość do prospektywnego badania przewagi nad STS:** technicznie tak:
- prognozy przed startem z wersją, snapshotem danych i stanem kodu;
- historia kursów STS;
- automatyczne wyniki i rozliczenia.

Warunek konieczny: regularne pobieranie oferty STS blisko meczów (na żądanie, po potwierdzeniu
warunków) i kilka tygodni zbierania danych.

## I. Rozszerzenie: API-Sports i ranking prawdopodobieństwa wygranej

**API-Sports** (`datasources/api_sports.py`; klucz użytkownika `API_SPORTS_KEY` w `.env`):
- **Piłka (v3, aktywne po podaniu klucza):** `/fixtures?league&season&from&to`, czyli
  Ekstraklasa (`pl.1`, nieobecna w Free Tier football-data.org) i 5 lig jako źródło zapasowe.
  Wynik to czas regulaminowy (`score.fulltime`); dogrywka i karne trafiają do szczegółów.
- **Limit:** 100 zapytań na dobę na API, z rezerwą 10. Licznik zapisuje się lokalnie i zawsze
  wygrywa nagłówek `x-ratelimit-requests-remaining`, jeśli serwer go poda. Do tego limit 10
  zapytań na minutę, ponowienia i stronicowanie.
- **Błędy:** API zwraca je w treści odpowiedzi z kodem HTTP 200, np. „plan nie obejmuje
  sezonu”. Adapter zatrzymuje się wtedy z komunikatem dostawcy.
- **Dostępność wyniku:** API nie ma znacznika aktualizacji. Obowiązuje reguła silnika:
  założenie +36 h, ale nigdy później niż moment, w którym aplikacja sama zobaczyła wynik
  końcowy (`observed_at_fetch`).
- **Pierwsze źródło nowej ligi** (Ekstraklasa nie ma historii w openfootball) wyznacza nazwy
  kanoniczne (`source`). Kolejne źródła muszą się do nich dopasować.
- **Weryfikacja na kluczu użytkownika (01.11.2026)**, łącznie 15 zapytań: konto w planie
  Free, aktywne do 01.11.2027, limit 100 zapytań na dobę.
- **Darmowy plan nie obejmuje bieżącego sezonu** w żadnym sprawdzonym API. Ekstraklasa,
  5 lig, NBA, Euroliga i NHL zwróciły: „Free plans do not have access to this season, try
  from 2022 to 2024.”
  - Adapter zatrzymał się na tym komunikacie i niczego nie zapisał.
  - Odmowa jest zapamiętana na 7 dni (`PLAN_RECHECK_DAYS`): dostawca jest wtedy nieaktywny i
    podaje powód, więc aktualizacje nie zużywają limitu.
  - **Wniosek:** bieżące wyniki z API-Sports wymagają płatnego planu, co jest decyzją
    użytkownika. W darmowym planie Ekstraklasa nadal nie ma bieżących wyników.
- **Koszykówka i hokej** — schemat zweryfikowany na dozwolonych sezonach:
  - NBA: liga 12, sezony zapisane jako „2023-2024”, 1377 meczów, w tym 66 po dogrywce;
  - Euroliga: liga 120, sezony zapisane jako rok;
  - NHL: liga 57, sezony zapisane jako rok, 1514 meczów, w tym 314 po dogrywce lub karnych.

  Wynik regulaminowy to suma kwart lub tercji, a dogrywka trafia do `after_ot`. W danych NHL
  były **3 niespójne rekordy** (np. wynik 2:4 przy sumie tercji 2:3). Kontrola spójności
  odrzuca je, więc nie trafiają do historii ani do rozliczeń. Siatkówka pozostaje
  niezweryfikowana.
- **Regulamin API-Sports** jest za zabezpieczeniem Cloudflare; użytkownik akceptuje go przy
  rejestracji.

**Ranking „Najwyższe prawdopodobieństwo wygranej”** (Kupon dnia, próg z polityki
`probable_min_probability = 0,65`):
- Ranking jest informacją, nie bramką walidacji i nie rekomendacją.
- Podwójna szansa (1X, X2, 12) jest liczona z tej samej prognozy 1X2. Przedziały niepewności
  sumują się zachowawczo, a prawdopodobieństwo rynkowe pochodzi z 1X2 STS bez marży.
- Przy każdym typie widać:
  - prawdopodobieństwo modelu i rynku;
  - **próg opłacalności po podatku**: 1 / (0,88 × kurs);
  - EV netto;
  - kalibrację modelu w danym przedziale (holdout).
- **Dane rzeczywiste 02.11 [P]:** 11 typów z P ≥ 65%, w tym **tylko 2 z dodatnim EV netto**.
  Przykład: Atalanta – Parma 1X @ 1,16 — model 93,1%, rynek 83,2%, wymagane 98,0%, więc
  oczekiwana strata. Przy kursie poniżej 1,14 trafienie nie zwraca nawet stawki.
