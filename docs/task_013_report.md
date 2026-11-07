# Raport TASK 013 — od challengera do decyzji: stabilny Dixon–Coles, kupon z identyfikatorem prognozy, diagnoza profilu WYSOKA TRAFNOŚĆ

Data pracy: 07.11.2026. Kod bazowy: `69f4c85` (TASK 012). Wszystkie pomiary na prawdziwych
danych wykonano na **kopiach** prywatnej bazy (`storage.snapshot_file`, katalog tymczasowy);
po pomiarach kopie usunięto. Prawdziwa baza nie była otwierana nowym kodem. W raporcie są
wyłącznie liczby zbiorcze — bez nazw meczów. Raport nie zawiera rekomendacji zysku ani stawek.
Żaden model nie został aktywowany, bramka v3 nie została zaakceptowana w Twoim imieniu, progi
profili, polityka walidacji i statusy modeli są bez zmian.

## 0. Najważniejsze w skrócie

- **Poprawka awarii workera scalona** (osobny commit przy „Commit i push”): `runtime.json`
  zapisywany przez własny plik tymczasowy, ponowienia przy blokadzie Windows, błąd zapisu
  statusu nie zatrzymuje workera. `tests/test_runtime_write.py`: 6/6.
- **Dixon–Coles w 5 czołowych ligach.** Żadna reguła wyboru okresu połowicznego zaniku przy
  kalibratorach z 12 miesięcy nie spełnia kryterium „nigdzie istotnie gorzej od v2” — nawet
  **stały** okres championa v2 (480 dni) jest istotnie gorszy w O/U 1,5 i O/U 2,5. Przyczyną
  są więc 12-miesięczne kalibratory, nie wybór okresu. Z **kalibratorami z 24 miesięcy**
  i regułą **kary za zmianę** (`sticky12`) żadna grupa rynków nie jest istotnie gorsza
  (wszystkie rynki: −0,0004, 95%: −0,0023…+0,0017). W 7 ligach ta sama reguła jest najlepsza
  (−0,0062, 95%: −0,0100…−0,0030). Ten wariant znaleziono w tym pomiarze — o aktywacji nadal
  decyduje tryb cienia (300 wspólnych meczów).
- **Flaga „zbyt ostrożna” (Elo, podwójna szansa 70–80%) się nie potwierdza** na 3268–4959
  meczach historii: w 5 ligach zarówno v2, jak i v3 są „zgodne z prognozą”, w 7 ligach v2 jest
  „zbyt optymistyczny”, v3 „zgodny”. Flaga z raportu tygodniowego opiera się na 37 meczach.
- **Każda nowa noga kuponu z prognozą ma `prediction_id`** (kreator, Mój kupon, Historia,
  Diagnostyka; Tasiemka i Kupon na kurs tylko wypełniają Mój kupon). „Ryzyko dnia” bierze
  λ, μ, ρ z prognozy nogi; stare nogi mają jawnie oznaczony fallback. Tożsamość kuponu bez zmian.
- **WYSOKA TRAFNOŚĆ:** w 20 decyzjach as-of (01–07.11, godz. 10/14/18) **0 kuponów**.
  Wiążące warunki: w **12 z 20** decyzji żaden typ w przedziale kursu nie kwalifikował się do
  kreatora — w 7 przez kurs starszy niż 3 h (albo trwający mecz), w 5 nie było w przedziale
  typów z prognozą; w pozostałych 8 zwalidowane typy w przedziale miały ostrożną szansę
  najwyżej 0,70–0,75 (0 z 98 ≥ 0,75). Challenger v3 w trybie
  cienia dałby **1 kupon** (SOLO, ostrożnie 76,4%). Opcje do Twojej decyzji w §5.4 — żadna nie
  jest wdrożona.
- **Kupon dnia** pokazuje przy „Brak propozycji” WYSOKIEJ TRAFNOŚCI jedną linię „najbliżej: …”
  (mecz, typ, kurs, ostrożnie X%, status) albo „brak kandydatów w przedziale kursu” z powodem.
- **Bramka v3 to Twoja zapisana decyzja**: Modele → Uczenie AI → „Bramka protokołu v3”
  (potwierdzenie + przycisk). Zapis i wycofanie to nowe wpisy `model_events`
  (`actor = 'user'`). Bez akceptacji aktywacja challengera v3 jest zablokowana z powodem
  „metodologia v3 czeka na Twoją akceptację”; z akceptacją nadal wymaga dowodów (n/300)
  i osobnej decyzji. Na kopii: brak decyzji (0 wpisów) — nic nie zaakceptowano za Ciebie.
- **Wiek kursów po trybie „w dniu meczu”: za mało danych (0 dni z pobraniami w dniu meczu).**
  Twoja aplikacja i worker nadal działają na kodzie sprzed TASK 012 (baza w v9), więc tryb ani
  razu nie pobrał oferty. Raport tygodniowy zapisuje od teraz te liczby co tydzień.

## 1. Diagnoza (07.11.2026, kopia bazy z 17:11, kod `69f4c85`)

1. **Poprawka workera** leżała niezatwierdzona w `claude/optimistic-hugle-7fa6d3` (baza
   `522dfda`): `automation.py` +46/−16, `tests/test_runtime_write.py` (6 testów). Potwierdzone.
2. **Walk-forward v3 vs v2** (TASK 012): w 5 ligach Dixon–Coles v3 gorszy (+0,0050), głównie
   O/U 1,5 (+0,0098). Pomiar TASK 013 rozszerzony o team totals: v3 (wybór na 12 mies.) jest też
   istotnie gorszy w team totals (+0,0014, 95%: +0,0001…+0,0027). Okres połowicznego zaniku
   zmieniał się w 5 ligach 9 razy w 24 retreningach (120 ↔ 240 ↔ 480 dni).
