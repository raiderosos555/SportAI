"""Template explainer (TASK 007 §7): answers about one coupon from real data only.

The default language layer: deterministic templates over the advisor report — no paid
model, nothing leaves the computer. Recognised questions (Polish, any wording close to):

* "Dlaczego ten typ ma tylko 65%?"            → where the probability of a leg comes from
* "Który mecz jest najbardziej ryzykowny?"    → the weakest link and why
* "Co się stanie, jeśli usunę trzeci mecz?"   → the coupon without that leg
* "Dlaczego kupon nie kwalifikuje się do profilu standardowego?" → profile rules check
* "Czy ten kupon ma większą szansę niż propozycja automatyczna?" → comparison
"""

import re
import unicodedata
from decimal import Decimal

from sports_edge import insights as ins
from sports_edge import my_coupon as mine

ORDINALS = {
    "pierwsz": 1,
    "drug": 2,
    "trzec": 3,
    "czwart": 4,
    "piat": 5,
    "szost": 6,
    "siodm": 7,
    "osm": 8,
    "dziewiat": 9,
    "dziesiat": 10,
}
PROFILE_WORDS = {
    "standard": "standard",
    "wysoka": "high_hit",
    "trafnosc": "high_hit",
    "wyzsza": "higher_payout",
    "wyplat": "higher_payout",
    "tasiem": "tasiemka",
}
EXAMPLES = (
    "Dlaczego ten typ ma tylko 65%?",
    "Który mecz jest najbardziej ryzykowny?",
    "Co się stanie, jeśli usunę trzeci mecz?",
    "Dlaczego ten kupon nie kwalifikuje się do profilu standardowego?",
    "Czy ten kupon ma większą szansę niż propozycja automatyczna?",
)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def leg_index(question: str, report: dict) -> int | None:
    """Which leg the question names: an ordinal, a number or a team name."""
    q = _plain(question)
    for stem, number in ORDINALS.items():
        if re.search(rf"\b{stem}\w*", q):
            return number - 1 if number <= len(report["legs"]) else None
    found = re.search(r"\b(\d{1,2})\s*(?:\.|mecz|typ|selekc|pozycj)", q)
    if found and 1 <= int(found.group(1)) <= len(report["legs"]):
        return int(found.group(1)) - 1
    for i, leg in enumerate(report["legs"]):
        for team in ins.teams(leg["event"]):
            name = _plain(team)
            if len(name) >= 3 and re.search(rf"{re.escape(name)}", q):
                return i
    percent = re.search(r"(\d{1,3})\s*%", q)
    if percent:
        value = int(percent.group(1)) / 100
        known = [(i, r) for i, r in enumerate(report["legs"]) if r.get("p_model") is not None]
        if known:
            return min(known, key=lambda item: abs(item[1]["p_model"] - value))[0]
    return None


def answer(
    question: str,
    report: dict,
    *,
    cart: list[dict] | None = None,
    rows: list | None = None,
    stake: Decimal | None = None,
    cost=None,
    on_date=None,
    profiles: dict | None = None,
    automatic: dict | None = None,
) -> dict:
    """{"answer": text, "facts": [...], "source": "szablon"} for one question."""
    q = _plain(question)
    legs = report["legs"]
    if not legs:
        return _out("Kupon jest pusty — dodaj selekcje, aby o nie zapytać.")
    if any(word in q for word in ("ryzykown", "najslabsz", "najgorsz", "slabe ogniwo")):
        return _weakest(report)
    if any(word in q for word in ("usun", "bez ", "wyrzuc", "skresl")):
        return _without(question, report, cart, rows, stake, cost, on_date)
    if "profil" in q or any(word in q for word in ("kwalifik", "tasiem")):
        return _profile(q, report, profiles or {})
    if "automatycz" in q or "propozycj" in q or "kupon dnia" in q:
        return _versus(report, automatic)
    if any(word in q for word in ("dlaczego", "skad", "czemu", "tylko")):
        index = leg_index(question, report)
        return _why(report, report["weakest"] if index is None else index)
    return _out(
        "Nie rozpoznałem pytania. Spróbuj na przykład: " + " • ".join(EXAMPLES),
        recognised=False,
    )


def _out(text: str, facts: list[str] | None = None, *, recognised: bool = True) -> dict:
    return {"answer": text, "facts": facts or [], "source": "szablon", "recognised": recognised}


def _why(report: dict, index: int | None) -> dict:
    if index is None:
        return _out("Nie wiem, o który typ chodzi — podaj numer meczu lub nazwę drużyny.")
    r = report["legs"][index]
    head = f"{index + 1}. {r['event']} — {r['pick']} @ {r['odds']}"
    if not r["supported"]:
        return _out(f"{head}: {r['reason']}.", [head])
    facts = [
        head,
        f"model: {r['model']}, szansa {ins.pct(r['p_model'])}, ostrożnie {ins.pct(r['p_low'])}",
        f"kalibracja: {r['calibration']}",
        f"status rynku: {r['status']}",
    ]
    text = (
        f"{head}: szansa {ins.pct(r['p_model'])} to wynik modelu {r['model']} po kalibracji na "
        f"danych sprzed okresu oceny; ostrożna wartość {ins.pct(r['p_low'])} to dolna granica "
        f"90% przedziału. {ins.sentence(r['calibration'])}. "
    )
    text += (
        "Ryzyka: " + "; ".join(r["risks"]) + "."
        if r["risks"]
        else "Nie widać dodatkowych ryzyk w danych."
    )
    return _out(text, facts)


