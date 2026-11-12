# TASK 018 — Reprezentacje dokładnie tak jak ligi

**Pomiar kopii danych:** 12.11.2026, ok. 15:15 UTC (16:15 czasu polskiego)
**Decyzje użytkownika (12.11.2026):** „to ma działać tak jak to mamy zrobione dla lig” oraz
(zmiana zakresu w trakcie) „te reżimy musimy zmienić dla reprezentacji bo przecież oni nie grają
tak często jak liga” — reguły zależne od częstotliwości gry są dla reprezentacji osobne
(`config/national_scope.toml`), progi jakości bramki i profili pozostają jak w ligach.

## Diagnoza

Ligi klubowe mają w rejestrze status `experimental` (brak benchmarku rynkowego), a mimo to ich
prognozy są serwowane do tabeli `predictions`. O kuponach decyduje bramka per rynek
`probability_lab.predictive_status` (ECE w oknie selekcji, wynik lepszy od częstości bazowej,
liczebność) i progi profili.

TASK 017 prowadził reprezentacje osobną ścieżką: prognozy cienia w `stat_observations`, do
kreatora dopiero po statusie `validated` rejestru (z benchmarkiem STS) i decyzji użytkownika.
Wynik: 0 kandydatów i 0 kuponów, nawet w WYŻSZEJ WYPŁACIE, gdzie ligi o tym samym statusie dają
kupony-symulacje. Model cienia nie miał kalibracji jak Dixon–Coles lig i przy dużej różnicy sił
był zbyt pewny (Czechy–Hiszpania 94%, San Marino–Białoruś 95%; patrz „Mecze 12–15.11”).

## Co zrównano z ligami

| Krok | Teraz dla reprezentacji |
|---|---|
| zakres modelu | `national` w `services.MODEL_SCOPES` obok `core` i `extended` |
| wersje modeli | Elo i Dixon–Coles rejestrowane tym samym `run_protocol_v2` → `register_version`; pierwsza wersja zakresu jest serwowana (jak w ligach) |
| protokół | v2 z czterema oknami; v3 (challengery) tym samym kodem co ligi |
| kalibracja | margines remisu Elo i mapy izotoniczne Dixon–Coles dopasowane w oknie kalibracji |
| bramka per rynek | `predictive_status` z tymi samymi `rules` (`config/probability_policy.toml`, bez zmian) |
| prognozy | tabela `predictions` (zadanie `forecasts`, „Analizuj”), `prediction_id`, rozliczenie jak w ligach |
| kandydaci | `analyse_day` → kreator, bez warunku specyficznego dla reprezentacji |
| typy eksperymentalne | tylko profil bez wymogu walidacji (WYŻSZA WYPŁATA), etykieta „SYMULACJA — model eksperymentalny” |
| świeżość kursu | dodatkowe pobrania oferty w dniu meczu obejmują też mecze reprezentacji |

Zakres `national` obejmuje: Ligę Narodów UEFA, kwalifikacje EURO, kwalifikacje MŚ, EURO, MŚ i
mecze towarzyskie seniorów. Wszystkie te rozgrywki tworzą **jedną pulę ratingów** (reprezentacja
ma jeden rating niezależnie od rozgrywek); w ligach pulą jest każda liga osobno — bez zmian.
Mecze towarzyskie są w puli: w TASK 017 warianty z towarzyskimi (N2/N3) miały niższy log loss w
selekcji (0,8639 / 0,8642) niż wariant bez nich (N1: 0,8680).

Bramka „per liga” (`by_league`) dla reprezentacji jest liczona dla całej puli (jedna „liga”
modelu): pojedyncza Liga Narodów ma w dwuletnim oknie selekcji mniej niż 300 meczów, więc
bramka per rozgrywki z definicji nie mogłaby przejść. Częstości bazowe pozostają liczone osobno
dla każdych rozgrywek (porównanie jest więc surowsze, nie łagodniejsze).

**Okna protokołu v2 (reprezentacje):** trening < 01.07.2019, hiperparametry 07.2019–06.2021,
kalibracja 07.2021–12.2022, selekcja 2023–2024 (to samo okno co w TASK 017), holdout od 2025
(tylko raportowany). Każde okno ma ≥ 300 meczów, jak wymaga kod lig.

## Reguły zależne od częstotliwości gry (`config/national_scope.toml`)

Ligi grają co tydzień, reprezentacje tylko w oknach FIFA (ok. 10 meczów drużyny rocznie, ok. 300
meczów rocznie w danych). Zmieniono wyłącznie poniższe reguły zakresu `national`; ligi zachowują
wszystkie dotychczasowe wartości (test).

