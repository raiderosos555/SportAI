# Raport TASK 007 — Sports Edge AI

Data: 02.11.2026. Podstawa: commit `fe60b60` (TASK 006). Praca wyłącznie lokalna: bez
commitów, pushu, zakupów API, płatnych wywołań LLM i nieograniczonych pobrań.

Przed migracją bazy powstały dwie kopie:
- `backups/2026-11-01_23-05-23` (ręczna);
- `backups/pre-migration-v6_2026-11-01_22-36-53.db` (automatyczna).

Żaden istniejący rekord nie został zmieniony ani usunięty. Baza jest tylko do dopisywania.

Wszystkie liczby poniżej pochodzą z bazy użytkownika albo z testów na danych syntetycznych
(co jest zaznaczone przy każdej z nich). Nie podaję skuteczności, której nie zmierzono.

---

## A. Audyt i poprawki P0

### A1. Spójność przedziałów (§4.1) — błąd potwierdzony i naprawiony

Audyt bazy wykazał trzy problemy.

- **18 zapisanych prognoz Dixona–Colesa dla 1X2 miało końcową szansę poza własnym
  przedziałem**, np. 75,8% przy górnej granicy 75,2%. Przyczyna: przedział Wilsona liczono
  przed normalizacją trzech skalibrowanych wartości, a normalizacja przesuwała samą szansę.
- **Podwójna szansa z Dixona–Colesa była niespójna z 1X2 tego samego modelu.** Każdą stronę
  kalibrowano osobno, więc P(1X) różniło się od 1 − P(2) nawet o 5,6 pkt proc., a suma trzech
  stron odchodziła od 2 nawet o 0,08.
- Rynki binarne i podwójna szansa z Elo były spójne.

Poprawki:

1. Nowe prognozy:
   - kolejność: kalibracja, normalizacja, a dopiero potem przedział wokół **końcowej**
     wartości (`probability_lab.calibrated_market`);
   - rynki binarne: kalibrowana jest jedna strona, druga to jej dopełnienie, a przedział
     drugiej strony to dopełnienie przedziału pierwszej;
   - podwójna szansa liczona z końcowego 1X2: P(1X) = 1 − P(2), przedział jako dopełnienie
     przedziału wykluczonego wyniku (`derived_double_chance`).
2. Odczyt każdej prognozy (`edge_engine.market_problem`, `consistent_interval`) sprawdza:
   - 0 ≤ P_low ≤ P ≤ P_high ≤ 1;
   - sumę rynków rozłącznych ≈ 1, a podwójnej szansy ≈ 2;
   - zgodność podwójnej szansy z 1X2.

   Niespójna selekcja dostaje flagę `probability_ok = False` i jawny powód. Nie trafia do
   rankingu (A/B/C), najlepszych typów ani kreatora kuponów.
3. Zapisane prognozy historyczne nie są zmieniane. Przy następnym generowaniu powstaje nowa,
   spójna prognoza: reguła „ta sama prognoza” uwzględnia teraz metodę przedziału.

### A2. Holdout nie wybiera modeli ani statusów (§4.2)

W TASK 005–006 holdout od 2024-07-01 decydował o czterech rzeczach:
- statusach predykcyjnych;
- bramkach lig;
- wyborze modelu dla 1X2;
- statusie wersji (EV).

Elo brało z niego dodatkowo szerokość przedziałów.

**Protokół v2** (`Split` z czterema oknami, `services.PROTOCOLS`):

| Okno | 5 czołowych lig | 7 lig dodanych | Decyduje o |
|---|---|---|---|
| trening | < 2021-07-01 | < 2022-07-01 | dopasowanie walk-forward |
| walidacja | 2021/22 | 2022-07 – 2022-12 | hiperparametry (k, przewaga gospodarzy, okres półtrwania) |
| kalibracja | 2022/23 | 2023-01 – 2023-06 | mapy izotoniczne, margines remisu, szerokość przedziałów Elo |
| selekcja | 2023/24 | 2023/24 | statusy, bramki lig, wybór modelu 1X2, status wersji |
| holdout końcowy | od 2024-07-01 | od 2024-07-01 | **nic** — tylko raport |

Pozostałe zasady protokołu v2:

- Ocena w oknach selekcji i holdoutu używa **dokładnie tej transformacji, którą aplikacja
  serwuje** (`served_view`). Zwalidowane liczby to więc te same liczby, które widzisz.
- Test regresji (`test_final_holdout_results_change_no_decision`) zamienia wszystkie wyniki
  holdoutu. Hiperparametry, kalibracja, statusy, bramki lig i wybór modelu pozostają
  identyczne; zmieniają się tylko raportowane metryki holdoutu.
