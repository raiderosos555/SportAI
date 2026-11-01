"""Sports Edge AI design system: colour tokens, one stylesheet and reusable components.

Colours and radii of the native widgets come from ``.streamlit/config.toml``; this module
only styles what Streamlit has no component for (cards, status pills, probability bars).
Every component escapes the text it receives, so ``unsafe_allow_html`` carries layout only.
Status colours always mean something: green = confirmed / won, red = error / lost,
amber = warning / uncertain, blue = information, grey = neutral.
"""

import html
from collections.abc import Iterable, Sequence
from string import Template

import streamlit as st

TOKENS = {
    "bg": "#0B1120",
    "surface": "#111C30",
    "raised": "#17243B",
    "border": "#22314D",
    "primary": "#22C55E",
    "info": "#38BDF8",
    "text": "#F8FAFC",
    "muted": "#94A3B8",
    "warn": "#F59E0B",
    "danger": "#EF4444",
}

CSS = Template(
    """
<style>
:root {
  --se-bg: $bg; --se-surface: $surface; --se-raised: $raised;
  --se-border: $border; --se-primary: $primary; --se-info: $info;
  --se-text: $text; --se-muted: $muted; --se-warn: $warn; --se-danger: $danger;
}
.block-container { padding-top: 1.4rem; max-width: 1440px; }
.se-header { margin: 0 0 1.1rem 0; }
.se-header .kicker { color: var(--se-primary); font-size: .74rem; font-weight: 700;
  letter-spacing: .14em; text-transform: uppercase; }
.se-header h1 { font-size: clamp(1.55rem, 2.6vw, 2.15rem); line-height: 1.15; margin: .15rem 0 .25rem;
  padding: 0; font-weight: 800; letter-spacing: -.01em; }
.se-header p { color: var(--se-muted); margin: 0; font-size: .95rem; }
.se-section { display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap;
  margin: 1.4rem 0 .6rem; }
.se-section h3 { font-size: 1.02rem; font-weight: 750; letter-spacing: .06em; margin: 0;
  padding: 0; text-transform: uppercase; }
.se-section span { color: var(--se-muted); font-size: .85rem; }
.se-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(150px, 100%), 1fr));
  gap: .75rem; margin: .2rem 0 1rem; }
.se-grid.wide { grid-template-columns: repeat(auto-fit, minmax(min(280px, 100%), 1fr)); }
.se-card { background: var(--se-surface); border: 1px solid var(--se-border);
  border-radius: 14px; padding: .95rem 1.05rem; min-width: 0; }
.se-card.raised { background: var(--se-raised); }
.se-card.accent { border-left: 3px solid var(--se-primary); }
.se-card.warn { border-left: 3px solid var(--se-warn); }
.se-card.bad { border-left: 3px solid var(--se-danger); }
.se-metric .label { color: var(--se-muted); font-size: .72rem; font-weight: 650;
  letter-spacing: .07em; text-transform: uppercase; }
.se-metric .value { font-size: 1.55rem; font-weight: 780; margin-top: .2rem;
  font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.se-metric .hint { color: var(--se-muted); font-size: .8rem; margin-top: .15rem; }
.se-pill { display: inline-flex; align-items: center; gap: .35rem; padding: .12rem .6rem;
  border-radius: 999px; font-size: .74rem; font-weight: 650; border: 1px solid;
  white-space: nowrap; margin: 0 .3rem .3rem 0; }
.se-pill.ok { color: #86EFAC; border-color: rgba(34,197,94,.45); background: rgba(34,197,94,.10); }
.se-pill.info { color: #7DD3FC; border-color: rgba(56,189,248,.45); background: rgba(56,189,248,.10); }
.se-pill.warn { color: #FCD34D; border-color: rgba(245,158,11,.5); background: rgba(245,158,11,.10); }
.se-pill.bad { color: #FCA5A5; border-color: rgba(239,68,68,.5); background: rgba(239,68,68,.10); }
.se-pill.neutral { color: #CBD5E1; border-color: #334155; background: rgba(148,163,184,.08); }
.se-match .teams { font-size: 1.04rem; font-weight: 750; overflow-wrap: anywhere; }
.se-match .meta { color: var(--se-muted); font-size: .8rem; margin: .1rem 0 .55rem; }
.se-pick { display: flex; justify-content: space-between; align-items: flex-end; gap: .8rem;
  flex-wrap: wrap; margin-top: .35rem; }
.se-pick .what { font-weight: 650; overflow-wrap: anywhere; }
.se-pick .what small { display: block; color: var(--se-muted); font-weight: 500; }
.se-pick .num { text-align: right; font-variant-numeric: tabular-nums; }
.se-pick .num b { font-size: 1.45rem; }
.se-pick .num small { display: block; color: var(--se-muted); }
.se-bars { display: grid; gap: .45rem; margin: .3rem 0 .4rem; }
.se-bar-row { display: grid; grid-template-columns: minmax(84px, 1.1fr) 3fr 4.2rem; gap: .65rem;
  align-items: center; }
.se-bar-row .name { overflow-wrap: anywhere; font-size: .9rem; }
.se-bar-row .val { text-align: right; font-weight: 700; font-variant-numeric: tabular-nums; }
.se-bar { height: .55rem; background: #1B2942; border-radius: 999px; overflow: hidden; }
.se-bar > i { display: block; height: 100%; border-radius: 999px; background: var(--se-info); }
.se-bar.lead > i { background: var(--se-primary); }
.se-compare { display: grid; grid-template-columns: 1fr minmax(110px, 1.2fr) 1fr; gap: .5rem;
  align-items: center; padding: .4rem 0; border-bottom: 1px solid var(--se-border); }
.se-compare:last-child { border-bottom: 0; }
.se-compare .l, .se-compare .r { font-weight: 700; font-variant-numeric: tabular-nums; }
.se-compare .r { text-align: right; }
.se-compare .m { text-align: center; color: var(--se-muted); font-size: .82rem; }
.se-compare .m small { display: block; font-size: .72rem; }
.se-split { display: flex; height: .4rem; border-radius: 999px; overflow: hidden; margin-top: .25rem;
  background: #1B2942; }
.se-split i { display: block; height: 100%; }
.se-split i.a { background: var(--se-primary); } .se-split i.b { background: var(--se-info); }
.se-empty { text-align: left; padding: 1.1rem 1.2rem; }
.se-empty h4 { margin: 0 0 .3rem; padding: 0; font-size: 1.02rem; }
.se-empty p { margin: 0; color: var(--se-muted); }
.se-list { margin: .2rem 0 0; padding-left: 1.1rem; }
.se-list li { margin: .18rem 0; }
.se-kv { display: grid; grid-template-columns: auto 1fr; gap: .25rem .9rem; font-size: .88rem; }
.se-kv dt { color: var(--se-muted); } .se-kv dd { margin: 0; overflow-wrap: anywhere; }
.se-brand { font-weight: 800; letter-spacing: .12em; font-size: .95rem; }
.se-brand small { display: block; color: var(--se-muted); letter-spacing: .02em; font-weight: 500;
  font-size: .76rem; margin-top: .15rem; }
.se-side-row { display: flex; justify-content: space-between; gap: .5rem; font-size: .8rem;
  padding: .18rem 0; color: var(--se-muted); }
.se-side-row b { color: var(--se-text); font-weight: 600; text-align: right; }
.se-title { font-size: 1.02rem; font-weight: 800; letter-spacing: .09em; text-transform: uppercase;
  word-break: keep-all; overflow-wrap: normal; margin-bottom: .2rem; }
.se-desc { color: var(--se-muted); font-size: .82rem; margin: 0 0 .5rem; }
.se-leg { border-top: 1px solid var(--se-border); padding: .55rem 0 .15rem; }
.se-leg .ev { font-weight: 700; overflow-wrap: anywhere; }
.se-leg small { display: block; color: var(--se-muted); font-size: .76rem; }
.se-leg .row { display: flex; justify-content: space-between; gap: .6rem; margin: .2rem 0;
  font-weight: 650; }
.se-leg .row b { font-variant-numeric: tabular-nums; white-space: nowrap; }
/* keyed card rows wrap instead of squeezing three cards into a narrow screen */
[class*="st-key-cards"] div[data-testid="stHorizontalBlock"] { flex-wrap: wrap; row-gap: 1rem; }
[class*="st-key-cards"] div[data-testid="stColumn"] { min-width: min(290px, 100%);
  flex: 1 1 290px !important; }
@media (max-width: 640px) {
  .block-container { padding-left: .8rem; padding-right: .8rem; }
  .se-bar-row { grid-template-columns: minmax(70px, 1fr) 2fr 3.6rem; }
  .se-metric .value { font-size: 1.3rem; }
}
</style>
"""
).substitute(TOKENS)

