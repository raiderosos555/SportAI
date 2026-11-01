# Roadmapa

## TASK 001 — wykonany zakres fundamentów

Audyt źródeł i badań, architektura, metodologie, modele domenowe, deterministyczna
arytmetyka i adaptery przykładowe, walidacje, testy i dokumentacja uruchomienia.
Nie wykonano eksperymentu empirycznego i nie znaleziono potwierdzonej przewagi.

## Propozycja TASK 002 — kontrolowany pilotaż danych

Cel: legalna, niewielka próbka jednej ligi i regulation 1X2, nadająca się do
audytu dostępności w czasie, bez budowy zaawansowanych modeli.

1. Zweryfikować uprawnienia/warunki wyników i kursów, pokrycie operatorów oraz koszt.
   Przed płatnym planem przedstawić konkretną próbkę, limit pobrania i cenę do akceptacji.
2. Adapter The Odds API + wybrane źródło wyników, domyślnie offline fixtures i dry-run.
   Jawny limit zapytań, redakcja kluczy, retry, źródłowe timestampy, manifest raw/hash.
3. Kanoniczne mapowanie event/participant/bookmaker/market, kwarantanna konfliktów,
   raport braków i ręczna kontrola małej próbki. Zero wymyślonych timestampów/kursów.
4. SQLite registry hipotez i append-only dziennik obserwacji/decyzji, test odmowy UPDATE,
   korekty przez nowy rekord. Parquet/DuckDB dla analizy i lokalne komendy CLI.
5. Kosztorys przyszłego prospektywnego zbierania i prerejestracja EXP-002 po pomiarze
   pokrycia. Dla historii dopiero zweryfikowany adapter archive-as-of, bez omijania kontroli czasu.

Odbiór: dwa przebiegi dają te same rekordy/hash (z pominięciem rzeczywistego nowego
czasu pobrania w nowym manifeście), przetestowane restarty/idempotencja, jawne braki,
żadnych sekretów w logach, prawa i koszt w raporcie. Brak datasetu/zgody na zakup
nie uprawnia do obejścia źródła. **TASK 002 nie został zaimplementowany w TASK 001.**

## Dalsze bramki

TASK 003: mały deterministyczny replay, benchmark rynku i konsensus, jednostkowe
testy czasu/foldów i pełnych przepływów; zarejestrowany eksperyment. TASK 004: przyszły
paper, wyniki/CLV, niezmienność decyzji, CI. TASK 005: Elo/DC/logistic na legalnych
cechach, porównanie z bazą. TASK 006: nowe sporty/props, LLM/ensemble jako osobne
hipotezy i niewielki dashboard. Wynik negatywny może zakończyć linię badań.
Rzeczywiste obstawianie nie jest elementem tej roadmapy.

## TASK 002 — stan (31.10.2026)

Zmieniony zakres na żądanie użytkownika: lokalna aplikacja STS/Superbet zamiast pilotażu
The Odds API. Etap A (aplikacja, SQLite, import PDF, normalizacja, porównanie) ukończony
i sprawdzony na autentycznych PDF STS. Etapy B–D mają działający kod (pobieranie STS na
żądanie, adapter ArbiScan, import wyników CSV, Elo, EV netto, kupon SOLO/AKO, paper, ROI,
CLV), ale bez danych niezależnych i bez zwalidowanego modelu — aplikacja nie tworzy kuponu.
Proponowany TASK 003: [raport TASK 002, sekcja 15](task_002_report.md).

## TASK 003 — stan (01.11.2026)

Na żądanie użytkownika aplikacja obsługuje wyłącznie STS; Superbet zarchiwizowano bez utraty
danych (migracja v3 z kopią). Usunięto przecieki czasowe w prognozach (tylko przed startem,
as-of, walk-forward po dniach, wersjonowanie danych i kodu). Pierwszy eksperyment na danych
rzeczywistych — Elo 1X2 na 24 330 meczach 5 lig (openfootball, CC0) — jest wyraźnie lepszy od
klimatologii na holdoucie, ale bez legalnych kursów tych samych meczów nie ma porównania
z rynkiem, więc model pozostaje `experimental`, a aplikacja nie tworzy kuponu. Dane kończą się
20.09.2026, dlatego prognozy dla bieżącej oferty są wstrzymane (próg 21 dni).
Proponowany TASK 004 (aktualne wyniki, kursy do walidacji, prospektywny test Elo vs STS):
[raport TASK 003, sekcja F](task_003_report.md).