3. **Flaga „zbyt ostrożna”** (Elo, podwójna szansa 70–80%, 37 meczów) — patrz §3.3.
4. **WYSOKA TRAFNOŚĆ**: od 02.11 (nazwy profili TASK 007) w bazie nie ma żadnego kuponu tego
   profilu; zapisane wiersze kuponów kreatora (z wersjami): STANDARDOWY 6, WYŻSZA WYPŁATA 12,
   TASIEMKA 1.
5. **`coupon_legs.prediction_id`**: pusty w 68 z 68 nóg (potwierdzone). Te nogi zostają bez
   zmian (baza append-only).
6. **Bramka v3**: brak mechanizmu zapisu decyzji — potwierdzone; brak wpisu w `model_events`.
7. **Tryb „w dniu meczu”**: prawdziwa baza jest w schemacie v9 — aplikacja użytkownika nie była
   uruchomiona ponownie na kodzie TASK 012. W `job_runs` jest 11 przebiegów `sts_offer`, żaden
   w trybie „w dniu meczu”.

## 2. Scalenie poprawki workera (P0)

- Zmiany z worktree `optimistic-hugle-7fa6d3` przeniesiono scaleniem trójdrożnym (baza
  `522dfda`, po ujednoliceniu końców linii): **0 konfliktów**, `automation.py` +46/−16 jak w
  oryginale; tamten worktree nie był edytowany. Zachowane są oba zachowania: zapis
  `runtime.json` przez własny plik tymczasowy (`NamedTemporaryFile`), ponowienia przy
  `PermissionError`, `_beat_lock` w `Worker.beat`, log błędów wątku heartbeat — oraz zadania
  TASK 012 (`forecasts`, `calibration_report`, tryb „w dniu meczu”).
- `tests/test_runtime_write.py`: **6 passed** (w tym dwa procesy × 300 zapisów i scenariusz
  awarii z 06.11). Pełny pytest zaraz po scaleniu: 390 passed, 4 skipped.
- Przy poleceniu „Commit i push” ta zmiana idzie **osobnym, pierwszym commitem** (tylko
  `automation.py` w wersji scalonej i nowy plik testu); TASK 013 w drugim commicie.

## 3. Stabilny Dixon–Coles w protokole v3 (P0)

### 3.1 Warianty i metoda

Te same mecze co walk-forward TASK 012 (retrening 1. dnia każdego miesiąca od 07.2024, tylko
wyniki dostępne przed miesiącem; zamrożony champion v2 na tych samych siłach drużyn as-of).
Różnica = log loss v3 − v2 na mecz, bootstrap po meczach (1000, ziarno 7); ujemna = v3 lepszy.
Kod: `rolling.HALF_LIFE_RULES`, `rolling.dc_train(rule=…)`,
`sports-edge research walk-forward-v3 --rules select12,select24,sticky12,sticky24,fixed`.

| Reguła | Opis |
|---|---|
| `select12` | najniższy wynik na oknie selekcji 12 mies. (TASK 012) |
| `select24` | (a) wybór na 24 mies.: dwa kolejne bloki 12 mies., każdy oceniany kalibratorami z 12 mies. przed nim (poza próbą); kalibratory modelu z okna 12 mies. |
| `sticky12` | (b) kara za zmianę: inna wartość niż poprzedni wybór tylko, gdy górna granica 95% (bootstrap po meczach) różnicy log loss < 0 |
| `sticky24` | (b) na 24 mies. |
| `fixed` | (c) stały okres championa v2, ruchome tylko kalibratory |

Grupa „team totals” (nowa w TASK 013): gole gospodarza i gościa 0,5 i 1,5 — serwowane bez
kalibracji, więc w regule `fixed` identyczne z v2.

### 3.2 Wyniki — 5 czołowych lig, kalibratory z 12 mies. (3910 meczów)

| Reguła | Wszystkie rynki | 1X2 | O/U 1,5 | O/U 2,5 | BTTS | Team totals | Zmiany okresu (24 retreningi) |
|---|---|---|---|---|---|---|---|
| select12 | **+0,0050** (+0,0015…+0,0087) | +0,0008 | **+0,0098** (+0,0022…+0,0205) | +0,0044 | +0,0050 | **+0,0014** (+0,0001…+0,0027) | 9 |
| select24 | **+0,0049** (+0,0018…+0,0087) | +0,0004 | **+0,0092** | **+0,0051** | **+0,0052** | **+0,0012** | 11 |
| sticky12 | **+0,0055** (+0,0017…+0,0103) | −0,0024 | **+0,0080** | **+0,0085** | **+0,0076** | +0,0002 | 2 |
| sticky24 | **+0,0069** (+0,0027…+0,0116) | +0,0023 | **+0,0066** | **+0,0114** | **+0,0077** | **+0,0010** | 2 |
| fixed (480 dni) | **+0,0053** (+0,0015…+0,0102) | −0,0022 | **+0,0075** (+0,0001…+0,0179) | **+0,0080** (+0,0004…+0,0191) | +0,0072 (0,0000…+0,0166) | 0 | 0 |

Pogrubione = istotnie gorzej niż v2 (dolna granica > 0). **Żadna reguła nie przechodzi** — także
`fixed`, w którym jedyną różnicą wobec v2 są kalibratory. Wniosek: w 5 ligach szkodzą
kalibratory dopasowane na 12 miesiącach, a nie wybór okresu połowicznego zaniku.

**Dodatkowy wariant (d): kalibratory z 24 mies.** — okno kalibracji [T − 36, T − 12 mies.),
selekcja bez zmian [T − 12 mies., T):

