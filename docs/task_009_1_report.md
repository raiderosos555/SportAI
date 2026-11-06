# HOTFIX 009.1 — Naprawa Playwright i odbiór generatora kuponów

Commit bazowy: `2b39bed95d41245a9149740fedbab0dc2af8083c` (poprawka generatora w
`coupon_builder.py`). Poprawka generatora została zachowana bez zmian. Plik polityki
`config/probability_policy.toml` nie zmienił się od tego commita: `git diff 2b39bed --
config/` jest pusty.

## 1. Playwright — przyczyna i naprawa

### 1.1 Odtworzenie

Błąd z GitHub Actions odtworzono lokalnie (Windows, Playwright 1.64.1, Chromium) na
niezmienionym kodzie, z tym samym komunikatem:

```text
Locator.click: Timeout 30000ms exceeded.
  - waiting for locator("button").filter(has_text="ZAPISZ USTAWIENIA").first
    - element is not visible
```

### 1.2 Przyczyny

1. **Przycisk w zamkniętej zakładce.** Po przebudowie interfejsu w TASK 009 przycisk
   „ZAPISZ USTAWIENIA” znajduje się w zakładce „Procesy” strony Automatyzacja AI.
   * Streamlit umieszcza w HTML treść wszystkich zakładek, ale treść zamkniętych jest ukryta.
   * `_button()` brał pierwszy pasujący element HTML (`.first`) — tutaj jedyny, ukryty.
   * Playwright czekał 30 s, aż element stanie się widoczny.
2. **Ukryty błąd w sprawdzaniu statusu, wykryty po naprawie pkt 1.** Po kliknięciu przycisku
   workera scenariusz szukał tekstu „Automatyzacja: aktywna”. Od TASK 009 etykieta
   („Automatyzacja”) i wartość („aktywna”) to osobne elementy paska statusu, więc ten tekst
   nigdy nie występuje. Krok kończyłby się porażką po 120 s nawet przy działającym workerze.
3. **Ukryty wyścig, wykryty po naprawie pkt 2.**
   * Po kliknięciu „URUCHOM AUTOMATYZACJĘ” scenariusz od razu przeładowywał stronę.
   * Przeładowanie zamyka sesję Streamlit i przerywa przebieg skryptu, zanim dotrze on do
     obsługi przycisku. Polecenie START nie zostawało zapisane i po 120 s status był
     „zatrzymana”.
   * Bezpośredni test bez przeładowania potwierdził, że sam worker startuje i zatrzymuje się
     poprawnie. W historii zadań widać przebiegi wszystkich zadań harmonogramu, także nowych
     zadań międzynarodowych.

Punkty 2 i 3 ujawniły się dopiero wtedy, gdy scenariusz przeszedł przez zapis ustawień.
Wcześniej w CI nigdy do nich nie docierał.

### 1.3 Naprawa (`src/sports_edge/qa_browser.py`)

* **`_button(page, label)`:**
  * wyszukuje przycisk po dokładnej etykiecie (tekst przycisku, nie fragment);
  * czeka do 30 s, aż dokładnie jeden pasujący element będzie widoczny i aktywny;
  * w przeciwnym razie zgłasza `ButtonNotFound` z diagnozą zamiast klikać zgadnięty element:
    „żaden nie jest widoczny”, „widocznych jest N (niejednoznaczne)” albo „widoczny jest
    nieaktywny”.
* **Brak obejść:** nie użyto `force=True` i nie zwiększono samego timeoutu.
* **Diagnostyka każdego wyszukania** (`LOOKUPS`) trafia do `screenshots.json` (pole `buttons`)
  i do komunikatu błędu w logu CI. Zawiera liczbę dopasowań i liczbę widocznych, a dla każdego
  elementu:
  * widoczność;
  * dostępność (`is_enabled`, atrybut `disabled`, `aria-disabled`);
  * czy leży w ukrytej zakładce;
  * tekst.

  Raport jest zapisywany także wtedy, gdy przebieg się nie powiedzie (pole `error`).
