# Raport końcowy TASK 001 — 31.10.2026

Zrealizowano fundamenty projektu w `G:\SportAI`. Nie wykazano przewagi ekonomicznej:
nie przeprowadzono empirycznego backtestu ani przyszłego paper. Działające demo
korzysta z danych syntetycznych i zwraca brak rekomendacji.

## A. Architektura

Modułowy monolit Python, wspólne modele danych i arytmetyka, osobne adaptery źródła,
sportu i reguł rynku. Docelowo Parquet/DuckDB do analiz, SQLite do rejestru;
PostgreSQL/FastAPI/Streamlit opcjonalnie później. [Schemat i struktura](architecture.md).

Przepływ: **źródła → raw/hash/czas → jakość → dane kanoniczne → cechy as-of →
model i benchmark rynku → EV netto → decyzja → replay/paper → walidacja**.
Rejestr hipotez oraz wersje kosztów/reguł sterują eksperymentem.

## B. Istniejące rozwiązania

[Audyt](existing_research_audit.md) obejmuje Market Efficiency Lab, penaltyblog,
NBA ML Sports Betting, flumine i publikacje: konsensus kursów Kaunitza,
replikacja WikiBuzz Clegga/Cartlidge'a, Dixon–Coles, ForecastBench i WC2026-Agents.

Deklarowane MLB +9,3%/1215 selekcji Market Efficiency Lab nie zostało przez nas
odtworzone. Kod opisuje pozostałe przecieki; cztery roczne klastry ograniczają pewność.
Wyniki autora, publikacje, cudze replikacje, symulacje i deklarowane realne zakłady
są rozdzielone. Nie ma naszej niezależnej replikacji rentowności.

Penaltyblog to kandydat do przyszłego baseline; flumine to wzorzec symulacji.
Licencje MIT tych bibliotek nie obejmują danych dostawców. Nie skopiowano obcego kodu.
Przykład korekty WikiBuzz pokazuje, dlaczego jeden błędny kurs może zdominować zysk.
Liczby, zakresy i pierwotne źródła są podane przy każdej pozycji audytu.

## C. Dane

[Porównanie](data_sources.md) zawiera wszystkie wskazane źródła i dodatkowy tenis,
publiczne zbiory oraz źródła oficjalne. Kandydat kursowy: The Odds API, free do pilota
schematu/pokrycia, historia tylko płatna. Publiczny cennik zaczyna się od 30 USD/mies.
Nie kupiono planu. Wyniki/terminarze: football-data.org po doprecyzowaniu praw;
StatsBomb tylko do pokrytych meczów i dopuszczonych badań.

Nie opieramy treningu na football-data.co.uk bez właściwych praw. API-Football ma
krótką historię kursów; SportsDataIO trial zmienia wartości i nie nadaje się do ROI.
Luki: timestampy i korekty, oferta/reguły STS, limity i osiągalność kursów, nisze/props,
koszt pełnej historii oraz warunki przechowywania/redystrybucji.

## D. Metodologia

Nested walk-forward, osobny dobór progów/kalibracji, nietknięty holdout, rejestr
wszystkich prób i prospektywne potwierdzenie. Dostępność cech ustalana as-of;
historyczne LLM to eksploracja. Bootstrap zachowuje wydarzenia i bloki czasu,
wielokrotność testów podlega korekcie. CLV jest diagnostyką, nie dowodem netto.

Cash flow rozdziela stawkę, podatek, stawkę efektywną, zwrot, wypłatę, podatek wygranej,
prowizję i fee. Profil PL/STS jest wąski i datowany; nieustalone przypadki są blokowane.
CI obejmujące zero lub brak mocy daje „brak wystarczających dowodów”.
[Metodologia](research_methodology.md), [leakage](leakage_prevention.md),
[protokół](experiment_protocol.md), [podatki](tax_and_settlement.md).

## E. Implementacja i weryfikacja

Utworzono 12 wymaganych dokumentów (README + 11 tematycznych) oraz ten raport,
`pyproject.toml`, `.gitignore`, `.env.example`, workflow CI, manifest pakietu,
przypięte wersje narzędzi, katalog rynków i profil kosztów.

Kod zawiera **16 wymaganych modeli domenowych** i bazową Entity, walidację czasu,
rozkłady/devig, EV binarne i pełnych wypłat, koszty, 6 stanów rozliczenia, dwa osobne
adaptery wyniku, TOML, kontrole jakości, porty przyszłych modułów i CLI.
Pełne ingestion, backtester, trwały paper ledger, ML i dashboard są zaprojektowane,
nie zaimplementowane.

| Sprawdzenie | Wynik lokalny |
|---|---|
| Środowisko | Windows, Python 3.12.10, pytest 9.2.0 |
| Testy | **69 passed**, bez pominięć |
| Ruff | Kontrola kodu i formatowania przechodzą |
| Instalacja editable | Poprawna, bez zależności runtime |
| CLI demo | EV brutto 0,1; netto −0,0320; brak rekomendacji |
| Build | Wheel i sdist zbudowane lokalnie |
| CI GitHub | Przygotowane, nie uruchomione zdalnie |
| Dane/płatne usługi | Nie pobrano datasetów, nie aktywowano usług |

Problem uprawnień do sandboxowego katalogu tymczasowego rozwiązano przez lokalny
`.tmp` w sesji procesu. Instrukcja instalacji i odtwarzania wersji: [README](../README.md).

```powershell
Set-Location G:\SportAI
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\sports-edge demo
```

Na początku `git status` wykazał brak repozytorium. Warunek commitu w istniejącym
repo nie miał zastosowania: nie utworzono commitu i nie wykonano push. Proponowany
komunikat przy dodaniu do Git: `feat: initialize multi-sport edge research architecture`.
Przegląd plików nie wykazał sekretów ani ograniczonych licencyjnie datasetów;
fixture są syntetyczne. Szczegółowe ograniczenia: [ryzyka](risks_and_limitations.md).

## F. Kolejne eksperymenty

Warunkowy ranking wykonalności: jedna liga futbolowa 1X2 → podstawowe NBA/MLB →
ATP/WTA → mniejsze ligi → props → nisze wymagające dodatkowego audytu. To ocena
projektowa, nie ranking zysków. Bramki praw i czasu mają pierwszeństwo.

Pierwsza hipoteza: konsensus pozostałych operatorów a cena jednego operatora,
T−60, stała stawka, pełne koszty. Dane: trzy kursy 1X2 z tego samego czasu,
snapshoty, terminarz/korekty, wynik regulation, closing i wersja zasad.
Sukces techniczny: lineage i odtwarzalność. Sukces badawczy: niezależna dodatnia
dolna granica CI netto po korektach i stresie kosztów. Brak mocy/CI przez zero:
inconclusive; niewiarygodne dane: wynik nieważny. [Szczegóły](market_selection.md).

## G. TASK 002

Mały legalny pilotaż ingestion jednej ligi/rynku, manifesty, mapowania/kwarantanna,
SQLite registry i append-only ledger, raport jakości oraz kosztorys przyszłego zbierania.
Płatny zakup dopiero po przedstawieniu konkretnej oferty do zgody. Bez zaawansowanego
ML i realnego obstawiania. TASK 002 opisano w [roadmapie](roadmap.md), nie wdrożono.

Odbiór dotyczy fundamentów: architektury, audytu, danych, metodologii, modeli,
minimalnych obliczeń z testami i lokalnego uruchomienia. Nie stanowi potwierdzenia
przewagi żadnej strategii.