| 5 lig, kalibracja 24 mies. | Wszystkie rynki | 1X2 | Podw. szansa | O/U 1,5 | O/U 2,5 | O/U 3,5 | BTTS | Team totals |
|---|---|---|---|---|---|---|---|---|
| sticky12 (= fixed: okres zawsze 480 dni) | −0,0004 (−0,0023…+0,0017) | −0,0038 (−0,0102…+0,0003) | −0,0024 (−0,0066…+0,0001) | +0,0001 (−0,0020…+0,0023) | +0,0029 (−0,0015…+0,0105) | −0,0006 | −0,0007 (−0,0020…+0,0007) | 0 |
| Elo w tym wariancie (margines remisu z 24 mies.) | +0,0002 (−0,0015…+0,0019) | — | +0,0002 (−0,0007…+0,0011) | | | | | |

Wszystkie grupy: **brak istotnej różnicy**. Wariant nie jest lepszy od v2, ale przestaje być
gorszy — a challenger może się różnić od championa (inne kalibratory, Elo k = 20 i przewaga 60
zamiast 15/40), więc tryb cienia ma co mierzyć.

**7 lig, kalibratory z 12 mies. (5521 meczów DC, 5572 Elo)** — wszystkie reguły v3 są lepsze
łącznie, żadna nie jest istotnie gorsza w żadnej grupie:

| Reguła | Wszystkie rynki | 1X2 | O/U 1,5 | O/U 2,5 | BTTS | Team totals | Zmiany okresu (28) |
|---|---|---|---|---|---|---|---|
| select12 | −0,0057 (−0,0097…−0,0021) | −0,0128 | −0,0105 | +0,0040 | −0,0096 | 0,0000 | 4 |
| select24 | −0,0052 (−0,0093…−0,0014) | −0,0131 | −0,0106 | +0,0042 | −0,0094 | 0,0000 | 11 |
| **sticky12** | **−0,0062 (−0,0100…−0,0030)** | −0,0120 (−0,0209…−0,0050) | −0,0112 (−0,0212…−0,0029) | +0,0025 (−0,0009…+0,0084) | −0,0095 (−0,0185…−0,0024) | 0 | 0 |
| sticky24 | −0,0053 (−0,0093…−0,0015) | −0,0135 | −0,0109 | +0,0042 | −0,0087 | +0,0003 | 1 |
| fixed | −0,0062 (jak sticky12) | −0,0120 | −0,0112 | +0,0025 | −0,0095 | 0 | 0 |

Elo w 7 ligach: −0,0038 (−0,0063…−0,0010), v3 lepszy.

**Wybrana reguła** (`config/protocol_v3.toml`, wersja polityki `2026-11-07.v3b`):

- `[dixon_coles] half_life_rule = "sticky12"` — kara za zmianę; poprzedni wybór to najnowsza
  wersja v3 zakresu, a bez niej okres championa. Uzasadnienie: w żadnym zakresie ani grupie nie
  jest istotnie gorsza od v2 (przy kalibracji z 24 mies. w 5 ligach), w 7 ligach daje najlepszy
  wynik łączny i jest najstabilniejsza (w końcowej konfiguracji 0 zmian w 24 + 28 retreningach;
  zmiana możliwa, ale tylko z dowodem).
- `[windows.scopes.core] calibration_months = 24` — 5 czołowych lig kalibrują na 24 miesiącach;
  7 lig zostaje przy 12 (krótsza historia; tam v3 jest lepszy).
- Kontrolny walk-forward przez zwykłą ścieżkę (`services.run_walk_forward_v3`, konfiguracja z
  pliku) dał dokładnie te liczby: 5 lig 98 s, 7 lig 89 s.
- **Uczciwie:** wariant (d) wyszedł z tego samego pomiaru, w którym go wybrano (porównano 7
  wariantów na tych samych meczach). Kryterium jest ochronne („nie gorzej”), a nie „lepiej”,
  i aktywacja nadal wymaga 300 wspólnych meczów w trybie cienia oraz Twojej decyzji. Jeżeli
  wolisz wariant zachowawczy ze specyfikacji, wystarczy usunąć sekcję `[windows.scopes.core]`:
  wtedy DC v3 w 5 ligach będzie istotnie gorszy (tabela wyżej) i należałoby go nie aktywować.

### 3.3 Niezawodność 70–80% (na meczach, końcowa konfiguracja)

Metoda TASK 011 (Wilson 95% na meczach, flaga od 30 meczów), te same mecze dla v2 i v3.

| Zakres | Model, rynek | v2: mecze, szansa / trafność, ocena | v3: mecze, szansa / trafność, ocena |
|---|---|---|---|
| 5 lig | Elo, podwójna szansa | 3348: 74,4% / 73,9% — zgodna | 3268: 74,0% / 73,8% — zgodna |
| 5 lig | Elo, 1X2 | 303: 74,5% / 76,6% — zgodna | 310: 74,5% / 76,1% — zgodna |
| 5 lig | Dixon–Coles, podwójna szansa | 3449: 75,3% / 74,3% — zgodna | 3250: 74,0% / 74,1% — zgodna |
| 5 lig | Dixon–Coles, 1X2 | 152: 74,6% / 80,9% — zgodna | 292: 73,3% / 76,7% — zgodna |
| 7 lig | Elo, podwójna szansa | 4757: 74,6% / 72,9% — **zbyt optymistyczna** | 4959: 73,8% / 72,7% — zgodna |
| 7 lig | Elo, 1X2 | 334: 74,9% / 72,2% — zgodna | 307: 74,6% / 73,3% — zgodna |
| 7 lig | Dixon–Coles, podwójna szansa | 4403: 74,7% / 73,2% — **zbyt optymistyczna** | 4525: 73,9% / 73,4% — zgodna |
| 7 lig | Dixon–Coles, 1X2 | 296: 75,0% / 64,2% — **zbyt optymistyczna** | 352: 74,6% / 72,2% — zgodna |

