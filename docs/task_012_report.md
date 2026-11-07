# Raport TASK 012 — modele, które naprawdę się uczą: ruchome okna, automatyczne prognozy, kursy bliżej startu

Data pracy: 06–07.11.2026. Kod bazowy: `b21cb86` (TASK 011). Wszystkie pomiary na prawdziwych
danych wykonano na **kopiach** prywatnej bazy (`storage.snapshot_file`, tylko do odczytu, katalog
tymczasowy). Po pomiarach kopie usunięto. W raporcie są wyłącznie liczby zbiorcze. Raport nie
zawiera rekomendacji zysku ani stawek. Żaden model nie został aktywowany, a prawdziwa baza nie
była otwierana nowym kodem.

## 0. Najważniejsze w skrócie

- **Protokół v3 — okna ruchome.** Okna liczone są wstecz od daty treningu T (dane dostępne do T):
  kalibracja [T − 24 mies., T − 12 mies.), selekcja hiperparametrów [T − 12 mies., T), wcześniej
  dopasowanie. Mecz należy do okna tylko wtedy, gdy jego wynik był znany przed końcem okna.
  Retrening **może** więc zmienić hiperparametry i kalibratory. Challengery v3 utworzone na kopii
  różnią się od championów v2 (§2.4).
- **Inna bramka decyzji (wymaga Twojej akceptacji).** W v3 nie ma już nietkniętego historycznego
  holdoutu. O aktywacji rozstrzyga porównanie w trybie cienia (co najmniej 300 wspólnych
  rozliczonych meczów). Championy v2 pozostają aktywne, v3 trenuje wyłącznie challengery i nic nie
  aktywuje samo.
- **Walk-forward v3 kontra zamrożony v2** (retrening co miesiąc od 07.2024, te same mecze):
  - **7 lig:** v3 lepszy. Elo −0,0038 log loss (95%: −0,0064…−0,0012), Dixon–Coles łącznie
    −0,0057 (−0,0094…−0,0023), 1X2 Dixon–Coles −0,0128. W strefie ≥ 60% znikają flagi
    „zbyt optymistyczna”, które v2 ma w 1X2, podwójnej szansie i Elo.
  - **5 czołowych lig:** Elo bez istotnej różnicy (+0,0007). Dixon–Coles v3 jest **gorszy**
    łącznie: +0,0050 (+0,0017…+0,0088), głównie przez O/U 1,5 (+0,0098).
  - Wniosek: v3 nie jest automatycznie lepszy, dlatego bramką pozostaje tryb cienia.
- **Automatyczne prognozy.** Nowe zadanie workera `forecasts` uruchamia etapy analizy od oferty
  do prognoz (dziś i jutro), bez kuponów i bez pobierania danych. Działa po każdym nowym
  dokumencie STS, co 12 h i awaryjnie o 07:30. Symulacja na archiwum: pokrycie **56/72 → 68/72**
  rozpoczętych meczów 12 lig (§3.1).
- **Raport tygodniowy** (`calibration_report`) zapisuje wpis historii (nowa tabela
  `calibration_reports`, schemat v10) i tworzy jeden alert na fakt. Na kopii z 07.11 pojawiła się
  pierwsza flaga: **Elo, podwójna szansa 70–80% — „zbyt ostrożna”** (37 meczów: przewidywane
  73,3%, trafione 87,8%, 95%: 73,6–95,0%). Przy powtórzeniu nie ma nowego alertu.
- **Kursy STS w dniu meczu.** Tryb zadania `sts_offer` pobiera ofertę na dziś ~120 i ~45 min
  przed pierwszym meczem każdej godziny. Obowiązuje limit raz na 10 minut na plik i dzienny pułap
  12 pobrań. Bez zgody użytkownika nic się nie pobiera. Plan dla 01.11: mediana wieku kursu
  18,0 h → 0,8 h. Benchmark rynku zapisuje wiek dokumentu i dzieli wyniki na ≤ 3 h, 3–12 h,
  > 12 h.
- **Ryzyko dnia z macierzy wyniku.** Nowe prognozy Dixon–Coles zapisują λ, μ i ρ. Typy jednego
  meczu w kilku kuponach są liczone z macierzy wyników. Gdy parametrów nie ma (stare wiersze),
  albo rynek nie wynika z macierzy (Elo 1X2), stosowane jest jawnie oznaczone założenie
  niezależności.
  - Przy okazji poprawiono błąd z TASK 011: nogi kuponów zapisane bez `event_id` były sklejane
    w jeden mecz. Dla 02.11 szansa przegranej co najmniej dwóch kuponów wynosi **58,1%**, a nie
    42,9%.
- **Testy w worktree importują własny kod.** `tests/conftest.py` wstawia `src` z bieżącego
  katalogu i ustawia `PYTHONPATH` dla podprocesów.

## 1. Diagnoza (07.11.2026, kopia bazy, kod `b21cb86`)

### 1.1 Stałe okna protokołu v2

- Okna protokołu v2 są stałe (`services.PROTOCOLS`):
  - core: walidacja od 2021-07-01, kalibracja od 2022-07-01, selekcja od 2023-07-01, holdout od
    2024-07-01;
  - extended: 2022-07-01 / 2023-01-01 / 2023-07-01 / 2024-07-01.
- Siły drużyn są bieżące (Elo liczy oceny na bieżąco, a Dixon–Coles dopasowuje się przy prognozie
  do wszystkich wyników do teraz). Zamrożone są natomiast hiperparametry (Elo k, przewaga
  gospodarza, margines remisu; Dixon–Coles okres połowicznego zaniku) i kalibratory — pochodzą z
  okien zakończonych w lipcu 2024.
- Od 07.2024 rozegrano **3936** meczów core i **5567** meczów extended (to te same mecze co
  w walk-forward, §2.3). Żaden z nich nie wpływa na hiperparametry ani kalibratory. Dlatego
  challengery TASK 011 były identyczne z championami.

### 1.2 Pokrycie prognozami

Od 01.11 13:00 (czasu polskiego) do 07.11 ok. 12:40 rozpoczęły się **72 mecze** z oferty STS w
12 obsługiwanych ligach. **56** z nich miało prognozę zapisaną przed startem, **16** nie miało.

| Dzień | Mecze | Z prognozą przed startem |
|---|---|---|
| 01.11 | 32 | 18 |
| 02.11 | 11 | 11 |
| 03.11 | 9 | 9 |
| 04.11 | 4 | 4 |
| 06.11 | 14 | 12 |
| 07.11 (do 12:40) | 2 | 2 |
| **razem** | **72** | **56** |