LEVELS = {"ok", "info", "warn", "bad", "neutral"}


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


# ---------------------------------------------------------------- text blocks


def app_header(title: str, subtitle: str = "", *, kicker: str = "Sports Edge AI") -> None:
    _html(
        f'<div class="se-header"><div class="kicker">{esc(kicker)}</div><h1>{esc(title)}</h1>'
        + (f"<p>{esc(subtitle)}</p>" if subtitle else "")
        + "</div>"
    )


def section(title: str, caption: str = "") -> None:
    _html(
        f'<div class="se-section"><h3>{esc(title)}</h3>'
        + (f"<span>{esc(caption)}</span>" if caption else "")
        + "</div>"
    )


def pill(text: str, level: str = "neutral") -> str:
    return f'<span class="se-pill {level if level in LEVELS else "neutral"}">{esc(text)}</span>'


def pills(items: Iterable[tuple[str, str]]) -> None:
    _html("".join(pill(text, level) for text, level in items))


def card(body: str, *, tone: str = "", raised: bool = False) -> str:
    classes = " ".join(x for x in ("se-card", tone, "raised" if raised else "") if x)
    return f'<div class="{classes}">{body}</div>'


def bullets(points: Sequence[str]) -> str:
    return '<ul class="se-list">' + "".join(f"<li>{esc(p)}</li>" for p in points) + "</ul>"