- Stary holdout jest **oznaczony jako użyty**: zdarzenie `holdout_used` w nowej tabeli
  `model_events` dla 9 starych wersji.
- Ich zapisane statusy się nie zmieniają, ale przy odczycie status jest **jawnie zawieszony**:
  „eksperymentalny” z widocznym powodem i z zachowaniem zapisanego statusu w
  `stored_status`. Nic nie dzieje się po cichu.

Wynik protokołu v2 na danych użytkownika (okno selekcji 2023/24):

| Zakres | Elo 1X2 (log loss, ECE, n) | DC 1X2 | Zwalidowane rynki (DC) | Elo |
|---|---|---|---|---|
| 5 czołowych lig | 0,98091 · 0,018 · 1752 | 0,98175 · 0,016 | 1X2, podwójna szansa, O/U 1,5, O/U 2,5 | 1X2, podwójna szansa |
| 7 lig dodanych | 1,00056 · 0,017 · 2474 | 1,02015 · 0,026 | O/U 2,5 | 1X2 |

- W obu zakresach 1X2 serwuje Elo, bo ma niższy log loss w oknie selekcji.
- Holdout końcowy, raportowany tylko informacyjnie, wypada gorzej niż okno selekcji: log loss
  DC 1X2 to 1,019 (5 lig) i 1,049 (7 lig), a Elo w 5 ligach 0,987. To uczciwy sygnał, że
  sezony od 2024/25 są trudniejsze albo dane się zmieniają.

**Nowe wersje (4 challengery) nie zostały aktywowane automatycznie.** Czekają w trybie cienia
(prognozy archiwizowane, nieserwowane) na Twoją decyzję w panelu „Uczenie AI”. Do tej decyzji
aplikacja serwuje dotychczasowe wersje z zawieszonymi statusami. W praktyce kupony są
oznaczone jako symulacja, bo żaden status v1 nie jest już traktowany jako zwalidowany.

### A3. Zależność par meczów (§4.3)

`pair_dependence` liczy 95% przedział stosunku „łączna trafność / iloczyn osobnych trafności”
**bootstrapem blokowym po dniach**. Pary jednego dnia nie są niezależnymi obserwacjami.

- Wniosek „brak dowodu zależności” jest formułowany wprost jako **nie dowód niezależności**.
- Opis łącznej szansy kuponu AKO zaczyna się od „PRZYBLIŻENIE”.
- Kupony AKO mają ostrzeżenie, że łączna szansa jest przybliżeniem.

### A4. Inne poprawki znalezione przy okazji

- Etykieta typu dla handicapu (linia „0:1”) wyłączała stronę z błędem; teraz jest poprawna.
- Wartość modelu bez kalibracji (np. powyżej 0,5 gola) nigdy nie liczy się do szansy kuponu.
- Przez pomyłkę nadpisałem w trakcie pracy istniejący moduł `quality.py` (kontrole TASK 001).
  Wykryłem to od razu i przywróciłem go z gita bez zmian. Nowy kod jest w
  `forecast_quality.py`.

## B. Mój kupon i AI Coupon Advisor

- **Strona „Mój kupon”**:
  - dzień, sport, liga, wyszukiwanie drużyn;
  - wydarzenie ze statusem, świeżością kursu i dostępnością prognozy;
  - każdy rynek z przyciskiem dodania;
  - kupon: bieżące kursy (ze zmianą od dodania), stawka, kurs łączny, wypłata po podatku STS,
    zysk netto, próg opłacalności i szansa modelu, gdy da się ją policzyć.
- Koszyk jest wspólny dla stron i zachowuje się po odświeżeniu (szkic w
  `data/drafts/`, poza repozytorium).
- **„DODAJ DO MOJEGO KUPONU”** jest dostępne w:
  - Dashboard (karty najlepszych typów);
  - Analiza meczów (najlepszy typ + zaznaczanie w tabeli rynków);
  - Kupon dnia (ranking A/B/C + przeniesienie całej propozycji profilu);
  - Wydarzenia (terminarz → rynki);
  - generator.
- **Walidacja** (`my_coupon.py`):
  - brak duplikatów i dwóch selekcji jednego rynku;
  - typy sprzeczne odrzucane (sprawdzenie wszystkich wyników meczu, np. „1” + „X2”);
  - brak mieszania okresów;
  - kurs niepotwierdzony, rozpoczęte wydarzenie lub wycofany rynek blokują zapis;
  - dwa typy jednego meczu blokują zapis i obliczanie szansy, bo nie ma modelu łącznego.