- Według zakresu: core 24/25, extended 32/47.
- Mecze bez prognozy były w dokumencie STS już **14,6–20,1 h przed startem**, dwa nawet około
  140 h wcześniej. Brakowało więc uruchomienia analizy, a nie oferty. Prognozy powstawały tylko
  po „Analizuj” albo `sports-edge analyse`, a zadanie `sts_offer` jedynie pobierało ofertę.
- Liczby różnią się nieco od specyfikacji (66 / 54 / 12): okres jest dłuższy (do 07.11) i do
  01.11 doliczono 4 mecze rozpoczęte o 13:00–14:00.

### 1.3 Wiek kursów STS przed startem

Wiek liczony jest od ostatniego dokumentu z kursem meczu przed startem, osobno dla każdego meczu.

| Zakres | Mecze | Mediana | p75 | Z ostatnich 3 h |
|---|---|---|---|---|
| 5 czołowych lig | 25 | 16,75 h | 19,25 h | 40% |
| 7 lig | 47 | 4,5 h | 16,25 h | 30% |
| wszystkie | 72 | 7,67 h | 17,94 h | 33% |

- Archiwum zawiera 3–8 dokumentów dziennie.
- Między czasem z dokumentu a jego pobraniem mija mediana **2,4 min** (p10–p90: 1,2–5,0 min,
  n = 35). STS generuje więc PDF praktycznie w chwili żądania, a wiek kursu zależy od tego, kiedy
  pobieramy plik.

### 1.4 Ryzyko dnia i 1.5 testy

- „Ryzyko dnia” traktowało różne typy jednego meczu jak niezależne. Zapisane prognozy Dixon–Coles
  nie miały λ, μ ani ρ, więc macierzy wyników nie dało się odtworzyć.
- `tests/conftest.py` nie wstawiał `src` bieżącego katalogu. W worktree o kolejności importu
  decydowała instalacja edytowalna (`G:\SportAI\src`), a testy uruchamiające podprocesy brały
  kod głównego checkoutu, o ile nie ustawiono ręcznie `PYTHONPATH`.

## 2. Protokół v3 — okna ruchome (P0)

### 2.1 Okna i brak przecieku

Konfiguracja jest w `config/protocol_v3.toml` (wersja `2026-11-06.v3a`), kod w
`src/sports_edge/rolling.py`.

| Okno | Zakres (T = data treningu) | Do czego służy |
|---|---|---|
| dopasowanie | przed T − 24 mies. | rozgrzewka sił drużyn (Elo as-of, dopasowania Dixon–Coles) |
| kalibracja | [T − 24 mies., T − 12 mies.) | mapy izotoniczne Dixon–Coles, margines remisu Elo i szerokości przedziałów |
| selekcja | [T − 12 mies., T) | hiperparametry (Elo k i przewaga gospodarza; Dixon–Coles okres połowicznego zaniku) wybierane na **skalibrowanych** prognozach; statusy rynków, bramki lig i wybór modelu 1X2 tylko jako informacja |

- **Dostępność wyników.** Mecz trafia do okna tylko wtedy, gdy jego wynik był dostępny
  (`available_at`) przed końcem okna. Okno selekcji kończy się w T. Wynik ostatniego dnia
  kalibracji, znany dopiero po jej końcu, nie należy do żadnego okna (test 1).
- **Prognozy w oknach** są as-of, jak w v2: dzień D korzysta wyłącznie z wyników dni < D.
  Dixon–Coles dopasowuje się co 7 dni na **stałej siatce** (kotwica 2000-01-03), dzięki czemu
  dopasowanie z danego tygodnia nie zależy od T.
- **Krótsza historia** (7 lig: wyniki od 2021/22, Polska i Turcja od 2022/23). Jeżeli przy
  12 + 12 mies. któreś okno ma mniej niż 300 meczów albo rozgrzewka mniej niż 300 meczów,
  oba okna skracają się do półsezonów 6 + 6 mies., jak w v2. Gdy i to nie wystarcza, pojawia się
  jawny błąd i model nie powstaje. Na prawdziwych danych w każdym miesiącu od 07.2024 obowiązuje
  reguła 12 + 12 mies. (dla 7 lig rozgrzewka 2021/22 liczy około 1700 meczów).
- **Raport wersji** zapisuje `protocol = "v3"`, wersję polityki, daty i liczności okien, regułę
  oraz informację o bramce decyzji.

### 2.2 Bramka decyzji — zmiana metodologii (wymaga Twojej akceptacji)

| | Protokół v2 | Protokół v3 |
|---|---|---|
| Okna | stałe daty (2021–2024) | ruchome, liczone od daty treningu |
| Hiperparametry | okno walidacji (2021/22 lub 2022) | ostatnie 12 mies. |
| Kalibratory | okno kalibracji (2022/23 lub I połowa 2023) | 12 mies. przed oknem selekcji |
| Holdout | nietknięty, od 2024-07, tylko raportowany | **brak** — każdy mecz sprzed T jest w którymś oknie |
| Bramka aktywacji | decyzja użytkownika po porównaniu w trybie cienia | to samo; to jedyny niezależny sprawdzian, bo okno selekcji wybiera hiperparametry |
| Statusy rynków w oknie selekcji | podstawa statusów | informacja (okno nie jest niezależne od wyboru hiperparametrów) |

- Do czasu akceptacji i zebrania 300 wspólnych meczów championy v2 pozostają aktywne.
- `run_protocol_v3` nigdy nie zapisuje zdarzenia `activated`. Bez zarejestrowanego championa
  odmawia działania: pierwszy model zakresu powstaje w v2 (test 3).
- Panel decyzji pokazuje datę okna v3. Jego wniosek mówi wprost, że log loss okna selekcji v3 i
  v2 dotyczy **różnych okresów**, więc nie jest porównywalny.

### 2.3 Walk-forward na historii

- **Metoda.** Retrening v3 odbywa się pierwszego dnia każdego miesiąca M od 07.2024 i korzysta
  wyłącznie z wyników dostępnych przed M. Każdy model prognozuje tylko mecze miesiąca M.
- **Zamrożony v2** to aktywne wersje: core Elo k = 15, przewaga 40, margines 0,25, Dixon–Coles
  480 dni; extended Elo k = 30, 40, 0,24, Dixon–Coles 480 dni. Ich kalibratory pochodzą z okien
  sprzed 07.2024.
