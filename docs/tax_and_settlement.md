# Podatki, koszty i rozliczenia

Weryfikacja źródeł: **31.10.2026**. Implementacja to deterministyczny kalkulator
badawczy pojedynczego zakładu; nie rozliczenie podatkowe konta ani pełny regulamin STS.

## Źródła i konfiguracja PL/STS

- Ministerstwo Finansów podaje **12%** dla odpowiedniej kategorii zakładów wzajemnych:
  [podatek od gier — stawki](https://podatki.gov.pl/pozostale/podatek-od-gier/stawki-i-limity).
- Aktualna strona MF podaje **10%** od wygranych w grach/zakładach:
  [PIT — stawki](https://podatki.gov.pl/podatki-osobiste/pit/stawki-i-limity).
- [STS — podatki](https://pomoc.sts.pl/jakie-podatki-sa-pobierane/) opisuje mnożnik
  stawki **0,88**, podatek wysokiej wygranej **10%** i próg **2280 zł**.
  Tekst opisowy mówi „równa lub wyższa”, ale przytoczone brzmienie art. 21 ust. 1 pkt 6a
  zwalnia wygrane nieprzekraczające progu. Nie rozstrzygamy operacyjnie tej sprzeczności.

Dlatego `pl_sts_research.toml` stosuje `reject_equal` na dokładnej granicy i dopuszcza
tylko zwykły win/loss. Zwroty, półrozliczenia, promocje, korekty i szczegółowe zaokrąglenia
STS pozostają do potwierdzenia na regulaminie oraz przykładach operatora.
Profil jest ważny wyłącznie dla dnia weryfikacji; nie ekstrapoluje prawa na poprzednie
sezony ani przyszłe lata. Przed eksperymentem trzeba utworzyć wersje dla właściwych dat.
Planowane zmiany przepisów nie są automatycznie stawką obowiązującą.

## Jednoznaczne przepływy

Nie mylić wpłaty na konto z kwotą przeznaczoną na kupon. W kodzie `deposited_stake = S`
to kwota obciążenia za pojedynczą selekcję, nie przelew/depozyt całego rachunku.

`T_s = S × t_s`, `E = S − T_s`. Dla wyniku udział wygrany `w` i zwrócony `r`:

| Wynik | w | r | Mnożnik bez kosztów |
|---|---:|---:|---|
| Win | 1 | 0 | o |
| Loss | 0 | 0 | 0 |
| Push / Void | 0 | 1 | 1 |
| Half-win | 0,5 | 0,5 | (o+1)/2 |
| Half-loss | 0 | 0,5 | 1/2 |

Wypłata przed potrąceniami `B = E × (w×o+r) + refund(T_s,r)`. Zasada zwrotu podatku
to osobna konfiguracja, nie założenie o wszystkich operatorach.
Jeśli podatek od wygranej ma zastosowanie, `T_w = B × t_w` od pełnej podstawy,
nie od samej nadwyżki ani od zysku. Próg sprawdzamy dla każdego możliwego wyniku,
nie dla średniej wypłaty. Brak części wygrywającej oznacza brak podatku od wygranej
w przyjętej generycznej regule; inne jurysdykcje mogą wymagać osobnego adaptera.

`C = max(B−S,0) × c`, `payout = B−T_w−C`, `net_profit = payout−S−fee`.
Prowizja w tym kalkulatorze jest od dodatniego zysku pojedynczego kuponu przed podatkiem
od wygranej; **nie** od wyniku całego rynku exchange. Dla exchange potrzebny osobny
adapter agregujący oraz definicja prowizji. Waluta w profilu i PaperBet musi być taka sama;
przyszła warstwa integracji egzekwuje to powiązanie. FX wymaga wersjonowanych kursów.

Brutto jest kontrfaktycznym `S × (w×o+r) − S` przy tym samym kursie, bez dodatkowych
kosztów. Marża operatora jest już w kursie i nie jest odliczana drugi raz.
Pośrednie etapy `CashFlow` pozwalają raportować oddzielnie podatek od stawki, wygranej,
prowizję i fee. ROI etapów używa tego samego mianownika S.

Przykład **syntetyczny**, bez podatku wysokiej wygranej: S=100, o=2, p=0,55.
Brutto EV=0,10; przy koszcie stawki 12% netto EV=`0,55×1,76−1=−0,032`.
Koszty zmieniają próg opłacalności. Dodatni brutto nie kwalifikuje do rentowności netto.

## Determinizm i granice

Kwoty i kursy w arytmetyce pieniężnej są `Decimal`, nie float. Lokalny kontekst ma
precyzję 38 i `ROUND_HALF_UP`; kwoty zaokrąglamy do skonfigurowanej jednostki (domyślnie
0,01). Jest to **jawna konwencja symulatora**, wymagająca porównania z fiskalnymi oraz
operatorskimi przykładami zanim wyniki zostaną nazwane dokładną repliką kuponów STS.
Stawki z nadmierną liczbą miejsc, niepoprawne kursy, NaN i wygasłe profile są odrzucane.

Promocje „bez podatku” domyślnie wyłączone. Osobna wersja profilu może powstać dopiero
po zapisaniu regulaminu, dat, dostępności dla konta, kwalifikujących kursów, limitów
i sposobu finansowania bonusu. Nie traktować reklamowego hasła jako t_s=0.
Nieobsługiwane: kupony AKO, systemy, cashout, freebet bez zwrotu stawki, podatki
na poziomie rocznym, bonusy i kursy boost. Pełne zasady ważności zakładu pozostają
w dedykowanym adapterze rynku/operatora, oddzielnie od kalkulatora przepływów.

## Profile aplikacji (TASK 002)

`config/tax/pl_sts_app.toml` i `config/tax/pl_superbet_app.toml` używają tych samych stawek
ustawowych (12% od stawki, 10% od wygranej powyżej 2280 zł, `reject_equal` na granicy,
wyłącznie win/loss), z oknem ważności 31.10–30.11.2026 wymagającym ponownej weryfikacji.
Profil Superbet jest **założeniem** — nie potwierdzono go w regulaminie operatora. Od TASK 003
jest zarchiwizowany w `config/archive/tax/` i nie jest wczytywany.
Profil badawczy `pl_sts_research.toml` z TASK 001 pozostaje bez zmian. EV netto
liczone jest przez `settle()` dla każdego wyniku osobno (próg wygranej przed wartością
oczekiwaną); uproszczenie `p × kurs × (1 − t) − 1` obowiązuje tylko poniżej progu
i tylko dla rynków bez zwrotu stawki (np. zakład bez remisu jest odrzucany).
AKO rozliczane jest jako jeden kupon z kursem łącznym; zwrot pojedynczej nogi w AKO
wymaga reguł operatora i trafia do rozliczenia ręcznego.

## TASK 003 — zasady STS potwierdzone w regulaminie

Źródła: „Regulamin Internetowych Zakładów Bukmacherskich” (plik 2025-07-08, **R**) i „Zasady
dot. zakładów na poszczególne sporty” (plik 2026-09-08, **Z**), odczytane 01.11.2026. Zapis
w `config/sts_rules.toml` z datą obowiązywania od 01.11.2026; rynek spoza listy jest
**niezweryfikowany** i nie może dać kandydata zwalidowanego.

| Zasada | Źródło | Zastosowanie w aplikacji |
|---|---|---|
| Wygrana = współczynnik stawki × kurs (przykład 0,88) | R 17.1 | EV netto i rozliczenie przez `settle()`; ostrzeżenie, że wiążący jest współczynnik z kuponu |
| Maksymalna wygrana 1 000 000 zł | R 17.2 | `create_coupon` odrzuca kupon, gdy stawka × kurs przekracza `limits.max_payout_pln` (ostrożnie: bez współczynnika) |
| 10% PIT od wygranej powyżej progu według odrębnych przepisów | R 17.5 | Profil `pl_sts_app.toml` bez zmian (próg 2280 zł, `reject_equal`) |
| Zwrot przy meczu nierozegranym, przerwanym lub przełożonym o ponad 24 h | R 15.3.c, Z 15.1.3 | Brak automatycznego zwrotu: wynik inny niż „zakończony” zostawia kupon do rozliczenia ręcznego |
| Anulowana noga AKO liczona z kursem 1,00 | R 15.7 | Zapisana w regułach; AKO z anulowaną nogą nadal rozliczane ręcznie |
| Zakład bez remisu: remis = zwrot | Z 1.8 | `payoff = void_on_draw`; EV z formułą binarną jest odrzucane |
| Linie całkowite sum (np. 2,0) mogą dać zwrot | — | Traktowane jako niezweryfikowane, nawet gdy rynek jest na liście |
| Piłka: czas regulaminowy z doliczonym, połowy osobno | Z 15.1.1, Z 15.2.1 | Rynki 1X2, podwójna szansa, sumy, obie strzelą, połowy — zweryfikowane |
| Koszykówka „mecz” bez dogrywki, „zwycięzca” z dogrywką; hokej — czas regulaminowy | Z 13.2.1–13.2.4, Z 11.1.1 | Zweryfikowane tylko rynki wpisane w `sts_rules.toml` |
