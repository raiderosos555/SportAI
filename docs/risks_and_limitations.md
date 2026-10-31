# Ryzyka i ograniczenia

| Ryzyko | Skutek | Reakcja i status |
|---|---|---|
| Brak przewagi / rynek silniejszy | Dodatnie modelowe EV bez realnego zysku | Benchmark, holdout, prospektywny test; obecnie brak dowodów |
| Koszt stawki i wygranej | ROI brutto mylący | Oddzielny cash flow i konfiguracja dat/jurysdykcji |
| Późniejsze informacje/korekty | Fałszywy wynik backtestu | As-of, archiwum, kwarantanna; pełny replay jeszcze nie istnieje |
| Stary/błędny kurs, pojedynczy outlier | Zysk oparty na niewykonalnej ofercie | TTL, audyt największych zysków, porównania źródeł, brak maksymalizacji ex post |
| Nieznana płynność/limity/odmowy | Brak skalowalności | Paper nie dowodzi fill; ekonomia wykonania musi mieć osobny test |
| Wiele selekcji na mecz | Zawyżona efektywna próba | Klaster event i blok czasu; nie liczyć snapshotów jako meczów |
| Data snooping | Pozorna przewaga po setkach prób | Rejestr wszystkich prób, korekta rodziny, nowy okres po zmianie |
| Mało niezależnych sezonów | Niestabilne CI | Ocena mocy, raport inconclusive, dłuższy przyszły okres |
| Licencja i zmiana regulaminu | Niedozwolony trening/publikacja | Source registry, warunki z datą, blokada źródeł z niejasnymi prawami |
| Domeny o podobnych nazwach | Zakup niewłaściwej usługi | Weryfikacja dokładnego hosta i produktu |
| Nieznane reguły rynku | Błędny wynik nawet przy dobrym modelu | Dedykowany adapter wersji operatora; fail closed |
| LLM pamięć/halucynacje/drift | Nieuczciwy historyczny wynik, błędny rozkład | Prospektywny pakiet źródeł, schemat, model ID, koszty, brak automatycznej naprawy |
| Sekrety/ograniczone dane w Git | Ujawnienie lub naruszenie warunków | `.gitignore`, brak kluczy i raw, przegląd przed commitem |

Granice implementacji: brak bazy danych, aktualizacji raw, collectorów, modeli,
końcowego holdoutu, symulatora wykonania, dziennika paper ani dashboardu.
`frozen` w Pythonie nie zapewnia niezmienności pliku; interfejs append-only wymaga
implementacji storage i testów w kolejnym zadaniu. Typy i podstawowe FK nie zastępują
kompletnego walidatora schematu źródła, tożsamości oraz wszystkich relacji.

Profil PL/STS: tylko zwykły pojedynczy win/loss i dzień weryfikacji; dokładna granica
2280 zł zablokowana, zaokrąglenia symulatora nie są certyfikacją fiskalną. Zwroty
i half outcomes są testowane na jawnych profilach syntetycznych, nie przypisane STS.

Audyt zewnętrzny: własnych replikacji ROI/CLV nie wykonano. Twierdzenia repozytoriów
i prac to wskazane poziomy dowodów, nie rekomendacja ich wyników. Nie uzyskano dostępu
do kont API, feedów płatnych ani danych rachunku użytkownika. Katalog źródeł podaje
nieznane warunki zamiast zgadywać ceny czy pokrycie.

Lokalne testy sprawdzają kod, nie wiarygodność hipotezy sportowej. Workflow GitHub
Actions jest przygotowany, lecz nie uruchomiony zdalnie. Windows Python 3.12 jest
lokalnie sprawdzonym środowiskiem; inne wersje/platformy wymagają wykonania CI.