- **Te same mecze i te same siły drużyn as-of** w obu wariantach. Różnią się tylko hiperparametry
  i kalibratory.
- Różnica = log loss v3 − v2, uśredniana po meczach, z bootstrapem po meczach (1000 losowań,
  ziarno 7); ujemna oznacza, że v3 jest lepszy. „*” to wszystkie rynki rodziny bez podwójnej
  szansy.
- Mecze: core **3936**, extended **5567**. Czas obliczeń na kopii: 132 s i 152 s.

**5 czołowych lig** (24 retreningi; w lipcu bez meczów):

| Model | Rynek | Mecze | Log loss v3 / v2 | Różnica (95%) | Brier v3 / v2 | Ocena |
|---|---|---|---|---|---|---|
| Elo | 1X2 | 3936 | 0,98849 / 0,98774 | +0,00074 (−0,00099…+0,00267) | 0,5893 / 0,5887 | brak istotnej różnicy |
| Elo | podwójna szansa | 3936 | 0,57791 / 0,57743 | +0,00048 (−0,00047…+0,00146) | 0,1964 / 0,1962 | brak istotnej różnicy |
| Dixon–Coles | 1X2 | 3905 | 1,00353 / 1,00269 | +0,00084 (−0,01139…+0,01299) | 0,5937 / 0,5928 | brak istotnej różnicy |
| Dixon–Coles | podwójna szansa | 3905 | 0,58433 / 0,58564 | −0,00131 (−0,00796…+0,00443) | 0,1979 / 0,1976 | brak istotnej różnicy |
| Dixon–Coles | O/U 1,5 | 3905 | 0,54291 / 0,53311 | +0,00980 (+0,00216…+0,01924) | 0,3507 / 0,3477 | **v3 gorszy** |
| Dixon–Coles | O/U 2,5 | 3905 | 0,68548 / 0,68112 | +0,00435 (−0,00079…+0,01215) | 0,4898 / 0,4881 | brak istotnej różnicy |
| Dixon–Coles | O/U 3,5 | 3905 | 0,61622 / 0,61137 | +0,00485 (−0,00175…+0,01376) | 0,4234 / 0,4215 | brak istotnej różnicy |
| Dixon–Coles | BTTS | 3905 | 0,69274 / 0,68765 | +0,00509 (−0,00028…+0,01325) | 0,4961 / 0,4946 | brak istotnej różnicy |
| Dixon–Coles | * | 3905 | 0,70818 / 0,70319 | +0,00499 (+0,00165…+0,00881) | 0,4707 / 0,4690 | **v3 gorszy** |

**7 lig** (28 retreningów):

| Model | Rynek | Mecze | Log loss v3 / v2 | Różnica (95%) | Brier v3 / v2 | Ocena |
|---|---|---|---|---|---|---|
| Elo | 1X2 | 5567 | 1,01583 / 1,01960 | −0,00377 (−0,00639…−0,00119) | 0,6090 / 0,6116 | **v3 lepszy** |
| Elo | podwójna szansa | 5567 | 0,59262 / 0,59472 | −0,00209 (−0,00353…−0,00067) | 0,2030 / 0,2039 | **v3 lepszy** |
| Dixon–Coles | 1X2 | 5516 | 1,02275 / 1,03559 | −0,01284 (−0,02161…−0,00551) | 0,6115 / 0,6147 | **v3 lepszy** |
| Dixon–Coles | podwójna szansa | 5516 | 0,59601 / 0,60355 | −0,00754 (−0,01297…−0,00321) | 0,2038 / 0,2049 | **v3 lepszy** |
| Dixon–Coles | O/U 1,5 | 5516 | 0,53798 / 0,54857 | −0,01059 (−0,02083…−0,00210) | 0,3509 / 0,3515 | **v3 lepszy** |
| Dixon–Coles | O/U 2,5 | 5516 | 0,68896 / 0,68493 | +0,00402 (−0,00105…+0,01119) | 0,4918 / 0,4918 | brak istotnej różnicy |
| Dixon–Coles | O/U 3,5 | 5516 | 0,61227 / 0,61161 | +0,00066 (−0,00155…+0,00277) | 0,4215 / 0,4208 | brak istotnej różnicy |
| Dixon–Coles | BTTS | 5516 | 0,69560 / 0,70522 | −0,00962 (−0,01870…−0,00219) | 0,4962 / 0,4982 | **v3 lepszy** |
| Dixon–Coles | * | 5516 | 0,71151 / 0,71719 | −0,00567 (−0,00941…−0,00227) | 0,4744 / 0,4754 | **v3 lepszy** |

**Strefa kuponów ≥ 60%, liczona na meczach** (metoda TASK 011: Wilson 95% na meczach, flaga od
30 meczów). Pokazane są tylko komórki, w których flaga różni się między wariantami:

| Zakres | Model | Rynek | v2: mecze, szansa / trafność, ocena | v3: mecze, szansa / trafność, ocena |
|---|---|---|---|---|
| 7 lig | Dixon–Coles | 1X2 | 1288: 72,6% / 66,1% — zbyt optymistyczna | 938: 71,2% / 69,7% — zgodna |
| 7 lig | Dixon–Coles | podwójna szansa | 5516: 75,3% / 73,8% — zbyt optymistyczna | 5516: 74,3% / 73,8% — zgodna |
| 7 lig | Dixon–Coles | BTTS | 71: 79,8% / 59,2% — zbyt optymistyczna | 324: 62,8% / 60,8% — zgodna |
| 7 lig | Elo | podwójna szansa | 5567: 75,2% / 73,9% — zbyt optymistyczna | 5567: 74,5% / 73,9% — zgodna |
| 5 lig | Dixon–Coles | 1X2 | 731: 68,3% / 72,8% — zbyt ostrożna | 912: 69,6% / 70,8% — zgodna |
| 5 lig | Elo | 1X2 | 1006: 68,7% / 71,6% — zbyt ostrożna | 1037: 69,1% / 70,4% — zgodna |
| 5 lig | Dixon–Coles | O/U 3,5 | 3431: 73,1% / 70,4% — zbyt optymistyczna | 3047: 71,6% / 71,2% — zgodna |
| 5 lig | Dixon–Coles | O/U 2,5 | 1659: 62,7% / 60,8% — zgodna | 1178: 65,5% / 60,7% — zbyt optymistyczna |
| 5 lig | Dixon–Coles | BTTS | 177: 68,0% / 60,5% — zbyt optymistyczna | 620: 63,8% / 58,2% — zbyt optymistyczna |