Flaga „zbyt ostrożna” z §1.3 **nie pojawia się w historii** ani dla v2, ani dla v3 — opiera się
na 37 prospektywnych meczach (95%: 73,6–95,0%). Na tysiącach meczów podwójna szansa 70–80% jest
zgodna (5 lig) albo lekko optymistyczna w v2 (7 lig). W v3 wszystkie komórki są zgodne.

### 3.4 Zapis reguły w wersji

- Nowe wersje Dixon–Coles v3 zapisują w `validation_json` i `training_data_json` pole
  `half_life_rule`: reguła, opis, poprzedni wybór, najlepszy kandydat, przedział bootstrap,
  czy zmieniono, liczba bloków. Stare wersje bez zmian.
- Okno kalibracji 5 lig widać w `split` (`calibration_months = 24`, reguła „24 + 12 mies.”).

## 4. Identyfikator prognozy w nodze kuponu (P0)

- `Candidate.prediction_id` — id prognozy, z której pochodzi szansa (podwójna szansa wyprowadzona
  z 1X2 wskazuje prognozę 1X2); bez prognozy pusty.
- Ścieżki zapisu: kreator (`coupon_store.save_builder` / `save_proposals`), Mój kupon
  (`save_my_coupon_result`), Historia → „Zapisz kupon” (kandydaci dnia), Diagnostyka (kandydat
  rankingu). Tasiemka i Kupon na kurs niczego nie zapisują — tylko dodają typy do Mojego kuponu,
  więc też trafiają do zapisu z identyfikatorem. Zapis propozycji (`proposal_json`) też ma
  `prediction_id` każdej nogi.
- **Tożsamość kuponu bez zmian**: `prediction_id` nie wchodzi do klucza ani do sygnatury kursów;
  nowsza prognoza tej samej selekcji to ta sama tożsamość („Bez zmian — kupon dnia jest
  aktualny”), podwójne kliknięcie nadal daje 1 kupon.
- **Ryzyko dnia**: λ, μ, ρ z prognozy każdej nogi (`insights.prediction_key`), nawet gdy istnieje
  nowsza prognoza meczu. Typy jednego meczu z prognoz o różnych parametrach → jawna niezależność
  („typy z różnych prognoz”). Stara noga bez identyfikatora → najnowsza prognoza wersji,
  oznaczona: „FALLBACK: najnowsza prognoza tej wersji dla meczu (stara noga bez identyfikatora
  prognozy)”.
- **Analiza wyniku kuponu** (Historia) podaje źródło szans: prognoza zapisana przy nodze albo
  FALLBACK — szansa z zapisu propozycji.

**Na kopii:** 68 starych nóg bez identyfikatora zostało bez zmian. Kreator na 07.11 (13:05,
świeże kursy) zbudował te same 3 kupony, które były już zapisane (STANDARDOWY, WYŻSZA WYPŁATA,
TASIEMKA) → „duplikat”, 0 nowych wierszy (reguła tożsamości); na 08.11 nie było kandydatów. Nowe
kupony zapisane na kopii: Historia → „Zapisz kupon” (3 nogi, 2 z prognozą → **2/2 z
`prediction_id`**, trzecia pusta) i Mój kupon (2 nogi → **2/2**, identyfikatory zgodne z
kandydatami). Ścieżka kreatora jest pokryta testem na danych syntetycznych.

## 5. Diagnoza profilu WYSOKA TRAFNOŚĆ (P1, pomiar — progi bez zmian)

Profil: kurs kuponu 1,25–1,60, do 2 nóg, ostrożna szansa nogi ≥ 75%, kuponu ≥ 65%, zysk netto
≥ 8% stawki, walidacja predykcyjna. Kod pomiaru: `simulation.high_hit_diagnosis` (tylko odczyt).

### 5.1 Replay as-of na zapisanych prognozach

20 decyzji: 01–07.11, godz. 10, 14, 18 czasu polskiego (07.11 18:00 jest po chwili kopii).
W każdej chwili tylko oferta, kursy i prognozy znane wtedy; prognozy wersji **serwowanych w tej
chwili** (do 02.11 rano v1, potem v2).

| Wiążący warunek (`refusal_codes`) | Decyzje |
|---|---|
| `no_candidates` — żaden typ nie kwalifikuje się do kreatora | 12 |
| `low_probability` — brak typów z ostrożną ≥ 75% | 4 |
| `payout` — brak kombinacji w przedziale kursu z zyskiem ≥ 8% | 3 |
| `coupon_probability` — kombinacje są, ostrożna szansa kuponu < 65% | 1 |
| kupon | **0** |

**Dlaczego „brak kandydatów”:** typy w przedziale kursu z prognozą były, ale nie kwalifikowały
się: 402 razy (typ × decyzja) kurs niepotwierdzony — ostatni dokument STS starszy niż limit 3 h,
90 razy mecz już trwał (sumy typ × decyzja ze wszystkich 20 decyzji). Spośród 12 decyzji bez
kandydatów: w 5 wszystkie typy w przedziale miały za stary kurs, w 2 część meczów już trwała, a
reszta miała stary kurs, w 5 nie było w przedziale żadnego typu z prognozą (01.11 rano — przed
pierwszymi zapisanymi prognozami; 05.11 — bez meczów 12 lig z prognozą).

**Ostrożna szansa typów w przedziale 1,25–1,60 ze świeżym kursem** (8 decyzji; typ × decyzja,
w nawiasie typy unikalne):