| Reguła | Ligi | Reprezentacje | Uzasadnienie i pomiar na kopii |
|---|---|---|---|
| świeżość danych przy starcie meczu | najnowszy wynik ≤ 21 dni | ≤ 150 dni | najdłuższa regularna przerwa (czerwiec → wrzesień) ok. 100 dni + jedno brakujące okno; reguła 21 dni blokowałaby każdy mecz z definicji |
| okna protokołu v2 | 1 sezon na okno | 1,5–2 lata na okno | hiperparametry 07.2019–06.2021 (547), kalibracja 07.2021–12.2022 (489), selekcja 2023–2024 (571); każde ≥ 300 meczów |
| okna protokołu v3 | kalibracja i selekcja 12 mies. (core: kalibracja 24) | 24 i 24 mies. | 12 miesięcy to ok. 300 meczów i część okien FIFA; 24 miesiące ≈ 600 |
| siatka Elo (k / przewaga gospodarza) | 15–30 / 40–80 | 20–60 / 40–100 | siatka lig wybrała swoje maksimum k = 30; w nowej siatce okno walidacji wybrało k = 50 (wnętrze: k = 60 gorsze), przewagę 40 |
| zanik Dixon–Coles (half-life) | 120 / 240 / 480 dni | 240 / 480 / 720 / 1080 dni | siatka lig wybrała swoje maksimum 480; teraz 1080 (wynik walidacji 2,1276 → 2,1237, krzywa się wypłaszcza) |
| okno dopasowania Dixon–Coles | 730 dni | 1460 dni | 730 dni to ok. 20 meczów reprezentacji (w lidze ok. 70) |
| retrening challengera | 150 nowych meczów i 7 dni | 60 nowych meczów i 28 dni | ok. 60 meczów reprezentacji na jedno okno FIFA: przegląd po każdym oknie, najwyżej jeden trening na okno |

**Bez zmian (nie zależą od częstotliwości):** progi `predictive_status` (ECE ≤ 0,03, lepszy od
częstości bazowej, 300 prognoz w oknie selekcji, sezon ≤ 0,06, brak nadmiernej pewności), progi
profili i dowody prospektywne challengerów (300 wspólnych rozliczonych meczów). Tempo dla
reprezentacji: ok. 300 meczów rocznie, czyli dowód prospektywny n/300 zbiera się ok. roku
(ok. 5 okien FIFA) — w lidze kilka tygodni.

## Co usunięto z TASK 017

* Dodatkowa bramka rejestru dla ścieżki kuponowej: `generator_allowed` dla reprezentacji zwraca
  „obowiązują bramki modeli jak w ligach”; wymóg `validated` + decyzji „1X2 reprezentacji” i
  komunikat „rynek poza generatorem” nie dotyczą już reprezentacji (puchary europejskie
  pozostają zablokowane jak dotąd).
* `national_serving_predictions` nie jest już wywoływane przy prognozach, a wersja
  `national_1x2` nie jest serwowana (`active_version_ids`, `prediction_preference`).
* Tabela „Reprezentacje — analiza poza kuponami” w Kuponie dnia: zamiast niej tabela
  „Reprezentacje — rozgrywki bez danych” z samymi rozgrywkami bez historii (z powodem). Mecze
  reprezentacji z historią są w „Najlepszych typach”, kartach kuponów, Moim kuponie i Analizie
  meczu — tak jak mecze lig.
* Odświeżanie prognoz cienia po „Analizuj” usunięto. Stare prognozy cienia w
  `stat_observations` zostają nietknięte jako historia (test). Worker `national_shadow` nadal
  może dopisywać obserwacje badawcze — nie wpływa to na kupony.
* Benchmark STS pozostaje wyłącznie diagnostyką (`market_benchmarks`, porównanie z kursami w
  ocenie prospektywnej — teraz także w grupie „reprezentacje”), jak w ligach.

## Kalibracja i miejsce meczu

Model reprezentacji to te same rodziny co w ligach: Elo z marginesem remisu i Dixon–Coles.
O modelu 1X2 decyduje ta sama reguła co w ligach (`prediction_preference`: zwalidowany wygrywa,
przy remisie statusów niższy log loss w selekcji). Na kopii wygrało Elo (zwalidowane; Dixon–Coles
1X2 ma ECE 0,036 > 0,03, log loss 0,971 wobec 0,852 Elo na tych samych 571 meczach). Dixon–Coles
serwuje rynki bramkowe — jak w ligach.