**Odczyt** (bez rekomendacji zysku):

- W 7 ligach ruchome okna poprawiają i trafność prognoz (log loss), i kalibrację strefy kuponów.
  Kalibratory v2 pochodzą tam z jednego półsezonu (01–06.2023).
- W 5 czołowych ligach v3 nie poprawia Elo. Pogarsza natomiast Dixon–Coles, głównie O/U 1,5.
- Okres połowicznego zaniku wybierany co miesiąc skacze między 120, 240 i 480 dni. Wybór na
  12 miesiącach jest więc niestabilny w 5 czołowych ligach — to kandydat na TASK 013 (§10).
- To dowód, że okna ruchome mogą zmienić model. Nie jest to jednak dowód, że zawsze go
  poprawiają, dlatego o aktywacji rozstrzyga tryb cienia.

**Test przecieku.** Model miesiąca M (hiperparametry, margines remisu i kalibratory) budowany jest
wyłącznie z wyników dostępnych przed M:

- zmiana każdego wyniku znanego dopiero po początku M nie zmienia modelu;
- zmiana wyników sprzed M go zmienia (test kontrolny);
- oba warianty mają ten sam zbiór meczów (testy 4 i 5).

### 2.4 Challenger v3 utworzony na kopii (07.11)

Trening przeprowadzono na kopii, ręcznie (`run_protocol_v3`). Przegląd tygodniowy by go teraz nie
uruchomił: warunki TASK 011 nie są spełnione. Nowych meczów jest 9/150 (core) i 24/150
(extended), czekają challengery v2 z 06.11, a od ostatniego treningu minęło mniej niż 7 dni.
Te warunki pozostały bez zmian (§9).

| | 5 czołowych lig | 7 lig |
|---|---|---|
| Okna | kalibracja od 2024-11-07 (1724 mecze), selekcja od 2025-11-07 (1714) | kalibracja od 2024-11-07 (2432), selekcja od 2025-11-07 (2351) |
| Elo: champion v2 → challenger v3 | k 15 → 15, przewaga 40 → **60**, margines 0,25 → **0,26** | k 30 → **15**, przewaga 40 → 40, margines 0,24 → **0,26** |
| Dixon–Coles: okres połowicznego zaniku | 480 → 480 dni | 480 → **240** dni |
| Kalibratory Dixon–Coles | wszystkie 5 inne (np. 1X2 przy surowym 0,6: 0,581 → 0,632; O/U 3,5 przy 0,6: 0,672 → 0,586) | wszystkie 5 inne (np. 1X2 przy 0,7: 0,744 → 0,667; O/U 1,5 przy 0,6: 0,703 → 0,636) |
| Okno selekcji v3 (informacja) | Elo n = 1714, log loss 0,993, ECE 0,013; Dixon–Coles n = 1702, log loss 1,011 | Elo n = 2351, log loss 1,020; Dixon–Coles n = 2332, log loss 1,028 |
| Statusy w oknie selekcji (informacja) | 1X2 i podwójna szansa: zwalidowane; O/U i BTTS: eksperymentalne | to samo |
| Champion po treningu | bez zmian (v2) | bez zmian (v2) |
| Stan w panelu | „czeka na dowody (0/300)” | „czeka na dowody (0/300)” |

- Prognozy cienia powstają automatycznie: zadanie `forecasts` prognozuje także challengerami,
  które są archiwizowane i nigdy nie są serwowane.
- Przy najbliższym przeglądzie starsze challengery v2 wygasają jako zastąpione (reguła TASK 011).

### 2.5 Wariant B — rekalibracja na rozliczeniach prospektywnych

- **Nie włączono.** Na kopii jest 41 rozliczonych meczów z kursami, a w strefie kuponów 37–40
  meczów na rodzinę.
- **Kiedy można go mierzyć.** Gdy w danym zakresie będzie co najmniej 300 rozliczonych meczów
  aktywnej wersji. Przy obecnym tempie (około 45 meczów tygodniowo w core i 60 w extended, mniej
  w przerwach reprezentacyjnych) to około 7 tygodni dla extended i 9–10 dla core.
- **Pomiar.** Kalibratory dopasowane na starszych rozliczeniach, oceniane na późniejszych, porównane
  z v3 na tych samych meczach. Tylko jako pomiar — bez zmiany modeli.

## 3. Automatyczne prognozy i raport tygodniowy (P0)

### 3.1 Zadanie `forecasts`

- **Co robi.** `services.run_forecasts` wykonuje etapy analizy od oferty do prognoz dla dziś i
  jutro: stan oferty, świeżość wyników, dopasowanie nazw, prognozy aktywnych wersji i
  challengerów (tryb cienia).
- **Czego nie robi.**
  - nie pobiera oferty, wyników ani statystyk;
  - nie trenuje modeli;
  - nie robi migawek cech;
  - nie buduje kuponów i nie zapisuje analizy.

  Kod: `run_analysis(..., forecasts_only=True, train_missing=False)`.
- **Kiedy działa.**
  - Po każdym nowym dokumencie STS z zadania `sts_offer`. W historii jest wtedy wyzwalacz
    `sts_offer`.
  - Co 12 h.
  - Awaryjnie o 07:30 czasu polskiego, gdy od tej godziny nie było przebiegu.
  - Domyślnie włączone; w trybie offline pomijane.
- **Brak duplikatów.** Ta sama wersja i te same dane oznaczają „aktualne”, a nie nowy wiersz
  (istniejąca idempotencja, test 6).

**Symulacja na archiwum (kopia).**

- Wyzwalacze: każdy dokument STS od 31.10 (zadanie startuje 1 s po imporcie) oraz 07:30 każdego
  dnia.
- W chwili t używane jest tylko to, co było wtedy znane: mecze, których pierwszy dokument STS
  zaimportowano do t, oraz wyniki dostępne do t.
- Uproszczenie: wersja modelu to dzisiejszy champion (dla pokrycia bez znaczenia).

| Mecze rozpoczęte od 01.11 13:00 | Przed (zapis w bazie) | Po (symulacja `forecasts`) |
|---|---|---|
| wszystkie | 56 / 72 | **68 / 72** |
| 01.11 | 18 / 32 | 30 / 32 |
| 02–04.11 | 24 / 24 | 24 / 24 |
| 06.11 | 12 / 14 | 12 / 14 |
| 07.11 (do 12:40) | 2 / 2 | 2 / 2 |
| 5 czołowych lig | 24 / 25 | 24 / 25 |
| 7 lig | 32 / 47 | 44 / 47 |