* **`_open_tab(page, nazwa)`:** przed zapisem ustawień otwiera zakładkę „Procesy”, przed
  sterowaniem workerem „Przegląd”. Czeka, aż zakładka będzie wybrana (`aria-selected`).
* **`_after_click(page)`:** po kliknięciu czeka na zakończenie przebiegu Streamlit, zanim
  cokolwiek przeładuje stronę.
* **`_status(page)`:** odczytuje wartość kafelka „Automatyzacja” paska statusu. `_until`
  porównuje tę wartość i zwraca ostatnio widziany status, który jest diagnozą porażki.

### 1.4 Wynik — pełny cykl workera (Windows, lokalnie)

| Krok | Oczekiwany status | Odczytany status |
|---|---|---|
| zapis ustawień (zakładka Procesy) | „Ustawienia zapisane” | widoczne |
| URUCHOM AUTOMATYZACJĘ | aktywna | aktywna |
| WSTRZYMAJ AUTOMATYZACJĘ | wstrzymana | wstrzymana |
| WZNÓW AUTOMATYZACJĘ | aktywna | aktywna |
| ZATRZYMAJ AUTOMATYZACJĘ | zatrzymana | zatrzymana |

Diagnostyka wyszukań z tego przebiegu (`artifacts/qa/screenshots/screenshots.json`):

| Wyszukanie | Dopasowania | Widoczne | Aktywny | W ukrytej zakładce | Wynik |
|---|---:|---:|---|---|---|
| ANALIZUJ MECZE I GENERUJ KUPONY | 1 | 1 | tak | nie | ok |
| zakładka Procesy | 1 | 1 | — | — | ok |
| ZAPISZ USTAWIENIA | 1 | 1 | tak | nie | ok |
| zakładka Procesy | 1 | 1 | — | — | ok |
| zakładka Przegląd | 1 | 1 | — | — | ok |
| URUCHOM AUTOMATYZACJĘ | 1 | 1 | tak | nie | ok |
| WSTRZYMAJ AUTOMATYZACJĘ | 1 | 1 | tak | nie | ok |
| WZNÓW AUTOMATYZACJĘ | 1 | 1 | tak | nie | ok |
| ZATRZYMAJ AUTOMATYZACJĘ | 1 | 1 | tak | nie | ok |

Wszystkie 22 zrzuty bez błędu. Wcześniejsze przebiegi tego dnia pokazały diagnostykę w
działaniu. Gdy nie było jeszcze poprawki z pkt 3, wyszukanie „WSTRZYMAJ AUTOMATYZACJĘ” dało:
„znaleziono 1, widoczny jest nieaktywny”, czyli trafną informację, że worker nie działał.

## 2. Odbiór generatora kuponów

Testy w `tests/test_task009_1.py` (dane syntetyczne) korzystają z **prawdziwego** pliku polityki
`config/probability_policy.toml`.

| Przypadek | Wynik |
|---|---|
| 71 selekcji zwalidowanych i 30 silniejszych eksperymentalnych | wspólna pula raportowa (25) zawiera wyłącznie eksperymentalne; WYSOKA TRAFNOŚĆ i STANDARDOWY budują kupony tylko ze zwalidowanych |
| STANDARDOWY nie traci zwalidowanych kandydatów | nogi identyczne jak przy samych zwalidowanych na wejściu (eksperymentalne niczego nie wypierają) |
| WYSOKA TRAFNOŚĆ filtruje własną pulę | do wyszukiwania trafiają tylko zwalidowane z ostrożną szansą ≥ 75%, najwyżej 25 |
| TASIEMKA wykorzystuje 40 kandydatów | przeszukuje 40 (`long_candidates`), STANDARDOWY 25 |
| brak selekcji spełniających wymagania | brak kuponu z kodem `low_probability` i progiem w komunikacie („≥ 75%”, „≥ 65%”); obiekt polityki niezmieniony; żaden zbudowany kupon nie ma nogi poniżej progu ani kursu poniżej minimum |
| brak duplikatów wydarzeń w kuponie | przy 3 rynkach na mecz w puli każdy kupon ma najwyżej jedną nogę z meczu |
| alternatywy nie powielają wydarzeń | kupon główny i alternatywy profilu mają rozłączne zbiory meczów |
| raport przyczyn braku kuponu | pusty wybór → `no_candidates`; same eksperymentalne → `not_validated`; poza przedziałem kursu → `payout` (komunikat z przedziałem 1.25–1.6); szansa kuponu < 65% → `coupon_probability` (komunikat „niższa niż 65%”) |