Miejsce meczu: wynik historyczny i mecz z oferty dostają przewagę gospodarza tylko wtedy, gdy
miejsce jest znane i leży w kraju gospodarza. Dla meczu z oferty STS źródłem jest zapisany
terminarz rozgrywek (jeden jednoznaczny wpis tego dnia); bez niego miejsce jest „nieznane —
bez przewagi gospodarza” (reguła z TASK 017). Mecze klubowe: bez zmian (zawsze gospodarz).
**W kopii nie ma zapisanych terminarzy reprezentacji**, więc wszystkie dzisiejsze mecze Ligi
Narodów są liczone bez przewagi gospodarza.

## Wynik bramki per rynek (kopia, okno selekcji)

| Rynek | Model | n | Log loss | ECE | Baza | Status |
|---|---|---:|---:|---:|---:|---|
| 1X2 | Elo | 571 | 0,852 | 0,0115 | 1,049 | `validated_predictive` |
| podwójna szansa | Elo | 571 | — | 0,0115 | — | `validated_predictive` |
| 1X2 / podwójna szansa | Dixon–Coles | 571 | 0,971 | 0,036 | — | `experimental` (ECE > 0,03) |
| gole (suma 1,5 / 2,5 / 3,5, obie strzelą) | Dixon–Coles | 571 | — | 0,036–0,047 | — | `experimental` (ECE > 0,03 lub nie lepsze od bazy) |

(1X2 jako trzy zdarzenia binarne: log loss 0,503 wobec 0,611 częstości bazowej.) Holdout (od
2025, nie decyduje): Elo log loss 0,790, ECE 0,0235. Elo: k = 50, przewaga gospodarza 40,
margines remisu 0,26 (dopasowany przed oknem selekcji). Dla porównania siatka lig (k = 30) dała
w selekcji log loss 0,848 i ECE 0,0137 — oba warianty przechodzą bramkę; wybór należy do okna
walidacji, nie do selekcji. Bramka puli: 1X2 i podwójna szansa przechodzą (571 prognoz, lepsze od
bazy). Rejestr: obie wersje `experimental` — tak jak każda wersja lig (brak benchmarku
rynkowego); nie blokuje to serwowania.

## Liczby z kopii prawdziwej bazy

Kopia przez `storage.snapshot_file`, offline, z poleceniem zatrzymania workera; po pomiarach
usunięta. Prawdziwej bazy nie otwierano nowym kodem.

* Historia reprezentacji: 4 315 meczów (trening 2 316, hiperparametry 547, kalibracja 489,
  selekcja 571, holdout 392). Najnowszy wynik w puli: 19.07.2026 (finał MŚ). **Brak wyników
  wrzesień–październik 2026** (Liga Narodów 2026/27, kolejki 1–4) w źródle — rating nie zna tych
  meczów.
* Mecze 12–16.11 w ofercie STS z rozgrywek z historią: 53; z prognozą 1X2 Elo: 45; bez
  prognozy 8 (nazwy bez potwierdzonego aliasu lub za mało meczów drużyny). Nowe prognozy: 881
  wierszy `predictions` (45 × 1X2 Elo, rynki Dixon–Coles). Obserwacje cienia: bez zmian.
* Analiza dnia (offline, 12.11, 16:20; reguły częstotliwości reprezentacji): 294 selekcje
  reprezentacji z prognozą; zwalidowane 1X2 i podwójna szansa 48, reszta (rynki bramkowe)
  eksperymentalne; pula kreatora 24. Propozycje: WYSOKA TRAFNOŚĆ (1 noga, kurs 1,28, szansa 80%,
  ostrożnie 76%), STANDARDOWY (2 nogi, kurs 1,85, 60% / 54%), WYŻSZA WYPŁATA (2 nogi, kurs 2,58,
  55% / 46%), PEWNY SOLO (kurs 1,22, 87% / 77%); TASIEMKA: brak kombinacji 8–12.
* 13.11: STANDARDOWY (kurs 1,81, 58% / 53%), WYŻSZA WYPŁATA (kurs 2,56, 57% / 48%), PEWNY SOLO
  (kurs 1,16, 87% / 83%); WYSOKA TRAFNOŚĆ: ostrożna szansa kuponu < 65%. 14.11: brak kuponów —
  kursy z oferty sprzed ponad 180 minut (świeżość kursu, jak w ligach; pobrania w dniu meczu
  obejmują teraz także mecze reprezentacji).
* Z siatką lig (pierwszy pomiar, k = 30, half-life 480) 12.11 dawał tylko STANDARDOWY i WYŻSZĄ
  WYPŁATĘ.
* **Regresja lig** (dzień ligowy 07.11 i 08.11, stary kod `c6660a7` na osobnej kopii, nowy kod
  na kopii po wytrenowaniu reprezentacji): kandydaci z prognozą (1 537 i 1 337) i propozycje
  identyczne.

### Mecze 12–15.11 — przed i po kalibracji, obok kursów STS (bez marży)