def explanation(title: str, points: Sequence[str], *, tone: str = "") -> None:
    """ExplanationPanel: a title and short plain-language points."""
    _html(card(f"<b>{esc(title)}</b>" + bullets(points), tone=tone))


def empty_state(title: str, message: str, *, tone: str = "") -> None:
    _html(
        card(
            f'<div class="se-empty"><h4>{esc(title)}</h4><p>{esc(message)}</p></div>',
            tone=tone,
        )
    )


def error_state(title: str, message: str) -> None:
    empty_state(title, message, tone="bad")


def key_values(pairs: Sequence[tuple[str, str]]) -> str:
    return (
        '<dl class="se-kv">'
        + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in pairs)
        + "</dl>"
    )


# ---------------------------------------------------------------- numbers


def metric_cards(items: Sequence[tuple[str, str, str]]) -> None:
    """MetricCard grid: (label, value, hint). Wraps to one column on phones."""
    _html(
        '<div class="se-grid">'
        + "".join(
            card(
                f'<div class="se-metric"><div class="label">{esc(label)}</div>'
                f'<div class="value">{esc(value)}</div>'
                + (f'<div class="hint">{esc(hint)}</div>' if hint else "")
                + "</div>"
            )
            for label, value, hint in items
        )
        + "</div>"
    )


def probability_bars(items: Sequence[tuple[str, float | None, str]]) -> str:
    """ProbabilityBar rows (label, probability, shown value); the largest is highlighted."""
    top = max((p for _, p, _ in items if p is not None), default=None)
    rows = []
    for label, p, shown in items:
        width = 0 if p is None else max(0.0, min(1.0, p)) * 100
        lead = " lead" if p is not None and p == top else ""
        rows.append(
            f'<div class="se-bar-row"><div class="name">{esc(label)}</div>'
            f'<div class="se-bar{lead}"><i style="width:{width:.1f}%"></i></div>'
            f'<div class="val">{esc(shown)}</div></div>'
        )
    return '<div class="se-bars">' + "".join(rows) + "</div>"