def _weakest(report: dict) -> dict:
    index = report["weakest"]
    if index is None:
        return _out("Brak selekcji do oceny.")
    r = report["legs"][index]
    text = (
        f"Najbardziej ryzykowny jest typ {index + 1}: {r['event']} — {r['pick']}. "
        f"{report['weakest_reason']}."
    )
    ranked = sorted(
        (x for x in report["legs"] if x["supported"]), key=lambda x: x["p_low"], reverse=True
    )
    if len(ranked) > 1:
        text += " Kolejność od najpewniejszego: " + ", ".join(
            f"{x['event']} ({ins.pct(x['p_low'])})" for x in ranked
        )
    return _out(text, [r["event"], report["weakest_reason"] or ""])


def _without(question, report, cart, rows, stake, cost, on_date) -> dict:
    index = leg_index(question, report)
    if index is None or cart is None or rows is None or stake is None:
        return _out("Podaj, który mecz usunąć (np. „usuń trzeci mecz”).")
    rest = [leg for i, leg in enumerate(cart) if i != index]
    rest_rows = [row for i, row in enumerate(rows) if i != index]
    before = report["summary"]
    after = mine.summary(rest, rest_rows, stake=stake, cost=cost, on_date=on_date)
    name = f"{index + 1}. {report['legs'][index]['event']}"
    if not rest:
        return _out(f"Po usunięciu {name} kupon byłby pusty.")
    parts = [
        f"Po usunięciu {name}: kurs {before['total_odds']} → {after['total_odds']}",
        f"wypłata {ins.money(before.get('payout'))} → {ins.money(after.get('payout'))}",
    ]
    if before["probability"] is not None and after["probability"] is not None:
        parts.append(f"szansa {ins.pct(before['probability'])} → {ins.pct(after['probability'])}")
    elif after["probability"] is not None:
        parts.append(f"szansa da się oszacować: {ins.pct(after['probability'])}")
    else:
        parts.append(f"szansy nadal nie da się oszacować ({after['probability_note']})")
    return _out("; ".join(parts) + ".", parts)


def profile_failures(summary: dict, legs: list[dict], profile: dict) -> list[str]:
    """Rules of a coupon profile that this coupon does not meet."""
    out = []
    odds = summary.get("total_odds")
    low, high = profile.get("min_total_odds"), profile.get("max_total_odds")
    if odds is None:
        return ["brak kursu łącznego"]
    if low is not None and odds < Decimal(str(low)):
        out.append(f"kurs łączny {odds} poniżej {low}")
    if high is not None and odds > Decimal(str(high)):
        out.append(f"kurs łączny {odds} powyżej {high}")
    if len(legs) > int(profile.get("max_legs", 99)):
        out.append(f"{len(legs)} selekcji — limit profilu to {profile['max_legs']}")
    minimum = float(profile.get("min_leg_probability", 0))
    for i, r in enumerate(legs):
        if not r["supported"]:
            out.append(f"typ {i + 1}: brak zwalidowanego oszacowania")
        elif r["p_low"] < minimum:
            out.append(f"typ {i + 1}: ostrożna szansa {ins.pct(r['p_low'])} < {ins.pct(minimum)}")
        elif profile.get("require_predictive_validation") and r["status"] != "zwalidowany":
            out.append(f"typ {i + 1}: rynek bez walidacji predykcyjnej")
    floor = profile.get("min_coupon_probability")
    if floor is not None and (summary.get("probability_low") or 0) < float(floor):
        out.append(
            f"ostrożna szansa kuponu {ins.pct(summary.get('probability_low'))} < "
            f"{ins.pct(float(floor))}"
        )
    if summary.get("blocked"):
        out.append("kupon ma blokujące problemy (np. kurs niepotwierdzony)")
    return out


def _profile(q: str, report: dict, profiles: dict) -> dict:
    key = next((code for word, code in PROFILE_WORDS.items() if word in q), "standard")
    profile = profiles.get(key)
    if profile is None:
        return _out("Nie znam takiego profilu w polityce kuponów.")
    failures = profile_failures(report["summary"], report["legs"], profile)
    if not failures:
        return _out(f"Kupon spełnia wszystkie warunki profilu {profile['label']}.")
    return _out(
        f"Kupon nie kwalifikuje się do profilu {profile['label']}: " + "; ".join(failures) + ".",
        failures,
    )


def _versus(report: dict, automatic: dict | None) -> dict:
    mine_p = report["summary"]["probability_low"]
    if automatic is None:
        return _out("Brak propozycji automatycznej dla tego dnia — nie ma z czym porównać.")
    auto_p = automatic.get("p_conservative")
    label = automatic.get("label", "propozycja automatyczna")
    if mine_p is None:
        return _out(
            f"Twojego kuponu nie da się wiarygodnie oszacować, więc nie można go porównać z "
            f"propozycją {label} (ostrożnie {ins.pct(auto_p)})."
        )
    if auto_p is None:
        return _out("Propozycja automatyczna nie ma ostrożnego oszacowania.")
    better = "większą" if mine_p > auto_p else "mniejszą" if mine_p < auto_p else "taką samą"
    return _out(
        f"Twój kupon ma {better} ostrożną szansę ({ins.pct(mine_p)}) niż propozycja {label} "
        f"({ins.pct(auto_p)}). Kursy: {report['summary']['total_odds']} vs "
        f"{automatic.get('total_odds')}. Oba wyniki to przybliżenia (niezależność meczów).",
        [ins.pct(mine_p), ins.pct(auto_p)],
    )
