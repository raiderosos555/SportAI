# Ochrona przed wykorzystaniem przyszłości

Data meczu nie jest czasem poznania cechy. Pięć zegarów rozdzielamy w `Timestamps`:

| Zegar | Znaczenie |
|---|---|
| event_time | Czas zdarzenia, którego dotyczy rekord (start/akcja/wynik według schematu źródła) |
| valid_at | Od kiedy wartość obowiązuje; np. notowanie kursu |
| published_at | Kiedy źródło ją opublikowało, jeśli znane |
| observed_at | Kiedy collector rzeczywiście ją zobaczył, jeśli zarejestrowane |
| ingested_at | Kiedy pobrano do naszego systemu; nie backdatować |

Wszystkie instanty offset-aware, przechowywanie docelowo UTC; zachować oryginalną strefę
w raw. Czas polski/DST nie jest stałym UTC+1. Data bez godziny pozostaje datą. Nie
wypełniać missing published/observed czasem startu lub pobrania pod inną nazwą.

## Dwie różne ścieżki dowodu

**Ścisły replay lokalny/paper (zaimplementowany walidator)**: ingest ≤ decyzja;
publication ≤ observation ≤ ingest, jeśli odpowiednie pola są znane. Kurs wymaga
valid_at ≤ decyzja, ograniczonego wieku i decyzji < start. Dowodzi jedynie obecności
rekordu w naszym systemie, nie jego osiągalności za daną stawkę u operatora.

**Historyczna rekonstrukcja dostawcy (tylko projekt)**: dane pobrane dziś mogą być
użyte do dawnego backtestu wyłącznie z dowodem archiwalnej dostępności. Zachowujemy
dzisiejszy ingest i osobny manifest archive snapshot, wersję kontraktu czasu oraz
oznaczenie `provider_archive`, nie „obserwowano na żywo”. `require_available` nie ma
przełącznika omijającego kontrolę. Nowa ścieżka wymaga jawnej implementacji i testów.
Sama kolumna closing/date nie spełnia tego warunku.

## Zagrożenia i konkretne zabezpieczenia

| Zagrożenie | Zabezpieczenie / test odbiorczy |
|---|---|
| Rolling forma zawiera bieżący mecz | Shift/as-of po dostępności zakończonego wyniku; zmiana wyniku meczu nie może zmienić jego cech |
| Sezonowe statystyki obliczone na całym sezonie | Przyrostowe agregaty wcześniejszych wydarzeń; brak globalnych mean/scaler |
| Imputacja, feature selection, kalibracja na pełnych danych | Fit tylko train, validation do doboru, holdout jednorazowy; test z perturbacją przyszłości |
| Bieżący ranking użyty historycznie | Archiwum rankingów ze znaną publikacją; brak ⇒ cecha wyłączona |
| Uraz/skład/wynik skorygowany później | Wersje rekordów i as-of, zakaz zastępowania przeszłości stanem obecnym |
| Closing jako cecha decyzji wcześniej | Oddzielne magazyny benchmarku ex post i cech, brak joinu przez sam event_id |
| Wynik zwycięzca/przegrany w nazwach kolumn tenisa | Permutacja uczestników deterministyczna przed etykietowaniem; audit feature lineage |
| Ten sam mecz w train i test przez wiele rynków | Grupowanie event_id, purge okien; kontrola udziałów foldów |
| Terminarz po zmianie meczu znany z przyszłości | Wersjonowanie startu i statusu; decyzja używa wersji widocznej wtedy |
| Maximum odds z różnych chwil | Wspólny timestamp, operator i dostępność; żadnego ex-post wybierania maksimum |
| LLM pamięta wynik | Prospektywne prognozy, log czasu i niezmienne wejście; historyczne tylko eksploracyjnie |
| Informacja w zewnętrznym tekście/prompt injection | Źródła jako dane; ekstrakcja schematu bez wykonywania poleceń; brak narzędzi obstawiania |
| Ręczny dobór ligi po holdoucie | Rejestr wszystkich prób i blokada użytego okresu jako przyszłego potwierdzenia |

W TASK 001 testy pokrywają strefy, kolejność, granicę startu, brak/future/stale quote.
Testy transformacji cech i archiwalnego replay zostaną dodane wraz z ich implementacją.
Nie twierdzimy, że sam zestaw aktualnych walidatorów wyklucza każdy przeciek.