def comparison_rows(rows: Sequence[tuple[str, str, str, float | None, float | None, str]]) -> str:
    """Two teams side by side: (label, left, right, left value, right value, note)."""
    out = []
    for label, left, right, a, b, note in rows:
        split = ""
        if a is not None and b is not None and a + b > 0:
            share = a / (a + b) * 100
            split = (
                f'<div class="se-split"><i class="a" style="width:{share:.1f}%"></i>'
                f'<i class="b" style="width:{100 - share:.1f}%"></i></div>'
            )
        out.append(
            f'<div class="se-compare"><div class="l">{esc(left)}</div>'
            f'<div class="m">{esc(label)}{split}'
            + (f"<small>{esc(note)}</small>" if note else "")
            + f'</div><div class="r">{esc(right)}</div></div>'
        )
    return "".join(out)


# ---------------------------------------------------------------- domain cards

RISK = {
    "high": ("Wyższa wiarygodność prognozy", "ok"),
    "moderate": ("Umiarkowana wiarygodność", "info"),
    "elevated": ("Podwyższona niepewność", "warn"),
    "insufficient": ("Niewystarczające dane", "neutral"),
}


def risk_pill(level: str) -> str:
    text, tone = RISK.get(level, RISK["insufficient"])
    return pill(text, tone)


def freshness_pill(age_hours: float | None, limit_hours: float | None) -> str:
    if age_hours is None:
        return pill("Brak dokumentu STS", "bad")
    shown = f"{age_hours:.1f}".replace(".", ",")
    if limit_hours is not None and age_hours <= limit_hours:
        return pill(f"Oferta STS potwierdzona ({shown} h)", "ok")
    return pill(f"Kurs niepotwierdzony — dokument sprzed {shown} h", "warn")


def match_card(
    *,
    teams: str,
    meta: str,
    pick: str,
    market: str,
    probability: str,
    conservative: str,
    odds: str,
    badges: Sequence[tuple[str, str]] = (),
    note: str = "",
) -> str:
    """MatchCard / PredictionCard with the selected type and its numbers."""
    return card(
        '<div class="se-match">'
        f'<div class="teams">{esc(teams)}</div><div class="meta">{esc(meta)}</div>'
        + "".join(pill(text, level) for text, level in badges)
        + '<div class="se-pick">'
        f'<div class="what">{esc(pick)}<small>{esc(market)}</small></div>'
        f'<div class="num"><b>{esc(probability)}</b><small>ostrożnie {esc(conservative)} · '
        f"kurs {esc(odds)}</small></div></div>"
        + (f'<div class="meta" style="margin-top:.5rem">{esc(note)}</div>' if note else "")
        + "</div>",
        tone="accent",
    )


def card_grid(cards: Sequence[str], *, wide: bool = True) -> None:
    _html(f'<div class="se-grid{" wide" if wide else ""}">' + "".join(cards) + "</div>")


def side_rows(rows: Sequence[tuple[str, str]]) -> None:
    _html(
        "".join(
            f'<div class="se-side-row"><span>{esc(k)}</span><b>{esc(v)}</b></div>' for k, v in rows
        )
    )


def legs_html(legs: Sequence[dict]) -> str:
    """Coupon legs: match, time and league, the pick in plain words, odds and probability."""
    out = []
    for i, leg in enumerate(legs, 1):
        out.append(
            '<div class="se-leg">'
            f'<div class="ev">{i}. {esc(leg["event"])}</div>'
            f"<small>{esc(leg['when'])}</small>"
            f'<div class="row"><span>{esc(leg["pick"])}</span><b>@ {esc(leg["odds"])}</b></div>'
            f"<small>{esc(leg['detail'])}</small></div>"
        )
    return "".join(out)


def brand() -> None:
    _html(
        '<div class="se-brand">SPORTS EDGE AI'
        "<small>Analiza sportowa i generator kuponów STS</small></div>"
    )