- **OCEŃ MÓJ KUPON AI** (`advisor.py`):
  - dla każdego typu: szansa modelu i ostrożna, status kalibracji, wiarygodność, jakość danych,
    świeżość, ryzyka i krótki powód;
  - bez modelu komunikat: „Brak zwalidowanego modelu dla tego rynku — nie można wiarygodnie
    oszacować szansy”;
  - dla kuponu: kurs, szansa (przybliżona), wypłata, zysk netto, próg opłacalności, EV jako
    diagnostyka i największe niepewności;
  - najsłabsze ogniwo z wyjaśnieniem.
- **ZAPROPONUJ ALTERNATYWĘ**: bez najsłabszego ogniwa, mniej selekcji, lepiej zamodelowany
  rynek tego samego meczu, SOLO. Każdą zmianę stosujesz sam przyciskiem.
- **Pytania** (`explainer.py`, szablony na danych):
  - wszystkie 5 pytań z zadania działa, m.in. „co jeśli usunę trzeci mecz” (przelicza kupon)
    i „dlaczego nie standardowy” (lista niespełnionych reguł);
  - zapis kuponu jako wirtualny albo jako rzeczywisty wpis ręczny (oddzielony);
  - kupon trafia do Historii.
- **Opcjonalny lokalny model AI (`llm.py`, Ollama) — bezpłatny**:
  - pierwsza wersja korzystała z płatnego API Anthropic, rozliczanego osobno od subskrypcji
    Claude. Na Twoją prośbę zastąpiłem ją modelem działającym na tym komputerze. Płatnego
    wywołania nie było ani wtedy, ani teraz;
  - domyślnie wyłączony; włączasz go w Ustawieniach, a każde pytanie wysyłasz kliknięciem;
  - model domyślny: **Bielik 11B v2.3 Instruct** (SpeakLeash, polski, Apache 2.0 + warunki
    bielik.ai/terms), pobierany raz poleceniem
    `ollama pull hf.co/speakleash/Bielik-11B-v2.3-Instruct-GGUF:Q4_K_M` (≈ 6,7 GB);
    alternatywy: Bielik Q8_0 (≈ 12 GB, wyższa jakość), `gemma3:12b`, `qwen2.5:14b`;
  - aplikacja łączy się wyłącznie z Ollamą na 127.0.0.1, a inny host jest odrzucany. Zestaw
    faktów jednego kuponu nie opuszcza komputera; działa też w trybie offline;
  - odpowiedź w ustalonym schemacie JSON. Każda liczba jest sprawdzana z faktami, a przy
    problemie (Ollama nie działa, model niepobrany, zły format, liczby spoza danych) obowiązuje
    odpowiedź szablonowa;
  - zużycie (tokeny, czas) jest zapisywane w `llm_usage`, koszt zawsze 0;
  - testowany na atrapie serwera Ollama, bo Ollama nie jest jeszcze zainstalowana.

## C. Cztery profile i generator „kupon na kurs”

| Profil | Kurs łączny | Maks. sel. | Min. ostrożna szansa selekcji | Min. ostrożna szansa kuponu |
|---|---|---|---|---|
| WYSOKA TRAFNOŚĆ | 1,25–1,60 | 2 (SOLO preferowany, tolerancja 2 pkt) | 75% | 65% |
| STANDARDOWY | 1,80–2,20 | 3 | 65% | 42% |
| WYŻSZA WYPŁATA | 2,50–4,00 | 4 | 55% | 22% |
| TASIEMKA | 8,00–12,00 | 8 | 60% | 7% |

- Progi nie są niższe niż w profilach, które zastępują. Polityka `2026-11-02.p3` jest
  zapisywana z każdą propozycją.
- **Tasiemka** używa programowania dynamicznego po logarytmach kursów (jeden typ na mecz).
  Test potwierdza zgodność z pełnym przeszukiwaniem.
- Gdy dane nie uzasadniają kuponu, nie ma kuponu. Kod odmowy „coupon_probability” oznacza,
  że kombinacje w przedziale istnieją, ale są zbyt mało prawdopodobne.
- **Generator „ZBUDUJ KUPON NA KURS…”**:
  - parametry: kurs docelowy i tolerancja, maksymalna liczba selekcji, dni, dyscypliny,
    minimalna wiarygodność, maksymalna niepewność, wykluczenia lig i rynków, wymóg walidacji;
  - maksymalizuje ostrożną szansę kuponu i wyświetla, że łączna szansa jest przybliżeniem;
  - presety odtwarzają dokładnie przedziały profili;
  - „Tasiemka” ma osobny wpis w menu.

