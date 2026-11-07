# Protokół i rejestr hipotez

Każdy eksperyment istnieje w rejestrze **przed** odczytem jego testu. Również porzucone
próby, prompty, progi i zmiany dostawcy otrzymują wpis. ID jest niezmienne; korekta
tworzy rewizję z odniesieniem do poprzedniej. W TASK 001 jest model `Experiment`
i szablon, nie działająca baza rejestru.

## Manifest do uzupełnienia przed uruchomieniem

```yaml
id: EXP-002-CONSENSUS-001
status: proposed                 # jeszcze nie zarejestrowano testu
hypothesis: Sygnał konsensusu poprawia wynik netto poza próbą
family_id: CONSENSUS-INITIAL
parent_experiment: null
registered_at: null              # rzeczywisty czas rejestracji, nie wymyślać
sport: soccer
competition: pending_coverage_audit
market: regulation_1x2
decision_horizon_minutes: 60
code_commit: pending
data_manifest_sha256: pending
model_version: market_proportional_devig_v1
config_sha256: pending
seed: 42
cost_profile: pending_jurisdiction_and_bookmaker
train_interval: pending
validation_interval: pending
test_interval: pending
final_holdout_manifest: pending
primary_metric: net_profit_per_deposited_stake
minimum_economic_effect: 0.02     # propozycja, do zamrożenia przed próbą
alpha: 0.05
power: 0.80
multiple_testing: Holm_family
resampling_unit: event_in_time_blocks
planned_duration_and_sample: pending_power_analysis
independence: prospective_required
artifacts: []
results: null
conclusion: not_run
```

`pending`/null blokują uruchomienie konfirmacyjne; to nie gotowy eksperyment ani
fikcyjne timestampy. Dokładne okresy wybieramy po ocenie pokrycia **bez czytania ROI**.
Pełny manifest przechowywany w `config_json`/artefakcie uzupełnia podstawowy model.

## Cykl życia i bramki

`proposed → data_review → registered → development → frozen → holdout_opened →
prospective → concluded`. Osobne zakończenia: `rejected`, `inconclusive`, `invalidated`.
Zmiana po frozen tworzy nowy ID; wykorzystany test pozostaje development dla potomka.
Nie zmieniać statusu na „confirmed” po samej eksploracji. Porażka techniczna nie jest
odrzuceniem hipotezy statystycznej.

Przed frozen: prawa do danych, sprawdzone timestampy, dokładna reguła rynku/operatora,
metryki i mianowniki, pola braków, rodzina testów, liczebność/moc, koszty i latency,
zasady outlierów, zakończenia i wyboru stawki. Holdout otwiera się tylko raz dla
zarejestrowanej rodziny. Kolejne podglądy wymagają z góry ustalonego sequential design
lub alpha spending, którego nie wdrażamy w pierwszym eksperymencie.

## Artefakty i odtwarzalność

Manifest raw + hashe, lista ID wydarzeń w każdym foldzie, kod/wersje bibliotek,
konfiguracja, ziarna, model i kalibrator, predykcje wszystkich wydarzeń, wszystkie
decyzje/odmowy, kurs wejścia i benchmark zamknięcia, reguły/podatki, rozliczenia,
raporty i wnioski. Brak możliwości publikacji raw ⇒ udostępniamy schemat, hash,
instrukcję legalnego pobrania i fixture syntetyczny, a ograniczenie reprodukcji opisujemy.
Źródła tekstowe LLM także muszą być legalnie archiwizowane.

Wersjonowanie danych niezależne od kodu. Brak Git na początku TASK 001 odnotowano;
przyszły eksperyment bez kodu opisanego commitem/hash manifestu jest niekompletny.
Odtwarzalność wyniku probabilistycznego nie oznacza bitowej deterministyczności
zewnętrznego LLM; dlatego zapisujemy odpowiedzi i oceniamy wariancję powtórzeń.

## Raport wyników

Raport zawiera pełny diagram wykluczeń (powód i n), liczebności na każdym etapie,
walk-forward/holdout/paper osobno, metryki i sparowane różnice do benchmarków,
koszty składowe, CI i metodę resamplingu, liczbę wszystkich hipotez, korektę
wielokrotności, brakujące CLV, wyniki per sezon/sport/rynek oraz wnioski ograniczone
do badanej populacji. Decyzja: odrzucenie / brak dowodów / dalsza walidacja, bez typów
do gry prawdziwymi pieniędzmi.

## Protokół v3 modeli piłkarskich (TASK 012)

Protokół v2 (TASK 007) ma stałe daty okien, więc retrening na nowych meczach odtwarza te same
hiperparametry i kalibratory. Protokół v3 (`config/protocol_v3.toml`, `src/sports_edge/rolling.py`)
liczy okna wstecz od daty treningu T, wyłącznie z danych dostępnych w T:
dopasowanie (siły drużyn as-of) przed T − 24 mies., kalibracja [T − 24, T − 12 mies.),
selekcja hiperparametrów [T − 12 mies., T); przy krótszej historii półsezony 6 + 6 mies.
Mecz należy do okna tylko wtedy, gdy jego wynik był dostępny (`available_at`) przed końcem okna.

Bramka decyzji v3 jest inna niż w v2: nie ma nietkniętego historycznego holdoutu. O aktywacji
rozstrzyga porównanie prospektywne z aktywną wersją w trybie cienia (co najmniej 300 wspólnych
rozliczonych meczów, TASK 011); wynik okna selekcji to tylko informacja. Wersja v3 jest zawsze
rejestrowana jako challenger, a aktywacja należy do użytkownika. Dowód historyczny bez czekania:
walk-forward z retreningiem co miesiąc (`sports-edge research walk-forward-v3`) na tych samych
meczach co zamrożona wersja v2 — szczegóły i wyniki w `docs/task_012_report.md`.

TASK 013 (wersja polityki `2026-11-07.v3b`): okres połowicznego zaniku Dixon–Coles wybierany
regułą **kary za zmianę** (`sticky12`: inna wartość niż poprzedni wybór tylko, gdy górna granica
95% bootstrapu po meczach różnicy log loss w oknie selekcji jest < 0), a 5 czołowych lig
kalibruje na **24 miesiącach** ([T − 36, T − 12 mies.)) — z kalibratorami z 12 mies. każda
reguła była tam istotnie gorsza od v2 w O/U 1,5 i 2,5. Reguła i jej decyzja są w
`validation_json` nowych wersji (`half_life_rule`). O aktywacji challengera v3 decyduje tryb
cienia dopiero po zapisanej akceptacji bramki przez użytkownika (`model_events`,
`protocol_gate_accepted`); bez niej aktywacja v3 jest zablokowana. Wyniki: `docs/task_013_report.md`.