- **Przebiegi.** Było 35 wyzwalaczy: 28 chwil importu dokumentów STS i 7 poranków. Jeden przebieg
  (oba zakresy, wersje aktywne i challengery w cieniu) trwał około 19 s, łącznie 659 s.
- **Bez duplikatów.** Przebiegi bez zmiany danych niczego nie dopisały — prognozy były
  „aktualne”.
- **4 mecze nadal bez prognozy** (po 2 z 01.11 i 06.11, w tym jedyny brak w core). Za każdym
  razem przyczyną jest za krótka historia drużyny: „za mało meczów w historii” (Elo, mniej niż
  10 meczów) i „za mało meczów w oknie modelu” (Dixon–Coles). To zamierzona blokada — prognoza
  dla drużyny bez historii byłaby niewiarygodna.
- **Artefakt symulacji (naprawiony).** Pierwsza wersja symulacji uruchamiała zadanie dokładnie w
  chwili importu. W bazie istniała już prognoza z tą samą sekundą (ręczna analiza użytkownika
  używała tego samego `now`), więc identyfikator prognozy kolidował. W aplikacji zadanie i tak
  rusza po imporcie, a ta sama wersja i te same dane dają „aktualne”, nie nowy wiersz.

### 3.2 Zadanie `calibration_report` (raport tygodniowy)

- **Wpis historii.** `prospective_eval.weekly_report` liczy kalibrację strefy kuponów na meczach i
  porównanie z rynkiem STS (TASK 011), a wynik dopisuje do nowej tabeli append-only
  `calibration_reports` (schemat v10). Identyfikator zależy od stanu danych i polityki, więc ten
  sam stan nie dodaje drugiego wpisu.
- **Alerty** (`alerts.create`, jeden na fakt):
  - przy pierwszej fladze „zbyt optymistyczna” / „zbyt ostrożna” dla rynku i przedziału. Liczą się
    rynki, które mogą trafić do kuponu, wersje aktywne; „wszystkie ligi” i zakres z tą samą flagą
    to jeden fakt;
  - przy pierwszym werdykcie porównania z rynkiem (model lepszy / gorszy / brak istotnej
    różnicy, czyli od 300 wspólnych meczów).

  Alert niczego nie zmienia w modelach, progach ani kuponach (test 8: zero zdarzeń modeli).
- **Na kopii z 07.11.** Wpis zapisano: 41 meczów, 1 flaga, 0 werdyktów, 1 alert. Treść alertu:
  „Strefa kuponów — Elo, Podwójna szansa, 70–80% (wszystkie ligi): zbyt ostrożna (n=37 meczów;
  średnia szansa 73,3%, trafność 87,8%)”.
  - W TASK 011 ta sama komórka miała 34 mecze i dolną granicę 71,6%, poniżej średniej szansy,
    więc bez flagi. Teraz dolna granica wynosi 73,6%.
  - Drugi przebieg nie dodał ani wpisu, ani alertu.
  - Osobno w 5 i w 7 ligach komórka ma mniej niż 30 meczów, więc tam flagi nie ma.

### 3.3 Panel Automatyzacja AI

- Oba zadania są widoczne w **Procesy** z przełącznikiem i częstotliwością: prognozy co
  3/6/12/24 h, raport co 1/3/7 dni.
- Prognozy mają dodatkowy opis: „po każdym nowym dokumencie STS i codziennie o 07:30”.
- Kalendarz pokazuje stałą godzinę prognoz i zaplanowane pobrania w dniu meczu.
- Bez sieci oba zadania są pomijane z powodem w historii.

## 4. Kursy STS bliżej startu (P1)

### 4.1 Tryb „w dniu meczu” zadania `sts_offer`

- **Plan** (`automation.matchday_plan`): dla każdej godziny (czas polski) z meczem 12 lig w
  dzisiejszej ofercie STS bierzemy pierwszy start i planujemy pobrania 120 i 45 min wcześniej.
  - Dwa pobrania bliżej niż 10 minut stają się jednym (późniejszym — nadal przed wcześniejszą
    grupą).
  - Pułap: 12 pobrań dziennie. Przy nadmiarze zostają najpierw pobrania 45 min przed startem.
- **Wykonanie.** Slot pobiera wyłącznie ofertę na dziś.
  - Slot jest „wykonany”, jeśli dowolne pobranie skończyło się najwyżej 10 min przed nim. Tak
    działa limit 10 min na plik, a dodatkowo pilnuje go `StsOfficialPdf`.
  - Slotu, którego grupa już się zaczęła, nie nadrabia się.
  - Zgoda: tryb jest domyślnie włączony, ale działa tylko wtedy, gdy użytkownik zezwolił na
    pobieranie oficjalnych PDF STS (i nie jest w trybie offline) oraz gdy zadanie `sts_offer` jest
    włączone. Bez zgody nic się nie pobiera (test 9).
- **Panel.** Harmonogram → „Kursy STS w dniu meczu”: stan, pobrania dziś (n/12), plan z godzinami
  i stanem (wykonane / oczekuje / pominięte), ustawienia (minuty przed meczem, limit 1–12).

**Plan dla dni z archiwum** (bez żadnego pobrania). Plan liczono z oferty danego dnia. Wiek „przed”
to ostatni dokument z kursem meczu sprzed startu, „po” — ten sam albo nowszy planowany dokument.
Założenia: dokument wygenerowany 2,4 min przed pobraniem, a plik na dziś zawiera wszystkie mecze
dnia.

| Dzień | Mecze 12 lig | Pobrania | Mediana wieku przed → po | Z ostatnich 3 h przed → po | Najstarszy kurs po |
|---|---|---|---|---|---|
| 01.11 (niedziela) | 35 | 12 (pułap; bez pułapu 21) | 18,00 h → 0,79 h | 0% → 100% | 1,79 h |
| 02.11 | 11 | 6 | 0,83 h → 0,79 h | 100% → 100% | 1,29 h |
| 06.11 | 14 | 6 | 2,33 h → 0,67 h | 64% → 100% | 1,54 h |

- 01.11 według zakresów: core 19,00 → 0,79 h, extended 16,25 → 0,79 h. 02.11 i 06.11 miały już
  pobrania z popołudnia (ręczne lub z interwału).
