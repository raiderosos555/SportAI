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

## TASK 003 — zabezpieczenia prognoz i treningu

| Ryzyko | Zabezpieczenie | Test |
|---|---|---|
| Prognoza po rozpoczęciu meczu | `save_prediction` odrzuca prognozę `prospective` bez wydarzenia, bez godziny startu lub z `created_at ≥ start`; `predict_offer` pomija rozpoczęte mecze | `test_forecasts_only_before_start_and_with_current_data`, `test_prediction_history_is_immutable_and_pre_match_only` |
| Prognoza utworzona po decyzji lub po starcie w odczycie | `latest_predictions(as_of)` zwraca tylko `created_at ≤ as_of` i `created_at < start` | jw. |
| Wynik meczu z tego samego dnia w cechach | Walk-forward grupuje mecze po dniach: prognozy dnia D używają wyłącznie dni < D | `test_walk_forward_does_not_use_the_current_or_same_day_results` |
| Wynik „dostępny” przed meczem | Import odrzuca wiersz z `available_at` przed dniem meczu, `HistoricalMatch` również; `history(until=…)` filtruje po dostępności | `test_result_cannot_be_available_before_its_match`, `test_history_is_as_of`, `test_source_timestamps_are_kept_apart_from_the_assumed_lag` |
| Założony czas dostępności traktowany jak potwierdzony | `available_at_basis`: `assumed_lag_36h` (openfootball, CSV bez czasu) vs `source_timestamp` (CSV z kolumną `available_at` ze strefą) | `test_history_is_as_of`, `test_source_timestamps_are_kept_apart_from_the_assumed_lag` |
| Dobór parametrów na holdoucie | Siatka wybierana na walidacji, margines remisu z danych sprzed holdoutu, holdout oceniany raz | `test_chronological_experiment_keeps_the_holdout_untouched` |
| Brak odtwarzalności | `info_cutoff ≤ created_at`, `data_snapshot` (SHA-256 wierszy), `code_state` (SHA-256 kodu + HEAD git) w każdej prognozie | `test_experiment_is_reproducible` |
| „Lepszy od rynku” bez dowodu | `beats_market` wyliczane z benchmarku na tych samych meczach; flaga wywołującego ignorowana | `test_validated_status_needs_derived_market_evidence`, `test_market_benchmark_uses_the_same_events` |
| Kurs zamknięcia jako cecha | Kursy innych operatorów tylko w `benchmark_odds`, bez joinu do cech modelu | — (brak danych; kontrola przy pierwszym imporcie) |
| Nieaktualne dane w prognozie | Prognoza wstrzymana, gdy ostatni wynik drużyny jest starszy niż 21 dni lub drużyna ma < 10 meczów | `test_forecasts_only_before_start_and_with_current_data` |

Wyniki: [raport TASK 003, sekcja C](task_003_report.md).

## TASK 004 — wyniki z wielu źródeł

| Ryzyko | Zabezpieczenie | Test |
|---|---|---|
| Wynik użyty przed jego publikacją | football-data.org: `available_at = lastUpdated` (górna granica dostępności, `source_last_updated`); bez znacznika — założenie +36 h; znacznik wcześniejszy niż start + 95 min jest odrzucany | `test_football_data_parser_standardises_records`, `test_incomplete_future_and_unavailable_records_are_refused` |
| Korekta wyniku nadpisuje przeszłość | Korekta to nowy wiersz; widok as-of pokazuje wynik znany w danej chwili | `test_corrections_are_new_rows_and_history_stays_as_of` |
| Ten sam mecz liczony dwa razy | `effective_results`: jeden mecz na fixture, hierarchia źródeł | `test_second_source_confirms_or_conflicts_and_the_hierarchy_decides` |
| Prognoza ponownie zapisana bez nowych danych | Ta sama wersja i ten sam snapshot danych — prognoza zostaje | `test_stale_elo_history_blocks_forecasts_until_the_gap_is_filled` |
| Prognoza całego meczu przypisana połowie | Klucz prognozy obejmuje rynek i okres | `test_match_halves_and_ht_or_ft_never_share_the_full_time_forecast` |

## TASK 011 — porównanie z rynkiem STS i ocena prospektywna

| Ryzyko | Zabezpieczenie | Test |
|---|---|---|
| Kurs po rozpoczęciu meczu (na żywo) jako benchmark | Tylko ostatni dokument STS z czasem dokumentu ściśle przed startem, status `ok` | `test_market_benchmark_uses_only_the_price_published_before_the_kick_off`, `test_a_forecast_with_only_a_price_from_after_the_start_is_not_compared` |
| Kurs STS jako cecha modelu | Kurs czyta wyłącznie `prospective_eval` (ocena po fakcie); modele i prognozy go nie znają | `test_flags_and_comparisons_change_no_forecast_threshold_or_status` |
| Brak pomiaru pokazany jako „model nie lepszy” | Stany „nieznane” / „za mało (n < 300)” zamiast domyślnego `false` | `test_market_benchmark_uses_only_the_price_published_before_the_kick_off` |
| Rynki jednego meczu jako niezależne dowody | Jednostką kalibracji i porównań jest mecz | `test_reliability_counts_matches_not_selections` |
