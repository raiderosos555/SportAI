"""HOTFIX 009.1: the browser QA picks the visible button; acceptance of the coupon builder.

Every selection below is SYNTHETIC test data, not a real fact. The coupon tests use the REAL
policy file (config/probability_policy.toml): its thresholds are checked, never lowered.
"""

import copy
import re
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import ROOT
from test_task005 import STS, cand

from sports_edge import qa_browser
from sports_edge.analysis import coupon_builder
from sports_edge.analysis.coupon_builder import VALIDATED, build_coupons
from sports_edge.probability_lab import load_probability_policy

POLICY = load_probability_policy(ROOT / "config" / "probability_policy.toml")


# ---------------------------------------------------------------- browser QA


class _Item:
    def __init__(self, visible: bool, enabled: bool = True, hidden_tab: bool = False):
        self.visible, self.enabled, self.hidden_tab = visible, enabled, hidden_tab

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def get_attribute(self, name):
        return "" if name == "disabled" and not self.enabled else None

    def evaluate(self, _script):
        return self.hidden_tab

    def text_content(self):
        return " ZAPISZ   USTAWIENIA "


class _Matches:
    def __init__(self, items):
        self.items = items

    def filter(self, has=None):
        assert has == ("text", "ZAPISZ USTAWIENIA", True)  # the exact label, not a substring
        return self

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _Page:
    def __init__(self, items):
        self.items = items

    def locator(self, selector):
        assert selector == "button"
        return _Matches(self.items)

    def get_by_text(self, text, exact=False):
        return ("text", text, exact)


def test_the_visible_button_is_chosen_when_the_first_match_is_hidden():
    qa_browser.LOOKUPS.clear()
    hidden, shown = _Item(False, hidden_tab=True), _Item(True)
    assert qa_browser._button(_Page([hidden, shown]), "ZAPISZ USTAWIENIA", timeout=0) is shown
    (entry,) = qa_browser.LOOKUPS
    assert (entry["found"], entry["visible"], entry["ok"]) == (2, 1, True)
    assert entry["elements"][0] == {
        "visible": False,
        "enabled": True,
        "disabled": False,
        "aria_disabled": None,
        "in_hidden_tab": True,
        "text": "ZAPISZ USTAWIENIA",
    }


@pytest.mark.parametrize(
    ("items", "why"),
    [
        ([_Item(False, hidden_tab=True)], "znaleziono 1, żaden nie jest widoczny"),
        ([_Item(True), _Item(True)], "znaleziono 2, widocznych jest 2 (niejednoznaczne)"),
        ([_Item(True, enabled=False)], "znaleziono 1, widoczny jest nieaktywny"),
        ([], "znaleziono 0, żaden nie jest widoczny"),
    ],
)
def test_no_guessed_click_without_one_visible_enabled_button(items, why):
    qa_browser.LOOKUPS.clear()
    with pytest.raises(qa_browser.ButtonNotFound, match=re.escape(why)):
        qa_browser._button(_Page(items), "ZAPISZ USTAWIENIA", timeout=0)
    assert qa_browser.LOOKUPS[-1]["ok"] is False  # the diagnosis is kept for the report


def test_the_automation_flow_opens_the_tab_of_each_button_and_never_forces_a_click():
    source = Path(qa_browser.__file__).read_text(encoding="utf-8")
    assert "force=True" not in source
    flow = source[source.index('page.goto(f"{base}/automatyzacja")') :]
    assert flow.index('_open_tab(page, "Procesy")') < flow.index('"ZAPISZ USTAWIENIA"')
    assert flow.index('_open_tab(page, "Przegląd")') < flow.index("for label, expected")
    assert [step[0] for step in qa_browser.WORKER_STEPS] == [
        "URUCHOM AUTOMATYZACJĘ",
        "WSTRZYMAJ AUTOMATYZACJĘ",
        "WZNÓW AUTOMATYZACJĘ",
        "ZATRZYMAJ AUTOMATYZACJĘ",
    ]


# ---------------------------------------------------------------- coupon builder


def _validated(n: int) -> list:
    """n validated selections of different matches, conservative 0.80 down to about 0.66."""
    odds = ("1.30", "1.35", "1.40", "1.45")
    return [cand(f"V{i:02d}", odds[i % 4], 0.84 - i * 0.002, 0.80 - i * 0.002) for i in range(n)]


def _experimental(n: int) -> list:
    """n STRONGER experimental selections (conservative about 0.90) of other matches."""
    return [
        cand(f"E{i:02d}", "1.25", 0.93, 0.90 - i * 0.001, predictive_status="experimental")
        for i in range(n)
    ]


def build(candidates):
    return build_coupons(candidates, POLICY, cost=STS.cost, on_date=STS.cost.valid_from)


def _legs(proposal) -> list[str]:
    return sorted(leg.odds_id for leg in proposal.legs)