| Status | < 0,60 | 0,60–0,65 | 0,65–0,70 | 0,70–0,75 | ≥ 0,75 |
|---|---|---|---|---|---|
| zwalidowany | 49 (33) | 12 (10) | 31 (19) | 6 (4) | **0** |
| eksperymentalny | 69 (53) | 35 (28) | 100 (78) | 15 (11) | 1 (1) |

**Luka szansa modelu → ostrożna** (punkty procentowe, mediana; typy unikalne):

| Rodzaj dolnej granicy | Typy | Mediana | Według liczności próbki kalibracji |
|---|---|---|---|
| Elo, podwójna szansa (suma dolnych granic **dwóch** wyników 1X2) | 110 | **6,7** | 300–1000: 6,8 (96); ≥ 1000: 4,9 (13) |
| Elo, 1X2 | 10 | 5,6 | < 100: 10,8; 100–300: 5,6; 300–1000: 4,5 |
| Dixon–Coles, O/U 2,5 | 24 | 6,0 | < 100: 10,7; 100–300: 7,1; 300–1000: 3,7 |
| Dixon–Coles, BTTS | 21 | 5,6 | < 100: 8,7; 100–300: 5,6; 300–1000: 2,9 |
| Dixon–Coles, O/U 1,5 | 24 | 3,8 | 100–300: 5,6; 300–1000: 3,7 |
| Dixon–Coles, O/U 3,5 | 48 | 3,5 | < 100: 9,4; 100–300: 5,5; 300–1000: 3,5 |

Zwalidowane typy w przedziale kursu to głównie podwójna szansa Elo. Jej dolna granica to suma
dolnych granic dwóch wyników 1X2 — dwa razy odejmuje połowę szerokości przedziału (każda co
najmniej ECE). Dixon–Coles liczy podwójną szansę z dopełnienia **jednego** wyniku. Przy
typowej szansie modelu 0,80–0,82 granica Elo ląduje na 0,73–0,75 — tuż pod progiem.

**Inna definicja (pomiar, nie wdrożona):** podwójna szansa Elo jak w Dixon–Coles
(1 − górna granica wykluczonego wyniku) dałaby 3 zwalidowane typy ≥ 0,75 (typ × decyzja) i
**1 decyzję z kuponem** (ostrożnie 76,9%).

### 5.2 Pomiar „cienia” challengera v3 (bez zapisu w prawdziwej bazie)

Na drugiej kopii: challengery v3 (końcowa reguła z §3) wytrenowane 01.11 o 09:00 czasu polskiego
— wyłącznie na danych znanych przed pierwszą decyzją (trening oznaczony jako wymuszony, bo bez
nowych meczów od championa). Potem w każdej z 20 chwil prognozy championa (v2) **i** v3 dla tych
samych meczów, a kreator dwa razy. Pokrycie typów jest identyczne w każdej decyzji, więc
różnica to model, nie liczba prognoz.

| | Champion v2 (prognozy automatyczne) | Challenger v3 |
|---|---|---|
| Kupony WYSOKIEJ TRAFNOŚCI | 0 | **1** (SOLO, kurs 1,30, ostrożnie 76,4%, model 81,3%, zwalidowany) |
| Kody odmów | brak kandydatów 12, niska szansa 4, wypłata 3, szansa kuponu 1 | brak kandydatów 12, wypłata 3, **brak walidacji** 4, kupon 1 |
| Zwalidowane w przedziale, ostrożna ≥ 0,75 | 0 | 1 |
| Eksperymentalne w przedziale, ostrożna ≥ 0,75 | 1 | 13 |
| Luka — Elo podwójna szansa | 6,7 pkt | 4,5 pkt |
| Luka — DC BTTS / O/U 2,5 / O/U 1,5 | 5,6 / 6,0 / 3,8 | 2,8 / 3,5 / 3,3 |
| Inna definicja dla Elo (pomiar) | 1 decyzja z kuponem (76,9%) | 1 decyzja (79,3%) |

- Okna v3 w chwili treningu: 5 lig kalibracja od 01.11.2023 (24 mies.), 7 lig od 01.11.2024;
  okres połowicznego zaniku 480 dni w obu zakresach (w 7 ligach najlepszy był 240, ale przedział
  −0,0047…+0,0027 nie dowodzi poprawy — reguła zostawiła 480).
- Statusy w oknie selekcji v3 (informacja): 1X2 i podwójna szansa zwalidowane, rynki bramkowe
  eksperymentalne — dlatego 4 decyzje v3 kończą się „brak walidacji” (typy ≥ 0,75 są, ale
  eksperymentalne).
- Kupon v3 pochodzi z decyzji 07.11 14:00 — jedynej z dużą ofertą i świeżymi kursami. To jedna
  obserwacja, nie dowód.

### 5.3 „Najbliżej: …” w Kuponie dnia

- Przy „Brak propozycji” WYSOKIEJ TRAFNOŚCI (i pod wyjaśnieniem „Brak kuponu”, gdy żaden profil
  nie ma propozycji) jedna linia: typ w przedziale kursu profilu z najwyższą ostrożną szansą
  spośród typów kwalifikujących się do kreatora — mecz, typ, kurs, „ostrożnie X% (próg nogi
  75,0%)”, status walidacji.
- Bez kandydatów: „najbliżej: brak kandydatów w przedziale kursu”, a gdy typy są, ale nie
  kwalifikują się — ich liczba i najczęstszy powód (np. „kurs niepotwierdzony (stara migawka)”).
- Bez nowej karty, bez zmiany progów i propozycji (`coupon_builder.closest` tylko czyta).
- Na kopii (te same 20 decyzji): w 12 linia mówi „brak kandydatów w przedziale kursu” (w 7 z
  powodem „kurs niepotwierdzony” albo „wydarzenie nie jest przed rozpoczęciem”), w 8 wskazuje
  typ z ostrożną szansą 72,0–76,0% (eksperymentalny albo zwalidowany z 72,7%).