- Żadnego testowego pobrania nie wykonano. Wiek „po” to wiek **oczekiwany** przy założeniach
  powyżej i trzeba go potwierdzić na prawdziwych pobraniach (TASK 013).

### 4.2 Wiek kursu w porównaniu z rynkiem

- Polityka `prospective_policy.toml` ma wersję `2026-11-06.e2` z przedziałami `age_groups = [3, 12]`.
- Każdy wiersz porównania i każdy nowy wpis `market_benchmarks` zawiera:
  - `document_age_hours`: wiek dokumentu jeden na mecz (najmłodszy kurs meczu) — mediana,
    kwartyle i udział w przedziałach;
  - `by_document_age`: model kontra rynek w przedziałach ≤ 3 h, 3–12 h i > 12 h, z tymi samymi
    stanami co całość.
- Stare wpisy zostają bez zmian (nowa wersja polityki = nowe identyfikatory).
- Panel: Uczenie AI → Porównanie z rynkiem STS → „Według wieku kursu STS”.

**Na kopii** (aktywne wersje, wszystkie rynki rodziny):

| Zakres | Model | Mecze | Mediana wieku | ≤ 3 h / 3–12 h / > 12 h (udział meczów) | Różnica log loss w przedziałach (n) |
|---|---|---|---|---|---|
| wszystkie | Elo | 41 | 4,25 h | 37% / 29% / 34% | +0,016 (15) / +0,029 (12) / +0,034 (14) |
| 5 lig | Elo | 16 | 19,0 h | 31% / 0% / 69% | +0,090 (5) / — / +0,065 (11) |
| 7 lig | Elo | 25 | 4,25 h | 40% / 48% / 12% | −0,021 (10) / +0,029 (12) / −0,080 (3) |

- Wszystkie przedziały mają stan „za mało (n < 300)” i to tylko kontrola wrażliwości.
- Elo w 5 czołowych ligach jest gorsze od rynku przy kursach starszych niż 12 h (+0,065,
  95%: +0,012…+0,119, 11 meczów). Przy kursach do 3 h różnica jest jeszcze większa, ale na
  5 meczach. Kursy bliżej startu mają ten sygnał potwierdzić albo obalić.

## 5. Ryzyko dnia z rozkładu wyniku (P1)

- **Zapis.** Nowe prognozy Dixon–Coles zapisują w `inputs_json` pole `score_matrix`: λ, μ, ρ oraz
  `max_goals = 10`, z którego `models.goals.score_matrix` odtwarza dokładnie tę samą macierz.
  Dotyczy to tylko nowych wierszy, starych nie zmieniamy. Osobna tabela nie była potrzebna.
- **Łączna szansa.** Dla kilku różnych typów jednego meczu w kuponach dnia, gdy wszystkie pochodzą
  z tej samej prognozy Dixon–Coles z zapisanymi parametrami, a każdy rynek wynika z macierzy
  (1X2, podwójna szansa, gole, gole drużyny, BTTS, „strzeli”):
  - P(wszystkie wygrywają) = Σ macierz · Π maski zdarzeń (`models.goals.outcome_mask`, te same
    definicje co `markets`);
  - szanse ostrożne jak dotąd: wynik mnożony przez Π qᵢ / rᵢ (q — szansa ostrożna, r — szansa z
    macierzy); dla niezależnych typów to dokładnie iloczyn szans ostrożnych;
  - wynik mieści się w granicach Frécheta (max(0, Σq − (n − 1)) … min q).
- **Fallback z oznaczeniem.** Niezależność stosujemy, gdy:
  - prognoza nie ma parametrów (starszy zapis);
  - rynek nie wynika z macierzy (np. Elo 1X2);
  - typy pochodzą z różnych modeli.

  Blok „Ryzyko dnia” zawsze pisze, której metody użył i dlaczego. Bez rekomendacji stawek.
- **Przykład z testu.** „Powyżej 1,5” i „gospodarz strzeli” jednego meczu (λ = 1,5, μ = 1,1,
  ρ = −0,05): szansa łączna z macierzy różni się od iloczynu, a przy szansach ostrożnych
  0,9 × r wynosi dokładnie łączna × 0,81.

**Na kopii** (kupony zapisane w bazie, bez ponownego budowania):

| Dzień | Kupony | Selekcje (wspólne) | Mecze z kilkoma typami | Metoda | P(≥ 2 przegrane) | Gdyby kupony były niezależne | Oczekiwana liczba przegranych |
|---|---|---|---|---|---|---|---|
| 02.11 | 5 | 5 (3) | 0 | — | **58,1%** | 74,2% | 2,22 z 5 |
| 06.11 | 2 | 5 (0) | 1 | niezależność — typy z różnych modeli | 31,7% | 31,7% | 1,13 z 2 |

- **Poprawka liczby z TASK 011 dla 02.11.** Nogi kuponów zapisane przed dodaniem `event_id` nie
  mają identyfikatora meczu. „Ryzyko dnia” sklejało je w jeden mecz: trzy typy „powyżej 1,5” z
  trzech różnych meczów liczyło jako jedną selekcję. Dlatego raport TASK 011 podał
  „3 selekcje, 42,9%”.
  - Teraz mecz rozpoznawany jest po `event_id`, a gdy go brak — po nazwie i godzinie
    (`insights.match_of`, test 12). Wynik dla 02.11: 5 selekcji, 58,1%.
  - Ta sama poprawka dotyczy oznaczenia „ten mecz jest też w…” w Kuponie dnia.
- **06.11.** Jeden mecz ma dwa typy z dwóch modeli: Dixon–Coles „powyżej 1,5” i Elo
  podwójną szansę 12. Elo jest aktywnym modelem 1X2 w obu zakresach — Dixon–Coles nie serwuje
  tam 1X2. Zgodnie ze specyfikacją blok stosuje jawnie oznaczoną niezależność: „metoda dla typów
  jednego meczu: założenie niezależności (1 mecz: typy z różnych modeli)”.
- **Macierz na kopii.** Po symulacji zadania `forecasts` nowe prognozy Dixon–Coles mają λ, μ i ρ.
  Na kopii nie ma jednak dnia z dwoma typami Dixon–Coles jednego meczu, więc metoda z macierzy
  nie miała zastosowania. Działa w testach 11 i 13 i pojawi się w pierwszym takim kuponie.