| Mecz | Cień TASK 017 (N_goals) | Elo, siatka lig (k = 30) | Elo, siatka reprezentacji (k = 50) — serwowane | STS (bez marży) |
|---|---|---|---|---|
| Czechy – Hiszpania | 2 / 5 / 94 % | 7 / 12 / 81 % | 4 / 9 / **87 %** | 8 / 13 / 79 % |
| San Marino – Białoruś | 1 / 4 / 95 % | 5 / 9 / 87 % | 3 / 6 / **92 %** | 5 / 11 / 84 % |

Kalibrowane Elo jest mniej przesadne niż cień TASK 017, ale przy k = 50 nadal pewniejsze od rynku
przy bardzo dużej różnicy sił (o 5–8 punktów); w całym oknie selekcji ECE wynosi 0,0115. W meczach, w których gospodarz jest faworytem rynku,
model daje gospodarzowi wyraźnie mniej niż kurs (np. 48–52% wobec 73–80%), bo bez terminarza nie
liczy przewagi gospodarza, a rating nie zna wrześniowych i październikowych meczów. To
ograniczenie danych, nie zmiana reguł.

## Testy

* `tests/test_task018.py`: 16 testów (zakres i rejestracja tym samym protokołem, prognozy w
  `predictions` z `prediction_id` i idempotencja, te same progi bramki — ECE powyżej progu →
  `experimental`, poniżej i lepsze od bazy → `validated_predictive`, kreator identyczny dla typu
  reprezentacji i ligi — eksperymentalny tylko w WYŻSZEJ WYPŁACIE, zwalidowany we wszystkich
  profilach, brak bramki TASK 017, kalibracja zmniejsza ECE i zachowuje kolejność, gospodarz
  tylko z jednoznacznego terminarza, rozgrywki bez historii bez prognoz, liczbowa niezmienność
  Elo i Dixon–Coles lig, kandydaci/propozycje/tożsamości kuponów ligi bez zmian po dodaniu
  zakresu `national`, stare prognozy cienia bez zmian, „Najlepsze typy” grupują typ
  reprezentacji jak typ ligi, reguły częstotliwości z konfiguracji zakresu `national` przy
  niezmienionych wartościach lig).
* `tests/test_task017.py`: pięć asercji opisujących usuniętą bramkę i tabelę „poza kuponami”
  dostosowano do decyzji użytkownika (żaden test nie został wyłączony).
* Pełny `pytest`: 515 zaliczonych, 4 pominięte (brak lokalnych autentycznych PDF-ów STS i
  jeden test AppTest zależny od środowiska), 0 błędów.
* `ruff check .` i `ruff format --check .`: bez uwag.
* Ścieżki QA (`python -m sports_edge.qa`): 40 / 0 błędów.
* Playwright: `sports_edge.qa_browser` 22 zrzuty / 0 błędów, `sports_edge.qa_visual` 108
  kontroli w 5 rozdzielczościach / 0 błędów (dane syntetyczne).

## Ograniczenia

* Brak wyników reprezentacji z września i października 2026 w bezpłatnym źródle (openfootball);
  limit świeżości 150 dni jest ustalony dla kalendarza reprezentacji (bez nowszych wyników pula
  przestanie dostawać prognozy po 16.12.2026).
* Brak zapisanych terminarzy reprezentacji: miejsce meczu „nieznane”, bez przewagi gospodarza.
* Typy bramkowe reprezentacji są eksperymentalne (tylko symulacja w WYŻSZEJ WYPŁACIE).
* Benchmark STS reprezentacji: „za mało” lub „nieznane” do czasu rozliczenia meczów z kursem
  sprzed startu (diagnostyka, nic nie blokuje).
* Model nie zna składów, kontuzji ani motywacji. Bez rekomendacji zysku.

## Proponowany TASK 019

1. Terminarze reprezentacji (Liga Narodów 2026/27, eliminacje MŚ 2030) z bezpłatnego źródła z
   miejscem meczu — przewaga gospodarza z jednoznacznego terminarza.
2. Uzupełnienie wyników wrzesień–październik 2026 (gdy źródło je opublikuje) i alert, gdy pula
   reprezentacji jest starsza niż jedno okno.
3. Ocena prospektywna reprezentacji po 18.11 (rozliczone prognozy, porównanie z kursami STS).

## Instrukcja dla użytkownika

1. Po aktualizacji zrestartuj aplikację i workera.
2. W Kuponie dnia kliknij „Analizuj mecze i generuj kupony” (pierwsze kliknięcie trenuje
   zakres „reprezentacje”, ok. 1–2 minut; potem prognozy i kupony powstają tak jak dla lig).