### 5.4 Opcje do Twojej decyzji (żadna nie jest wdrożona)

| Opcja | Co by zmieniła (liczby z kopii) | Koszt / ryzyko |
|---|---|---|
| A. Świeże kursy: uruchomić aplikację na kodzie TASK 012/013 i zgodę na pobieranie (tryb „w dniu meczu”) | w 7 z 20 decyzji typy w przedziale były, ale z kursem starszym niż 3 h (albo w trakcie meczu); tryb planuje pobrania 120 i 45 min przed meczami | bez zmiany modeli i progów; do zmierzenia (§7) |
| B. Poczekać na challenger v3 (akceptacja bramki + 300 wspólnych meczów) | w cieniu: 1 kupon na 20 decyzji (0 u championa), mniejsza luka (Elo podw. szansa 6,7 → 4,5 pkt) | dowody: dziś 0/300; tempo rozliczeń na kopii ~20 meczów/tydz. (5 lig) i ~32 (7 lig), z automatycznymi prognozami więcej |
| C. Rekalibracja na rozliczeniach (wariant B TASK 012) po 300 meczach | dziś rozliczone mecze aktywnych wersji: 20 (5 lig) i 32 (7 lig) | przy obecnym tempie ok. 14 tyg. (5 lig) i 8–9 tyg. (7 lig) |
| D. Inna definicja szansy ostrożnej dla podwójnej szansy Elo (jeden przedział, jak Dixon–Coles) | +3 zwalidowane typy ≥ 0,75, 1 decyzja z kuponem (76,9%) | węższa granica = mniejszy margines bezpieczeństwa; wymaga sprawdzenia pokrycia przedziału na historii przed decyzją |

Progi profilu (0,75 / 0,65 / 8%) nie są opcją tego zadania i zostały bez zmian.

## 6. Bramka v3 — zapisana decyzja użytkownika (P1)

- **Gdzie:** Modele → widok „Uczenie AI” → sekcja „Bramka protokołu v3” (stan, wyjaśnienie,
  pole potwierdzenia z pełną treścią decyzji i przycisk „Zapisz akceptację bramki v3”). W
  widoku „Podstawowy” Modeli widać stan bramki.
- **Treść decyzji:** „Akceptuję bramkę v3: o aktywacji challengera v3 decyduje porównanie w trybie
  cienia (≥ 300 wspólnych meczów), nie historyczny holdout”.
- **Zapis:** nowy wiersz `model_events` (`kind = 'protocol_gate_accepted'`, `actor = 'user'`,
  treść i `shadow_matches = 300` w `payload_json`). Wycofanie (osobne potwierdzenie, przycisk
  „Wycofaj akceptację bramki v3”) to kolejny wiersz `protocol_gate_withdrawn`; obowiązuje
  najnowszy. Nic nie jest nadpisywane (triggery `immutable_*`). Historia uczenia pokazuje oba.
- **Blokada:** wersja v3, która nigdy nie była serwowana, nie może być aktywowana bez
  akceptacji — `learning.activate` i `services.switch_models` odmawiają z powodem „metodologia v3
  czeka na Twoją akceptację”, a przyciski „AKTYWUJ NOWY MODEL” i „Ustaw wybrane jako aktywne”
  są nieaktywne z tym podpisem. Z akceptacją aktywacja nadal wymaga dowodów — „za mało dowodów w
  trybie cienia (n/300 wspólnych rozliczonych meczów)” — i osobnego kliknięcia z potwierdzeniem.
  Wersje v1/v2 i wcześniej serwowane wersje nie są blokowane (przywracanie działa jak dotąd).
- **System nigdy sam:** `accept_gate` wywołuje wyłącznie przycisk w panelu (test sprawdza każdy
  plik `src/`); trening v3, przegląd tygodniowy i worker nie zapisują `activated`.

## 7. Wiek kursów po trybie „w dniu meczu” (P1)

`prospective_eval.odds_age_report`: mecze 12 lig rozpoczęte w okresie (jeden mecz = jeden wiersz,
także gdy STS pokazał go w kilku dniach oferty); wiek = start − czas z ostatniego dokumentu STS
z kursem meczu przed startem. Dziennie: pobrania automatyzacji (wszystkie i w trybie „w dniu
meczu”), dokumenty STS, plan slotów z pułapem i bez. Od 3 dni z pobraniami w dniu meczu wynik jest
„zmierzone”, wcześniej „za mało danych (n dni…)” — liczby i tak są pokazane, nic nie jest
zgadywane. Raport tygodniowy (`calibration_report`) zapisuje to w polu `odds_age` wpisu.

**Na kopii (01.11–07.11 do 17:00):**

| Dzień | Mecze | Mediana wieku | ≤ 3 h | Pobrania automatyzacji (w dniu meczu) | Dokumenty STS | Sloty planu bez pułapu → z pułapem |
|---|---|---|---|---|---|---|
| 01.11 | 35 | 18,00 h | 0% | 0 (0) | 8 | 21 → **12** |
| 02.11 | 11 | 0,83 h | 100% | 3 (0) | 8 | 6 → 6 |
| 03.11 | 9 | 4,25 h | 0% | 2 (0) | 4 | 4 → 4 |
| 04.11 | 4 | 0,83 h | 100% | 2 (0) | 6 | 4 → 4 |
| 05.11 | 0 | — | — | 1 (0) | 3 | 0 |
| 06.11 | 14 | 2,33 h | 64% | 2 (0) | 6 | 6 → 6 |
| 07.11 (do 17:00) | 32 | 3,58 h | 34% | 1 (0) | 2 | 21 → **12** |
| **razem** | **105** | **4,08 h** | **33%** | **11 (0)** | 37 | pułap uciąłby 2 dni |