## 6. Testy w worktree (P2)

- `tests/conftest.py` przed importem `sports_edge`:
  - wstawia `ROOT / "src"` na początek `sys.path`;
  - usuwa moduły `sports_edge` załadowane z innego checkoutu;
  - ustawia `PYTHONPATH` z tym katalogiem na początku, więc worker i Streamlit w podprocesach
    używają kodu z worktree.
- Test sprawdza, że `sports_edge.__file__` leży pod `ROOT / "src"`.

## 7. Zmiany w kodzie i danych

- `src/sports_edge/rolling.py` (nowy): okna v3, trening Elo i Dixon–Coles v3, raporty, różnice
  względem championa, walk-forward v3 vs v2.
- `config/protocol_v3.toml` (nowy). `config/prospective_policy.toml`: wersja `e2` i
  `age_groups`. `probability_policy.toml` **bez zmian** — progi profili i polityka walidacji
  nietknięte.
- `services.py`:
  - `run_protocol_v3`, `train_challengers` (v3; v2 tylko, gdy okna v3 są niewykonalne),
    `run_walk_forward_v3`;
  - `run_forecasts` i `forecast_coverage`, `run_analysis(forecasts_only, train_missing)`;
  - rozpoznawanie v3 jako protokołu bez użytego holdoutu (statusy nie są zawieszane);
  - „Trenuj teraz” trenuje v3.
- `retraining.py`: przegląd tygodniowy trenuje przez `train_challengers`. Warunki (150 nowych
  meczów, czekający challenger, 7 dni) są bez zmian. Wniosek panelu dla v3 kontra v2.
- `automation.py`:
  - zadania `forecasts` i `calibration_report`;
  - tryb „w dniu meczu” zadania `sts_offer`, `morning_next` i kalendarz;
  - **`write_runtime`, `Worker.beat`, `_heartbeat_loop` i `main()` nietknięte.** Próbne scalenie
    3-drożne z niescaloną poprawką workera (`claude/optimistic-hugle-7fa6d3`, baza `522dfda`)
    przechodzi bez konfliktów.
- `prospective_eval.py`: wiek dokumentu w porównaniu z rynkiem, `weekly_report`,
  `latest_weekly_report`. `alerts.py`: rodzaje `calibration_flag` i `market_verdict`.
- `probability_lab.predict_offer_goals`: λ, μ, ρ w nowych prognozach. `models/goals.outcome_mask`.
  `insights.day_risk` (macierz) i `matrix_rates`.
- `storage.py`: `SCHEMA_VERSION = 10`, `MIGRATIONS[10]` — jedna nowa tabela append-only
  `calibration_reports` z triggerami `immutable_*`.
- Interfejs:
  - `page_automation`: Kursy STS w dniu meczu, opisy w Procesach;
  - `page_learning`: wiek kursu, okno v3 w panelu decyzji, opis przycisku treningu;
  - `page_coupon`: metoda Ryzyka dnia;
  - `page_models`: v3 bez zawieszenia statusów.
- `cli.py`: `research protocol-v3`, `research walk-forward-v3 [--first-month] [--out]`,
  `forecasts`, `calibration-report`.
- `qa.py`: 3 nowe ścieżki. `docs/experiment_protocol.md`: opis protokołu v3. README.

**Migracja na świeżej kopii prawdziwej bazy:**

- przejście v9 → v10 utworzyło kopię `pre-migration-v10_…db`;
- liczby wierszy wszystkich 45 tabel są bez zmian (poza jednym nowym wpisem
  `schema_migrations`), a jedyną nową tabelą jest `calibration_reports`;
- żaden istniejący rekord nie został zmieniony ani usunięty (triggery `immutable_*`; test 15
  sprawdza też odmowę `DELETE`).

## 8. Testy i kontrole

`tests/test_task012.py` — 17 testów na danych syntetycznych:

1. okna v3 przesuwają się z datą treningu, a żadne okno nie zawiera wyniku znanego po jego
   końcu; reguła półsezonów i jawny błąd przy zbyt krótkiej historii;
2. challenger v3 na nowych meczach różni się od championa v2 (kalibratory, prognozy nowych
   meczów); champion v2 pozostaje aktywny; jedyne nowe zdarzenia to `challenger_ready`;
3. odmowa treningu v3 na tych samych danych i bez championa;
4. model miesiąca M w walk-forward nie zmienia się, gdy zmieniają się wyniki znane po początku
   M (test przecieku z próbą kontrolną);
5. walk-forward: v3 i v2 na tym samym zbiorze meczów, trening przed miesiącem;
6. `forecasts`: prognozy dla meczów oferty, 0 kuponów i 0 analiz, brak duplikatów, pominięcie
   offline, uczciwy brak prognozy dla zakresu bez modelu;
7. nowe zadania w harmonogramie, poranna godzina, `forecasts` po nowym dokumencie STS;
8. raport tygodniowy: pierwsza flaga daje jeden alert, przy powtórzeniu nie ma nowego alertu
   ani wpisu, modele bez zmian;
9. tryb „w dniu meczu”: plan względem startów, scalanie bliżej niż 10 min, pułap dzienny, brak
   zgody, nadrabianie tylko przed startem grupy, stany w panelu, pobranie wyłącznie oferty na
   dziś;
10. benchmark zapisuje wiek dokumentu, a przedziały ≤ 3 h / 3–12 h / > 12 h są poprawne;
11. Ryzyko dnia: „powyżej 1,5” i „gospodarz strzeli” z macierzy (≠ iloczyn), szanse ostrożne,
    oznaczony fallback (brak parametrów, różne modele);
12. stare nogi bez identyfikatora meczu to różne mecze;
13. Kupon dnia podaje metodę; `matrix_rates` znajduje λ, μ, ρ zapisane w prognozie;
14. nowe prognozy Dixon–Coles zapisują parametry macierzy;
15. migracja v9 → v10: kopia zapasowa, liczby wierszy bez zmian, triggery, odmowa `DELETE`;
16. `conftest`: `sports_edge` z `ROOT / "src"`, `PYTHONPATH` dla podprocesów;
17. AppTest Automatyzacji AI: nowe zadania i sekcja „Kursy STS w dniu meczu”.

**Zmienione istniejące testy** (żaden nie został wyłączony):

- `test_task011.py` — test migracji v9 sprawdza teraz `SCHEMA_VERSION >= 9`, nazwę kopii z
  bieżącą wersją i wpis migracji pod bieżącą wersją, tak jak TASK 011 zrobił z testem TASK 010;