**Kontrola, czy testy wykrywają błąd.** Trzy testy generatora (71+30, własne pule, TASIEMKA 40)
uruchomiono na wersji `coupon_builder.py` sprzed poprawki (`2b39bed^`): wszystkie trzy **nie
przechodzą**. Na wersji obecnej przechodzą. Plik przywrócono, różnica w gicie jest pusta.

## 3. Polityka

Bez zmian, `config/probability_policy.toml` jest identyczny z wersją z `2b39bed`.

| Profil | Ostrożna szansa nogi | Zwalidowane | Kurs | Zysk netto |
|---|---|---|---|---|
| WYSOKA TRAFNOŚĆ | ≥ 75% | tak | 1,25–1,60 | ≥ 8% |
| STANDARDOWY | ≥ 65% | tak | 1,80–2,20 | ≥ 35% |
| WYŻSZA WYPŁATA | ≥ 55% | nie (symulacja) | 2,50–4,00 | ≥ 100% |
| TASIEMKA | ≥ 60% | tak | 8–12 | ≥ 500% |

## 4. Odbiór

| Kontrola | Windows (lokalnie, Python 3.12) | Ubuntu (GitHub Actions) |
|---|---|---|
| Ruff check i format | bez uwag (163 pliki) | po pushu |
| pytest | 338 zaliczonych | po pushu |
| Ścieżki interfejsu (`sports_edge.qa`) | 25/25 | po pushu |
| Playwright (`sports_edge.qa_browser`) | 22/22, pełny cykl workera | po pushu |
| Testy wizualne (`sports_edge.qa_visual`) | 78/78 | po pushu |

**Artefakty QA** są w `artifacts/qa/` (lokalnie; katalog jest w `.gitignore`):
* `junit.xml`;
* `ui_report.json`;
* `versions.txt`;
* `screenshots/` z `screenshots.json` (diagnostyka przycisków);
* `visual/` z `visual_report.json`;
* `fixtures/`.

W CI te same pliki trafiają do artefaktów `qa-report-Windows` i `qa-report-Linux`. Nie zawierają
bazy, kluczy ani prywatnych plików (sprawdza to samo QA).

**Ubuntu i CI.** Konfiguracji Ubuntu nie da się uruchomić na tym komputerze: brak WSL (jest
tylko instalator) i brak Dockera. Wymóg „wszystkie konfiguracje CI zakończone sukcesem” —
macierz Windows/Ubuntu × Python 3.12/3.13, Playwright i testy wizualne na 3.12 — zostanie
potwierdzony dopiero przez GitHub Actions po commicie i pushu tej poprawki. Na chwilę pisania
raportu CI dla tych zmian **nie zostało uruchomione**.

## 5. Zmienione pliki

* `src/sports_edge/qa_browser.py` — wybór widocznego przycisku, diagnostyka, otwieranie
  zakładek, oczekiwanie po kliknięciu, odczyt statusu.
* `tests/test_task009_1.py` — 12 testów (wyszukiwanie przycisku: 6, odbiór generatora: 6).
* `docs/task_009_1_report.md` — ten raport.
* `README.md` — liczba testów.