- **Stan: „za mało danych (0 dni z pobraniami w dniu meczu; potrzeba 3)”.** Tryb nie działał ani
  razu: Twoja aplikacja i worker pracują na kodzie sprzed TASK 012 (baza w v9). Wiek „po” z
  TASK 012 (mediana 0,8 h) pozostaje oczekiwany, niezmierzony.
- Wpis raportu tygodniowego na kopii zapisał te liczby ze stanem „za mało danych”.
- Pułap 12 pobrań uciąłby sloty w obie niedziele z dużą ofertą (01.11 i 07.11: po 21 slotów).

## 8. Zmiany w kodzie i danych

- `automation.py`: poprawka workera (§2); `job_calibration_report` przekazuje ustawienia do
  raportu tygodniowego.
- `rolling.py`: reguły okresu połowicznego zaniku (`HALF_LIFE_RULES`, `choice_blocks`,
  `match_losses`, `dc_train(rule, previous)`), okna zakresu (`RollingPolicy.for_scope`),
  walk-forward wielu reguł na tych samych meczach (`rules`, `variants`), team totals,
  `range_reliability` (70–80%).
- `config/protocol_v3.toml`: wersja `2026-11-07.v3b`, `[dixon_coles]`, `[windows.scopes.core]`.
  `probability_policy.toml` i `prospective_policy.toml` **bez zmian**.
- `services.py`: `previous_half_life`, `activation_block` i blokada w `switch_models`,
  `day_candidates_at` (silnik `analyse_day` z wyborem wersji — tylko pomiary), parametr `goals` w
  `prediction_preference` i `elo_benchmarks`, `benchmarks` w `with_predictive_status`.
- `learning.py`: rodzaje `protocol_gate_accepted` / `protocol_gate_withdrawn`, `gate_state`,
  `accept_gate`, `withdraw_gate`, `gate_block` w `activate`.
- `analysis/edge_engine.py`: `Candidate.prediction_id`. `analysis/coupon_builder.py`:
  `in_odds_range`, `closest`.
- `coupon_store.py`: `prediction_ids_of`, `prediction_ids` w `save_builder` i `save_manual`,
  `prediction_id` w nogach `proposal_json`.
- `insights.py`: Ryzyko dnia z prognozy nogi (`prediction_key`, `PARAMETER_SOURCES`, źródło w
  `joint`), `closest_text`. `forecast_quality.py`: źródło szans w analizie wyniku kuponu.
- `prospective_eval.py`: `odds_age_report`, pole `odds_age` raportu tygodniowego (nowy stan
  cen lub pobrań = nowy wpis; ten sam stan nic nie dopisuje).
- `simulation.py`: `high_hit_diagnosis`, `calibration_sample`.
- Interfejs: `page_learning` (sekcja bramki, blokady przycisków), `page_models` (stan bramki),
  `page_coupon` + `coupon_view` (linia „najbliżej”), `pages_history` i `diagnostics`
  (`prediction_ids`), `page_coupon` (źródło parametrów Ryzyka dnia).
- `cli.py`: `research walk-forward-v3 --rules …`. `qa.py`: 3 nowe ścieżki.
- **Schemat bez zmian (v10).** Decyzja o bramce mieszka w `model_events`, identyfikator prognozy
  w istniejącej kolumnie `coupon_legs.prediction_id`, wiek kursów we wpisie raportu
  tygodniowego — migracja v11 nie była potrzebna. Żaden istniejący rekord nie został zmieniony
  ani usunięty (68 starych nóg nadal bez identyfikatora).

## 9. Testy i kontrole

`tests/test_task013.py` — 18 testów na danych syntetycznych:

1. poprawka workera jest w kodzie, `tests/test_runtime_write.py` ma 6 testów (przechodzą w
   pełnym przebiegu);
2. reguła okresu połowicznego zaniku jest deterministyczna i zapisana (`sticky12`, `fixed`,
   `select24` z dwoma blokami);
3. kara za zmianę zatrzymuje poprzednią wartość przy nieistotnej poprawie i zmienia ją przy
   istotnej;
4. bloki wyboru nigdy nie używają wyników spoza swoich okien;
5. walk-forward: zmiana wyników znanych po T nie zmienia okresu ani kalibratorów żadnej reguły
   (kontrola: zmiana w oknie kalibracji zmienia);
6. plik protokołu zawiera wybraną regułę i okna 5 lig;
7. każda ścieżka zapisu (kreator, Diagnostyka, Historia, Mój kupon) zapisuje identyfikator
   prognozy kandydata, noga bez prognozy ma pusty, nowsza prognoza = ta sama tożsamość,
   podwójne kliknięcie = 1 kupon;
8. `prediction_id` nie wchodzi do tożsamości kuponu;
9. Ryzyko dnia z prognozy nogi mimo nowszej prognozy meczu; fallback z oznaczeniem; różne
   prognozy jednego meczu → niezależność;
10. analiza wyniku kuponu podaje źródło szans;
11. „najbliżej”: najwyższa ostrożna szansa w przedziale kursu, bez zmiany progów i propozycji,
    „brak kandydatów w przedziale kursu” z powodem;
12. diagnoza WYSOKIEJ TRAFNOŚCI niczego nie zapisuje;
13. bramka v3: bez akceptacji blokada z powodem, akceptacja i wycofanie to nowe wpisy, potem
    wymagane dowody n/300, champion bez zmian;
14. system nigdy sam nie akceptuje bramki ani nie aktywuje modelu;
15. wiek kursów: liczby na danych syntetycznych, „za mało danych” bez pobrań, zapis w raporcie
    tygodniowym bez duplikatu;