- `test_ui_paths.py` oczekuje 3 nowych ścieżek QA.

**Wyniki** (07.11.2026, kod z worktree — `conftest` wstawia własny `src`, a `PYTHONPATH` ustawiono
też dla modułów QA):

| Kontrola | Wynik |
|---|---|
| `pytest -q` | **384 passed, 4 skipped** (388 testów; pominięte: brak lokalnych autentycznych PDF STS), 125 s |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 173 pliki sformatowane |
| `python -m sports_edge.qa` (ścieżki UI, DEMO) | **29/29 OK**, w tym nowe: „automatyzacja: prognozy automatyczne i raport tygodniowy”, „automatyzacja: kursy STS w dniu meczu”, „kupon dnia: metoda Ryzyka dnia” |
| `python -m sports_edge.qa_visual` (Playwright, DEMO) | **83 kontrole** (14 stron × 5 rozdzielczości: 1920, 1366, 1024, 768, 390 px + 13 scenariuszy, w tym „Kupon dnia: Ryzyko dnia”), **0 błędów** |
| `python -m sports_edge.qa_browser` (Playwright, DEMO) | 22 zrzuty, 0 błędów |
| `qa_visual --home <kopia>`: Automatyzacja AI, Modele, Kupon dnia × 5 rozdzielczości | 15 kontroli, 0 błędów; po poprawce stanów slotów powtórka Automatyzacji w 1366 i 390 px: 2 kontrole, 0 błędów |

**Przegląd ręczny zrzutów z kopii** (zrzutów nie dodano do repozytorium; usunięto je razem z
kopiami):

- **Błąd znaleziony i poprawiony.** Tabela „Kursy STS w dniu meczu” oznaczała minione sloty jako
  „wykonane”, bo pokrywało je późniejsze pobranie. Teraz slot jest wykonany tylko wtedy, gdy
  pobranie skończyło się między „slot − 10 min” a startem jego grupy. Pozostałe stany:
  „zaległe”, „oczekuje”, „pominięte (grupa rozpoczęta)” (`automation.matchday_status`, test 9).
- **Worker kopii.** Serwer QA uruchomiony na kopii wznowił workera automatyzacji, bo kopia
  przejęła z prawdziwej bazy stan „running”.
  - Kopia miała tryb offline, więc wszystkie zadania sieciowe zostały pominięte (wyniki, składy,
    STS, UEFA, prognozy, raport).
  - Worker pracował wyłącznie na kopii i zatrzymał się razem z serwerem („serwer aplikacji
    zakończył pracę”).
  - Przed powtórką w kopii zapisano polecenie „stop”. Prawdziwa baza i prawdziwy worker nie
    były dotykane.
- **Kupon dnia** na kopii (07.11) pokazuje „Brak zapisanego kuponu — stare kursy” (ostatni
  dokument sprzed 18,2 h). „Ryzyko dnia” pojawia się dopiero przy co najmniej 2 kuponach, więc
  na kopii go nie ma; w DEMO jest widoczne.
- **Modele** na kopii: championy v2 ze statusami bez zmian, challengery „czeka na decyzję”.

## 9. Ograniczenia

- **Bramka v3 wymaga Twojej akceptacji.** Bez holdoutu jedynym niezależnym sprawdzianem jest tryb
  cienia, a 300 wspólnych meczów to tygodnie zbierania danych.
- **Walk-forward** używa tych samych sił drużyn as-of dla obu wariantów i porównuje wyłącznie
  hiperparametry i kalibratory.
  - Wynik dla 5 czołowych lig (Dixon–Coles gorszy) pokazuje, że wybór na 12 miesiącach bywa
    niestabilny.
  - Mecze z jednej kolejki nie są w pełni niezależne (bootstrap po meczach).
- **Warunki przeglądu tygodniowego są bez zmian** (150 nowych meczów, brak czekającego
  challengera, 7 dni od treningu). Pierwszy challenger v3 w aplikacji powstanie przyciskiem
  „Trenuj teraz” albo przy przeglądzie, gdy warunki zostaną spełnione (przy włączonym treningu w
  automatyzacji). Warunków nie poluzowano, bo kodują je testy TASK 007/011.
- **Pokrycie prognozami** nadal zależy od dopasowania nazw i świeżości wyników: mecz bez
  zweryfikowanego dopasowania albo z danymi starszymi niż 21 dni nie dostaje prognozy, i tak ma
  być.
- **Wiek kursu „po”** jest oczekiwany, a nie zmierzony. Dzienny pułap 12 nie wystarcza w
  najbardziej zatłoczone niedziele: 01.11 miało 11 grup meczów, czyli 21 slotów bez pułapu.
  Zostaje wtedy 12 slotów — najpierw pobrania 45 min przed meczem.
- **Ryzyko dnia z macierzy** dotyczy tylko nowych prognoz Dixon–Coles. Elo 1X2 i stare wiersze
  zostają przy jawnie oznaczonej niezależności. Parametry są brane z najnowszej prognozy tej wersji
  dla meczu, a nie z wiersza użytego przy budowie kuponu (noga kuponu nie ma identyfikatora
  prognozy).
- **Raport tygodniowy** alarmuje tylko o rynkach kuponowych wersji aktywnych. Pozostałe flagi są
  we wpisie historii.

## 10. Proponowany TASK 013

1. **Decyzja o metodologii v3** (akceptacja bramki „tryb cienia”), a potem pierwsze challengery
   v3 w aplikacji i obserwacja dowodów (n/300).
2. **Stabilniejszy wybór Dixon–Coles w 5 czołowych ligach:** wybór okresu połowicznego zaniku na
   dłuższym oknie (np. 24 mies.) albo z karą za zmianę między retreningami. Sprawdzić walk-forward
   na tych samych meczach, zwłaszcza O/U 1,5.
3. **Potwierdzić wiek kursów po trybie „w dniu meczu”** na prawdziwych pobraniach (mediana,
   udział ≤ 3 h, liczba pobrań, pułap w niedziele), a potem porównanie z rynkiem w przedziale ≤ 3 h.
4. **Wariant B (rekalibracja na rozliczeniach)** jako pomiar, gdy zakres przekroczy 300
   rozliczonych meczów.
5. **Identyfikator prognozy w nodze kuponu**, aby „Ryzyko dnia” zawsze korzystało z tej samej
   prognozy, z której zbudowano kupon.