def test_71_validated_and_30_stronger_experimental_selections():
    validated, experimental = _validated(71), _experimental(30)
    report = build([*experimental, *validated])
    # the reported pool (strongest 25 of any status) holds only experimental selections …
    assert len(report.pool) == 25
    assert not any(c.predictive_status == VALIDATED for c in report.pool)
    # … yet the profiles that require validation still build from the validated ones
    alone = build(validated)
    for name in ("high_hit", "standard"):
        proposal = report.proposals[name]
        assert proposal is not None, name
        assert all(leg.predictive_status == VALIDATED for leg in proposal.legs)
        assert _legs(proposal) == _legs(alone.proposals[name])  # nothing validated was lost


def test_high_hit_and_standard_search_their_own_pools(monkeypatch):
    seen: dict[str, list] = {}
    original = coupon_builder._optimise

    def spy(legs, profile, **kwargs):
        seen.setdefault(profile["label"], list(legs))
        return original(legs, profile, **kwargs)

    monkeypatch.setattr(coupon_builder, "_optimise", spy)
    build([*_experimental(30), *_validated(71)])
    for label, floor in (("WYSOKA TRAFNOŚĆ", 0.75), ("STANDARDOWY", 0.65)):
        legs = seen[label]
        assert legs and len(legs) <= int(POLICY.builder["candidates"])
        assert all(c.predictive_status == VALIDATED and c.p_low >= floor for c in legs)
    assert any(c.predictive_status != VALIDATED for c in seen["WYŻSZA WYPŁATA"])


def test_tasiemka_searches_40_candidates(monkeypatch):
    sizes: dict[str, int] = {}
    original = coupon_builder._optimise

    def spy(legs, profile, **kwargs):
        sizes.setdefault(profile["label"], len(legs))
        return original(legs, profile, **kwargs)

    monkeypatch.setattr(coupon_builder, "_optimise", spy)
    build(_validated(71))
    assert int(POLICY.builder["long_candidates"]) == 40
    assert sizes["TASIEMKA"] == 40  # was silently 25: a shadowed variable emptied the long pool
    assert sizes["STANDARDOWY"] == int(POLICY.builder["candidates"]) == 25


def test_no_qualifying_selection_never_lowers_the_thresholds():
    before = copy.deepcopy(POLICY.profiles)
    weak = [cand(f"W{i}", "1.60", 0.66, 0.60 + i * 0.001) for i in range(20)]  # below 0.65
    report = build(weak)
    assert POLICY.profiles == before  # the policy object is never changed
    for name, floor in (("high_hit", "75%"), ("standard", "65%"), ("tasiemka", "60%")):
        if name == "tasiemka":
            continue  # 0.60–0.62 passes the TASIEMKA floor of 60%
        assert report.proposals[name] is None
        assert report.refusal_codes[name] == "low_probability"
        assert f"≥ {floor}" in report.refusals[name]
    for name, proposal in report.proposals.items():
        if proposal is not None:
            profile = POLICY.profiles[name]
            assert all(leg.p_low >= profile["min_leg_probability"] for leg in proposal.legs)
            assert Decimal(str(profile["min_total_odds"])) <= proposal.total_odds


def test_no_coupon_or_alternative_repeats_a_match():
    # three markets of every match compete for the pool (per_event = 3)
    pool = []
    for i in range(30):
        pool.append(cand(f"M{i:02d}", "1.30", 0.85, 0.80 - i * 0.003))
        pool.append(
            cand(
                f"M{i:02d}",
                "1.42",
                0.80,
                0.76 - i * 0.003,
                market_key="double_chance",
                selection="home_draw",
            )
        )
        pool.append(cand(f"M{i:02d}", "1.55", 0.76, 0.70 - i * 0.003, line="2.5"))
    report = build(pool)
    built = 0
    for name, proposal in report.proposals.items():
        if proposal is None:
            continue
        coupons = [proposal, *report.alternatives.get(name, ())]
        used: set[str] = set()
        for coupon in coupons:
            events = [leg.event_id for leg in coupon.legs]
            assert len(events) == len(set(events)), (name, events)  # one leg per match
            assert not used & set(events), (name, events)  # alternatives are other matches
            used |= set(events)
            built += 1
    assert built >= 4


def test_refusal_report_names_the_real_reason():
    assert set(build([]).refusal_codes.values()) == {"no_candidates"}
    experimental_only = build(_experimental(10))
    assert experimental_only.refusal_codes["high_hit"] == "not_validated"
    assert "zwalidowanych predykcyjnie" in experimental_only.refusals["high_hit"]
    out_of_range = build([cand(f"R{i}", "1.80", 0.85, 0.80) for i in range(5)])
    assert out_of_range.refusal_codes["high_hit"] == "payout"  # 1.80 > 1.60, pairs 3.24
    assert "w przedziale 1.25–1.6" in out_of_range.refusals["high_hit"]
    floor = build([cand(f"F{i}", "1.15", 0.80, 0.78) for i in range(2)])
    assert floor.refusal_codes["high_hit"] == "coupon_probability"  # 0.78² = 0.61 < 0.65
    assert "niższa niż 65%" in floor.refusals["high_hit"]