16. pułap dzienny raportowany, gdy ucina sloty;
17. schemat bez zmian, triggery nadal odrzucają `DELETE`/`UPDATE` w `model_events`;
18. AppTest: Kupon dnia pokazuje „najbliżej”, Modele pokazują i zapisują decyzję o bramce.

**Zmienione istniejące testy** (żaden nie został wyłączony): `test_ui_paths.py` oczekuje 3
nowych ścieżek QA. Kod testowano z worktree (`conftest` wstawia własny `src`; dla QA i
Playwright ustawiono `PYTHONPATH=<worktree>\src`, `sports_edge.__file__` sprawdzony).

**Przegląd ręczny zrzutów z kopii** (zrzutów nie dodano do repozytorium; usunięte z kopią):
Kupon dnia pokazuje pod „Brak propozycji” WYSOKIEJ TRAFNOŚCI linię „najbliżej: brak kandydatów w
przedziale kursu (w przedziale 166 typów wykluczonych, najczęściej: wydarzenie nie jest przed
rozpoczęciem — 166)” (wieczór 07.11 na kopii); Modele (Podstawowy) — stan „Bramka v3: metodologia
v3 czeka na Twoją akceptację”; Uczenie AI — sekcja „Bramka protokołu v3” z polem potwierdzenia.
Przed QA na kopii zapisano w niej tryb offline i polecenie „stop” dla workera; na kopii nie
zapisano żadnej decyzji o bramce (0 wpisów) i żadnej aktywacji.

| Kontrola | Wynik |
|---|---|
| `pytest -q` | **408 passed, 4 skipped** (412 testów; pominięte: brak lokalnych autentycznych PDF STS), 196 s |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 176 plików sformatowanych |
| `python -m sports_edge.qa` (ścieżki UI, DEMO) | **32/32 OK**, w tym nowe: „modele: bramka v3 (stan)”, „modele: akceptacja bramki v3 (zapis decyzji)”, „kupon dnia: najbliżej (WYSOKA TRAFNOŚĆ)” |
| `python -m sports_edge.qa_visual` (Playwright, DEMO) | **83 kontrole, 0 błędów** (14 stron × 5 rozdzielczości: 1920, 1366, 1024, 768, 390 px + 13 scenariuszy). Pierwszy przebieg przerwało uśpienie komputera: 8 × `net::ERR_NETWORK_IO_SUSPENDED` w 768 px (błąd sieci przeglądarki, nie aplikacji) — powtórka w całości czysta |
| `python -m sports_edge.qa_browser` (Playwright, DEMO) | 22 zrzuty, 0 błędów |
| `qa_visual --home <kopia>`: Modele, Kupon dnia × 5 rozdzielczości | **10 kontroli, 0 błędów** |
| `qa_visual --home <kopia>`: Uczenie AI (1366, 390 px) | 1366: OK; 390: raz zgłoszony pasek narzędzi tabeli („Show/hide columns”) poza ekranem — pojawia się po najechaniu myszą; nie powtórzył się w dwóch kolejnych przebiegach 390 px (Uczenie AI, Modele, Kupon dnia: 3/3 OK) ani w bezpośrednim pomiarze strony |

## 10. Ograniczenia

- **Wariant kalibracji 24 mies. dla 5 lig znaleziono w tym samym pomiarze**, w którym go
  oceniono (7 wariantów na tych samych meczach). Jest „nie gorszy”, nie „lepszy”; decyduje tryb
  cienia.
- **Reguła `sticky12` w praktyce trzyma 480 dni** (na historii nie było istotnej poprawy) — jest
  stabilna, ale nie „uczy się” okresu bez mocnego dowodu.
- **Pomiar „cienia”** to 20 decyzji z jednego tygodnia; 1 kupon v3 to pojedyncza obserwacja.
  Challenger w cieniu był trenowany jako wymuszony (bez nowych meczów od championa).
- **Diagnoza WYSOKIEJ TRAFNOŚCI** zależy od kursów z archiwum: zwykle 2–8 dokumentów dziennie,
  stąd wiele decyzji ze starymi kursami.
- **Wiek kursów** nie jest zmierzony: tryb „w dniu meczu” nie działał. Wymaga uruchomienia
  aplikacji na nowym kodzie i zgody na pobieranie oficjalnych PDF STS.
- Rynki team totals są serwowane bez kalibracji; w strefie ≥ 60% linia 1,5 jest „zbyt
  optymistyczna” w obu wersjach (nie są rynkiem kuponowym — brak dolnej granicy).
- Tożsamość i Ryzyko dnia dla kuponów sprzed TASK 013 korzystają z fallbacku (jawnie oznaczonego).

## 11. Proponowany TASK 014

1. **Pierwsze dni trybu „w dniu meczu”**: po uruchomieniu aplikacji na nowym kodzie zmierzyć wiek
   kursów (raport tygodniowy) i powtórzyć diagnozę WYSOKIEJ TRAFNOŚCI na świeżych kursach;
   rozważyć pułap dzienny w niedziele (21 slotów).
2. **Decyzja o bramce v3** (Twoja) i obserwacja dowodów challengerów v3 (n/300) w obu zakresach.
3. **Pokrycie dolnej granicy na historii**: jak często wynik wypada poniżej granicy
   „ostrożnie” dla podwójnej szansy Elo (suma dwóch przedziałów) i Dixon–Coles (jeden przedział) —
   dane do decyzji o opcji D bez zgadywania.
4. **Wariant B (rekalibracja na rozliczeniach)** jako pomiar, gdy zakres przekroczy 300
   rozliczonych meczów.
5. **Kalibracja team totals** (dziś surowe, w strefie ≥ 60% optymistyczne dla linii 1,5).
