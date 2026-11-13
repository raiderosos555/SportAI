"""The shared MÓJ KUPON cart of the interface.

One cart per browser session, available on every page; it is also kept in a local draft
file (data/drafts/, never committed) so a page reload does not lose it. Every page shows
the same "DODAJ DO MOJEGO KUPONU" button; one click opens the full analysis.
"""

from decimal import Decimal, InvalidOperation

import streamlit as st

from sports_edge import insights as ins
from sports_edge import my_coupon as mine
from sports_edge.analysis.edge_engine import Candidate
from sports_edge.app import common as c

KEY = "my_coupon_cart"
MESSAGE = "my_coupon_message"
ADD_LABEL = "DODAJ DO MOJEGO KUPONU"


def _path():
    name = "my_coupon_demo.json" if c.demo_mode() else "my_coupon.json"
    return c.paths().data / "drafts" / name


def state() -> dict:
    if KEY not in st.session_state:
        st.session_state[KEY] = mine.load_draft(_path())
    return st.session_state[KEY]


def legs() -> list[dict]:
    return state()["legs"]


def stake() -> Decimal:
    try:
        value = Decimal(state()["stake"]).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("10.00")
    return value if value > 0 else Decimal("10.00")


def _save() -> None:
    try:
        mine.save_draft(_path(), legs(), stake())
    except OSError:
        pass  # the session cart still works without the draft file


def set_stake(value: Decimal) -> None:
    state()["stake"] = str(value)
    _save()


def set_legs(new: list[dict]) -> None:
    state()["legs"] = list(new)
    _save()


def remove(selection_id: str) -> None:
    set_legs(mine.remove(legs(), selection_id))


def clear() -> None:
    set_legs([])


def rows_for(cart: list[dict]) -> tuple[list[Candidate | None], list[Candidate]]:
    """Current offer rows of the cart legs and every candidate of their days."""
    pool: list[Candidate] = []
    for day in sorted({leg["day"] for leg in cart}):
        pool += c.day_candidates(day)[1]
    return mine.current(cart, pool), pool


def _add(candidate: Candidate, day: str) -> None:
    cart = legs()
    _rows, pool = rows_for(cart)
    updated, why = mine.add(cart, [*pool, candidate], candidate, day=day, now=c.now())
    if why:
        st.session_state[MESSAGE] = ("warn", why)
        return
    set_legs(updated)
    st.session_state[MESSAGE] = (
        "ok",
        f"Dodano do Mojego kuponu: {candidate.event} — {ins.candidate_pick(candidate)}",
    )


def add_many(items: list[tuple[Candidate, str]]) -> None:
    """Add several selections (candidate, offer day); refusals are reported together."""
    added, refused = 0, []
    for candidate, day in items:
        cart = legs()
        _rows, pool = rows_for(cart)
        updated, why = mine.add(cart, [*pool, candidate], candidate, day=day, now=c.now())
        if why:
            refused.append(f"{candidate.event}: {why}")
            continue
        set_legs(updated)
        added += 1
    text = f"Dodano do Mojego kuponu: {added}"
    if refused:
        text += " · pominięto: " + "; ".join(refused[:3])
    st.session_state[MESSAGE] = ("ok" if not refused else "warn", text)


def started_legs(cart: list[dict], now=None) -> list[dict]:
    """TASK 019 §4.1: legs whose match has already started (or has no known start)."""
    moment = (now or c.now()).isoformat()
    return [leg for leg in cart if leg.get("start_at") and leg["start_at"] <= moment]


def remove_started(now=None) -> int:
    """ "Usuń rozpoczęte (N)": drop the started legs from the cart (the draft only)."""
    gone = {leg["selection_id"] for leg in started_legs(legs(), now)}
    set_legs([leg for leg in legs() if leg["selection_id"] not in gone])
    return len(gone)


def counted(cart: list[dict], rows: list, *, late: bool, now=None) -> tuple[list, list]:
    """(legs, rows) that count in the chance of a NEW coupon: started legs are left out,
    except in "Wpis po fakcie" (a coupon already placed in STS)."""
    if late:
        return cart, rows
    gone = {leg["selection_id"] for leg in started_legs(cart, now)}
    kept = [
        (leg, row) for leg, row in zip(cart, rows, strict=True) if leg["selection_id"] not in gone
    ]
    return [k[0] for k in kept], [k[1] for k in kept]


def contains(candidate: Candidate) -> bool:
    return any(leg["selection_id"] == candidate.selection_id for leg in legs())


def add_button(candidate: Candidate, *, day: str, key: str, compact: bool = False) -> None:
    """The shared button; a selection already on the coupon shows a disabled marker."""
    if contains(candidate):
        st.button(
            "Na kuponie",
            key=key,
            icon=":material/check:",
            disabled=True,
            width="stretch" if compact else "content",
        )
        return
    st.button(
        "Dodaj" if compact else ADD_LABEL,
        key=key,
        icon=":material/add_shopping_cart:",
        on_click=_add,
        args=(candidate, day),
        width="stretch" if compact else "content",
        help="Dodaje typ do Twojego kuponu (strona Mój kupon)",
    )


def flash() -> None:
    """Show (once) the result of the last add."""
    message = st.session_state.pop(MESSAGE, None)
    if message:
        level, text = message
        st.toast(text, icon=":material/check_circle:" if level == "ok" else ":material/warning:")


def summary_line() -> str:
    n = len(legs())
    return ins.plural(n, "selekcja", "selekcje", "selekcji") if n else "pusty"