## D. Symulator budżetu i symulator historyczny

- **Monte Carlo** (`simulation.py`, numpy):
  - stawka stała albo procent **bieżącego** budżetu, bez podnoszenia po przegranej;
  - limit stawki kuponu, limit straty, korelacja kuponów ze wspólnymi selekcjami;
  - niepewność parametru: szansa losowana w każdym przebiegu z rozkładu Beta wokół wartości
    modelu, z rozrzutem z ostrożnej granicy;
  - koszty STS: współczynnik 0,88 i 10% podatku od wygranej powyżej 2280 zł;
  - wyniki: percentyle 5/50/95, mediana, P(strata), P(osiągnięcia limitu), serie porażek,
    rozkład obsunięć, histogram i wachlarz percentyli w czasie (z widokiem tabeli);
  - ziarno losowania gwarantuje powtarzalność;
  - źródło szansy: Mój kupon, ostatnia propozycja profilu albo ręczne założenie. Szansa
    niezwalidowana daje wynik „warunkowa (eksperymentalna)”.
  - Przykład z testu dymnego (ręcznie: 50%, kurs 2,00, 50 × 10 zł): mediana 440 zł z 500 zł,
    co jest zgodne z podatkiem (−1,20 zł na kupon).
- **Symulator historyczny**:
  - oferta, kursy, dokumenty i wyniki odczytywane **as-of** wybranej godziny decyzji
    (`offer_table(as_of=…)`);
  - prognozy tylko zapisane przed tą chwilą;
  - kupony z bieżącej polityki rozliczane zapisanymi wynikami;
  - wyniki zapisywane jako `simulation_runs`, oddzielnie od kuponów wirtualnych i
    rzeczywistych. Bez danych z przyszłości i bez fikcyjnych kursów.

## E. Zbieranie danych

Schemat v6 dodaje tylko nowe tabele, a migracja odbyła się z kopią:
- `model_events`;
- `prediction_settlements`;
- `job_runs`;
- `simulation_runs`;
- `llm_usage`.

- **Rozliczanie każdej prognozy prospektywnej**, nie tylko nóg kuponów:
  - osobne statusy: rozliczona, nierozstrzygnięta (> 48 h bez wyniku), odwołana, przełożona,
    unieważniona, niepewna (wynik niezweryfikowany), konflikt źródeł, brak statystyki;
  - korekta wyniku tworzy nową rewizję wskazującą na poprzednią; niczego nie nadpisuje.
- Stan bazy użytkownika:
  - 4561 prognoz, z czego 644 dotyczy rozpoczętych meczów;
  - **384 rozliczone**, 260 czeka na wynik (mecze sprzed chwili);
  - żadnego meczu bez wyniku dłużej niż 48 h.
- Każde zadanie (aktualizacja wyników, rozliczenia, przegląd modeli) zostawia wpis w
  `job_runs`: status, powód, liczby.
- **„Uzupełnij po przerwie”** rozlicza zaległe prognozy, a online aktualizuje wyniki
  maksymalnie dla 14 dni przerwy. Nie robi nieograniczonych pobrań.
- Raport kompletności i monitoring: Źródła i ustawienia → „Zbieranie danych i monitoring”.

## F. Uczenie modeli

- **Panel „Uczenie AI”** (menu oraz widok w „Modele i statystyki”):
  - status każdego zakresu (8 statusów z zadania);
  - champion i challenger z wnioskiem prostym językiem;
  - **AKTYWUJ NOWY MODEL / POZOSTAW POPRZEDNI MODEL** z potwierdzeniem;
  - przywracanie starszej wersji;
  - przegląd tygodniowy z warunkami minimalnymi i historia uczenia.
- **Przegląd** (`retraining.py`):
  - challengera trenuje tylko wtedy, gdy są co najmniej 150 nowe wyniki od treningu
    championa, żaden challenger nie czeka i minęło 7 dni od ostatniego treningu;
  - w przeciwnym razie zapisuje powód pominięcia;
  - nigdy nie aktywuje sam: automatycznej promocji nie ma;
  - CLI: `sports-edge learning review`. Instrukcja dla Harmonogramu zadań Windows jest w
    panelu, ale aplikacja nie tworzy zadań systemowych sama.
- **Tryb cienia**: challengery prognozują te same mecze, a prognozy są archiwizowane. Porównanie
  prospektywne jest parowane (te same mecze), z przedziałem bootstrap. Werdykt pada od 100
  wspólnych rozliczonych prognoz.
- **Eksperyment ablacyjny** (log loss 1X2, okno selekcji, te same mecze):

  | Zakres | A Elo | B Dixon–Coles | C DC + cechy | D prosty ML |
  |---|---|---|---|---|
  | 5 czołowych lig (n=1745) | 0,98166 | 0,98175 | 0,98465 | **0,97739** |
  | 7 lig dodanych (n=2455) | **1,00021** | 1,02015 | 1,01919 | 1,00172 |

  - Cechy: forma i bilans bramek 5 ostatnich meczów oraz dni przerwy, wyłącznie sprzed meczu.
  - Cechy **nie poprawiają** Dixona–Colesa.
  - Prosty model ML jest minimalnie lepszy tylko w 5 ligach w oknie selekcji.
  - Na raportowanym holdoucie najlepsze jest Elo (0,98697 i 1,01854).
  - Wniosek: zostaje Elo dla 1X2. D warto sprawdzić prospektywnie, zanim zostanie modelem.

## G. Jakość prognoz (prospektywnie)

384 rozliczone prognozy (867 selekcji, wszystkie rynki):

| Metryka | Wartość |
|---|---|
| Log loss | 0,444 (95%: 0,395–0,488) |
| Brier | 0,145 |
| ECE | 0,041 |
| Trafność faworyta | 79,7% (95%: 75,4–83,4%) |

- Trafność faworyta obejmuje rynki binarne, więc nie jest to trafność 1X2.
- Analiza błędów: remisy prognozowane na 25%, a zdarzały się w 10% (n=42, próba wciąż mała).
  Przewaga gospodarzy jest zgodna z prognozą (43% wobec 45%).
- To dane z dwóch dni oferty. **To jeszcze nie jest ocena skuteczności.** Wiarygodne wnioski
  będą możliwe po setkach rozliczonych prognoz na rynek.
- „Analiza wyniku kuponu” w Historii mówi, co weszło, a co nie, i z jaką szansą. Nie zgaduje
  przyczyn.

## H. Testy

- Testy TASK 007 (`tests/test_task007.py`):
  - przedziały i sumy (także test własności na losowych rynkach);
  - holdout bez wpływu na decyzje;
  - champion i challenger, bootstrap blokowy;
  - koszyk i walidacja, doradca i objaśniacz, lokalny model na atrapie serwera Ollama;
  - 4 profile, programowanie dynamiczne kontra pełne przeszukiwanie, generator;
  - symulatory i as-of;
  - rozliczenia z rewizjami i statusami, przegląd, ablacja bez wycieku, jakość, kopia z
    testem odtworzenia.
- `tests/test_ui_paths.py`: 20 ścieżek interfejsu z §28 na danych syntetycznych, wszystkie
  zaliczone:
  - start, dashboard, kupon dnia, mój kupon, dodanie, ocena AI, zmiana stawki;
  - kurs docelowy, tasiemka, symulator budżetu, historia, Uczenie AI;
  - brak danych, brak internetu, nieaktualne kursy, brak prognozy, awaria źródła wyników.
- Trzy testy z TASK 005–006 zaktualizowałem świadomie, bo TASK 007 zmienił zasady:
  - statusy v1 są zawieszane;
  - polityka ma cztery profile;
  - reguła dotycząca statusu v1 przy wyborze modelu.
- CI ma raport JUnit i artefakt „qa-report” z jednej kombinacji macierzy. Artefakt zawiera
  raport ścieżek UI, wersje bibliotek i syntetyczny PDF. Kod sprawdza, że nie ma w nim bazy,
  `.env` ani kluczy.
- Zrzuty ekranu nie są wykonywane, bo wymagają przeglądarki.
- Wynik końcowy: patrz README (liczba testów).

## I. Następne kroki (propozycja TASK 008)

1. **Twoja decyzja w „Uczenie AI”: aktywacja modeli protokołu v2.** Bez niej kupony pozostają
   oznaczone jako symulacja.
2. Ocena prospektywna challengerów, gdy będzie co najmniej 100 wspólnych rozliczonych
   prognoz. Kandydaci: model D, kalibracja remisów.
3. Coroczne przesuwanie okien protokołu (nowe okno selekcji 2024/25 dopiero z nowym czystym
   holdoutem) i plan prospektywny jako główna miara.
4. Zrzuty ekranu w artefakcie QA (np. Playwright w CI) i automatyczny raport kompletności po
   przerwie przy starcie aplikacji.
5. Model łączny dla typów z jednego meczu (rozkład wyników Dixona–Colesa). Obecnie takie
   kupony są blokowane.
6. Archiwizacja kursów starszych niż 12 miesięcy do osobnej bazy, na Twoje polecenie.
