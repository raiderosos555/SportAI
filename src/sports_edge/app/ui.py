"""Sports Edge AI design system (3.0): tokens, one stylesheet and reusable components.

Colours and radii of the native widgets come from ``.streamlit/config.toml``; this module
styles what Streamlit has no component for. Every component escapes the text it receives,
so ``unsafe_allow_html`` carries layout only.

Layout rules (TASK 009):

* a number, odds, percentage, date or amount never wraps (``.se-num``, ``white-space:
  nowrap``); words break only between words, never letter by letter;
* grids have readable minimum widths (KPI 220 px, stat 128 px, cards 320 px) and wrap to
  fewer columns instead of squeezing; on a phone everything is one column;
* three levels of information: the essentials first, the analysis below, technical details
  in ``details()`` panels that are closed by default.

Status colours always mean something: green = confirmed / won, red = error / lost, amber =
warning / uncertain, blue = information, grey = neutral. Coupon profiles have their own
identity colours (outlined badges), never a status colour alone.
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
    "violet": "#A78BFA",
}

CSS = Template(
    """
<style>
:root {
  --se-bg: $bg; --se-surface: $surface; --se-raised: $raised;
  --se-border: $border; --se-primary: $primary; --se-info: $info;
  --se-text: $text; --se-muted: $muted; --se-warn: $warn; --se-danger: $danger;
  --se-violet: $violet; --se-gap: .85rem; --se-radius: 14px;
}
.block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1380px; }
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li,
[data-testid="stCaptionContainer"] { overflow-wrap: break-word; word-break: normal; }
.se-num { white-space: nowrap; font-variant-numeric: tabular-nums; }
.se-muted { color: var(--se-muted); }
/* ---------------------------------------------------------------- headings */
.se-header { margin: 0 0 1rem 0; }
.se-header .kicker { color: var(--se-primary); font-size: .74rem; font-weight: 700;
  letter-spacing: .14em; text-transform: uppercase; }
.se-header h1 { font-size: clamp(1.45rem, 2.4vw, 2.05rem); line-height: 1.15;
  margin: .15rem 0 .25rem; padding: 0; font-weight: 800; letter-spacing: -.01em; }
.se-header p { color: var(--se-muted); margin: 0; font-size: .95rem; }
.se-section { display: flex; align-items: baseline; gap: .35rem .7rem; flex-wrap: wrap;
  margin: 1.5rem 0 .65rem; }
.se-section h3 { font-size: 1rem; font-weight: 760; letter-spacing: .06em; margin: 0;
  padding: 0; text-transform: uppercase; }
.se-section span { color: var(--se-muted); font-size: .86rem; }
/* ---------------------------------------------------------------- grids and cards */
.se-grid { display: grid; gap: var(--se-gap); margin: .25rem 0 1rem;
  grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); }
.se-grid.wide { grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr)); }
.se-grid.two { grid-template-columns: repeat(auto-fit, minmax(min(440px, 100%), 1fr)); }
.se-grid.markets { grid-template-columns: repeat(auto-fit, minmax(min(270px, 100%), 1fr)); }
.se-card { background: var(--se-surface); border: 1px solid var(--se-border);
  border-radius: var(--se-radius); padding: .95rem 1.05rem; min-width: 0; }
.se-card.raised { background: var(--se-raised); }
.se-card.accent, .se-card.ok { border-left: 3px solid var(--se-primary); }
.se-card.info { border-left: 3px solid var(--se-info); }
.se-card.warn { border-left: 3px solid var(--se-warn); }
.se-card.bad { border-left: 3px solid var(--se-danger); }
.se-card > b:first-child { display: block; margin-bottom: .25rem; }
/* ---------------------------------------------------------------- KPI */
.se-kpi { display: flex; flex-direction: column; gap: .2rem; }
.se-kpi .label { color: var(--se-muted); font-size: .74rem; font-weight: 650;
  letter-spacing: .06em; text-transform: uppercase; line-height: 1.3; }
.se-kpi .value { font-size: 1.6rem; font-weight: 780; line-height: 1.15; white-space: nowrap;
  font-variant-numeric: tabular-nums; overflow: hidden; text-overflow: ellipsis; }
.se-kpi .hint { color: var(--se-muted); font-size: .82rem; line-height: 1.35; }
/* ---------------------------------------------------------------- badges */
.se-pill { display: inline-flex; align-items: center; gap: .35rem; padding: .14rem .62rem;
  border-radius: 999px; font-size: .76rem; font-weight: 650; border: 1px solid;
  white-space: nowrap; margin: 0 .3rem .3rem 0; max-width: 100%; }
.se-pill.ok { color: #86EFAC; border-color: rgba(34,197,94,.45); background: rgba(34,197,94,.10); }
.se-pill.info { color: #7DD3FC; border-color: rgba(56,189,248,.45); background: rgba(56,189,248,.10); }
.se-pill.warn { color: #FCD34D; border-color: rgba(245,158,11,.5); background: rgba(245,158,11,.10); }
.se-pill.bad { color: #FCA5A5; border-color: rgba(239,68,68,.5); background: rgba(239,68,68,.10); }
.se-pill.neutral { color: #CBD5E1; border-color: #334155; background: rgba(148,163,184,.08); }
.se-pill.wrap { white-space: normal; }
.se-profile { display: inline-flex; align-items: center; gap: .45rem; font-weight: 800;
  letter-spacing: .07em; text-transform: uppercase; font-size: .8rem; padding: .22rem .7rem;
  border-radius: 8px; white-space: nowrap; border: 1.5px solid; }
.se-profile::before { content: ""; width: .55rem; height: .55rem; border-radius: 2px;
  background: currentColor; }
.se-profile.green { color: #86EFAC; border-color: rgba(34,197,94,.55); }
.se-profile.blue { color: #7DD3FC; border-color: rgba(56,189,248,.55); }
.se-profile.amber { color: #FCD34D; border-color: rgba(245,158,11,.6); }
.se-profile.violet { color: #C4B5FD; border-color: rgba(167,139,250,.65);
  background: rgba(167,139,250,.10); }
.se-profile.slate { color: #CBD5E1; border-color: #475569; }
.se-profile.teal { color: #5EEAD4; border-color: rgba(20,184,166,.6); }
/* ---------------------------------------------------------------- stat strip */
.se-stats { display: grid; gap: .5rem; margin: .65rem 0;
  grid-template-columns: repeat(auto-fit, minmax(min(128px, 100%), 1fr)); }
.se-stat { background: var(--se-raised); border-radius: 10px; padding: .5rem .7rem; min-width: 0; }
.se-stat .l { color: var(--se-muted); font-size: .72rem; letter-spacing: .05em;
  text-transform: uppercase; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.se-stat .v { font-weight: 760; font-size: 1.12rem; white-space: nowrap;
  font-variant-numeric: tabular-nums; }
.se-stat.em .v { color: #86EFAC; }
/* ---------------------------------------------------------------- list rows */
.se-rows { display: grid; gap: .5rem; margin: .2rem 0 .9rem; }
.se-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .35rem 1rem;
  align-items: center; background: var(--se-surface); border: 1px solid var(--se-border);
  border-radius: 12px; padding: .7rem .95rem; }
.se-row.ok { border-left: 3px solid var(--se-primary); }
.se-row.warn { border-left: 3px solid var(--se-warn); }
.se-row.bad { border-left: 3px solid var(--se-danger); }
.se-row.info { border-left: 3px solid var(--se-info); }
.se-row .t { font-weight: 700; line-height: 1.3; }
.se-row .m { color: var(--se-muted); font-size: .83rem; margin-top: .12rem; }
.se-row .b { margin-top: .35rem; }
.se-row .b .se-pill { margin-bottom: 0; }
.se-row .r { text-align: right; }
.se-row .r b { white-space: nowrap; font-size: 1.12rem; font-variant-numeric: tabular-nums; }
.se-row .r small { display: block; color: var(--se-muted); white-space: nowrap; font-size: .8rem; }
/* ---------------------------------------------------------------- coupon legs */
.se-legs { margin: .3rem 0 .2rem; }
.se-leg { display: grid; grid-template-columns: 1.7rem minmax(0, 1fr) auto; gap: .15rem .7rem;
  padding: .6rem 0; border-top: 1px solid var(--se-border); align-items: start; }
.se-leg .no { color: var(--se-muted); font-weight: 700; font-variant-numeric: tabular-nums; }
.se-leg .ev { font-weight: 700; line-height: 1.3; }
.se-leg .sub { color: var(--se-muted); font-size: .8rem; }
.se-coupon-status { margin: .35rem 0 .45rem; }
.se-coupon-status .sub { color: var(--se-muted); font-size: .85rem; margin-top: .2rem; }
.se-leg .pick { margin-top: .15rem; }
.se-leg .odds { text-align: right; font-weight: 760; white-space: nowrap;
  font-variant-numeric: tabular-nums; }
.se-leg .odds small { display: block; color: var(--se-muted); font-weight: 500; font-size: .78rem; }
.se-leg .note { grid-column: 2 / -1; color: var(--se-info); font-size: .78rem; }
/* ---------------------------------------------------------------- probability bars */
.se-market h5 { display: flex; justify-content: space-between; align-items: center; gap: .4rem;
  flex-wrap: wrap; font-size: .8rem; text-transform: uppercase; letter-spacing: .07em;
  color: var(--se-muted); margin: 0 0 .55rem; padding: 0; }
.se-market h5 .se-pill { margin: 0; }
.se-bars { display: grid; gap: .5rem; margin: .3rem 0 .4rem; }
.se-bar-row { display: grid; grid-template-columns: minmax(6.5rem, 1.25fr) minmax(3.5rem, 3fr) 4.4rem;
  gap: .65rem; align-items: center; }
.se-bar-row .name { font-size: .9rem; line-height: 1.25; }
.se-bar-row .val { text-align: right; font-weight: 720; white-space: nowrap;
  font-variant-numeric: tabular-nums; }
.se-bar { height: .6rem; background: #1B2942; border-radius: 999px; overflow: hidden; }
.se-bar > i { display: block; height: 100%; border-radius: 999px; background: var(--se-info); }
.se-bar.lead > i { background: var(--se-primary); }
/* ---------------------------------------------------------------- comparison */
.se-table-wrap { overflow-x: auto; }
.se-table { width: 100%; border-collapse: collapse; font-size: .9rem; }
.se-table th, .se-table td { padding: .52rem .6rem; border-bottom: 1px solid var(--se-border);
  text-align: left; vertical-align: top; }
.se-table tr:last-child td { border-bottom: 0; }
.se-table th { color: var(--se-muted); font-size: .74rem; font-weight: 700;
  letter-spacing: .05em; text-transform: uppercase; }
.se-table th.v, .se-table td.v { text-align: center; }
.se-table td.v { font-weight: 720; white-space: nowrap; font-variant-numeric: tabular-nums; }
.se-table td.v.better { color: #86EFAC; }
.se-table td.v.none { color: var(--se-muted); font-weight: 500; }
.se-table td small { display: block; color: var(--se-muted); font-size: .74rem; font-weight: 500; }
.se-compare { display: grid; grid-template-columns: 1fr minmax(110px, 1.2fr) 1fr; gap: .5rem;
  align-items: center; padding: .4rem 0; border-bottom: 1px solid var(--se-border); }
.se-compare:last-child { border-bottom: 0; }
.se-compare .l, .se-compare .r { font-weight: 700; white-space: nowrap;
  font-variant-numeric: tabular-nums; }
.se-compare .r { text-align: right; }
.se-compare .m { text-align: center; color: var(--se-muted); font-size: .82rem; }
.se-compare .m small { display: block; font-size: .72rem; }
.se-split { display: flex; height: .4rem; border-radius: 999px; overflow: hidden;
  margin-top: .25rem; background: #1B2942; }
.se-split i { display: block; height: 100%; }
.se-split i.a { background: var(--se-primary); } .se-split i.b { background: var(--se-info); }
/* ---------------------------------------------------------------- factors and summary */
.se-factors { display: grid; gap: .5rem; }
.se-factor { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .2rem .8rem;
  align-items: start; padding: .45rem 0; border-bottom: 1px solid var(--se-border); }
.se-factor:last-child { border-bottom: 0; }
.se-factor .x { line-height: 1.35; }
.se-factor .se-pill { margin: 0; }
.se-summary { display: grid; gap: .65rem; }
.se-summary .k { color: var(--se-muted); font-size: .72rem; font-weight: 700;
  letter-spacing: .07em; text-transform: uppercase; }
.se-summary .t { line-height: 1.45; }
/* ---------------------------------------------------------------- match header */
.se-mh { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: .5rem 1rem; align-items: center; }
.se-mh .team { font-size: clamp(1.15rem, 2.1vw, 1.6rem); font-weight: 800; line-height: 1.2; }
.se-mh .team.away { text-align: right; }
.se-mh .vs { color: var(--se-muted); font-weight: 700; }
.se-mh .meta { grid-column: 1 / -1; color: var(--se-muted); font-size: .9rem; }
.se-mh .badges { grid-column: 1 / -1; }
/* ---------------------------------------------------------------- status strip */
.se-status { display: grid; gap: .6rem; margin: .2rem 0 1rem;
  grid-template-columns: repeat(auto-fit, minmax(min(210px, 100%), 1fr)); }
.se-status > div { background: var(--se-surface); border: 1px solid var(--se-border);
  border-radius: 12px; padding: .6rem .85rem; min-width: 0; }
.se-status .l { color: var(--se-muted); font-size: .72rem; letter-spacing: .06em;
  text-transform: uppercase; }
.se-status .v { font-weight: 720; display: flex; align-items: center; gap: .45rem;
  line-height: 1.3; }
.se-status .v::before { content: ""; flex: none; width: .55rem; height: .55rem;
  border-radius: 999px; background: #64748B; }
.se-status .ok .v::before { background: var(--se-primary); }
.se-status .warn .v::before { background: var(--se-warn); }
.se-status .bad .v::before { background: var(--se-danger); }
.se-status .info .v::before { background: var(--se-info); }
.se-status .h { color: var(--se-muted); font-size: .8rem; }
/* ---------------------------------------------------------------- misc */
.se-empty { text-align: left; padding: .2rem 0; }
.se-empty h4 { margin: 0 0 .3rem; padding: 0; font-size: 1.02rem; }
.se-empty p { margin: 0; color: var(--se-muted); }
.se-list { margin: .2rem 0 0; padding-left: 1.1rem; }
.se-list li { margin: .2rem 0; }
.se-kv { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); gap: .3rem .9rem;
  font-size: .9rem; margin: 0; }
.se-kv dt { color: var(--se-muted); }
.se-kv dd { margin: 0; overflow-wrap: break-word; }
.se-brand { font-weight: 800; letter-spacing: .12em; font-size: .95rem; }
.se-brand small { display: block; color: var(--se-muted); letter-spacing: .02em; font-weight: 500;
  font-size: .76rem; margin-top: .15rem; }
.se-side-row { display: flex; justify-content: space-between; gap: .5rem; font-size: .8rem;
  padding: .18rem 0; color: var(--se-muted); }
.se-side-row b { color: var(--se-text); font-weight: 600; text-align: right; white-space: nowrap; }
.se-title { font-size: 1.02rem; font-weight: 800; letter-spacing: .09em; text-transform: uppercase;
  margin-bottom: .2rem; }
.se-desc { color: var(--se-muted); font-size: .84rem; margin: 0 0 .5rem; }
.se-match .teams { font-size: 1.04rem; font-weight: 750; line-height: 1.3; }
.se-match .meta { color: var(--se-muted); font-size: .82rem; margin: .1rem 0 .55rem; }
.se-pick { display: flex; justify-content: space-between; align-items: flex-end; gap: .8rem;
  flex-wrap: wrap; margin-top: .35rem; }
.se-pick .what { font-weight: 650; min-width: 0; }
.se-pick .what small { display: block; color: var(--se-muted); font-weight: 500; }
.se-pick .num { text-align: right; font-variant-numeric: tabular-nums; }
.se-pick .num b { font-size: 1.45rem; white-space: nowrap; }
.se-pick .num small { display: block; color: var(--se-muted); white-space: nowrap; }
/* Tasiemka cards carry the violet identity line */
[class*="st-key-coupon-"][class*="-violet"] { border-left: 4px solid var(--se-violet);
  padding-left: .9rem; border-radius: 6px; }
/* rows of Streamlit columns in keyed containers wrap instead of squeezing */
[class*="st-key-row"] div[data-testid="stHorizontalBlock"] { flex-wrap: wrap; row-gap: .75rem; }
[class*="st-key-row"] div[data-testid="stColumn"] { min-width: min(210px, 100%); }
[class*="st-key-row-controls"] div[data-testid="stColumn"] { min-width: min(250px, 100%); }
/* coupon card actions: "DODAJ DO MOJEGO KUPONU" with its icon never cut (TASK 010, 768 px) */
[class*="st-key-row-actions"] div[data-testid="stColumn"] { min-width: min(290px, 100%); }
/* two-panel pages: side by side on wide screens, stacked when a panel would be narrow */
[class*="st-key-split"] div[data-testid="stHorizontalBlock"] { flex-wrap: wrap; row-gap: 1rem; }
[class*="st-key-split"] div[data-testid="stColumn"] { min-width: min(360px, 100%); }
@media (min-width: 641px) {  /* phones keep Streamlit's own stacking of nested columns */
  [class*="st-key-split"] div[data-testid="stColumn"] div[data-testid="stColumn"] { min-width: 0; }
  [class*="st-key-split"] div[data-testid="stColumn"] [class*="st-key-row"]
    div[data-testid="stColumn"] { min-width: min(250px, 100%); }
}
@media (max-width: 640px) {
  .block-container { padding-left: .9rem; padding-right: .9rem; }
  .se-kpi .value { font-size: 1.35rem; }
  .se-mh { grid-template-columns: minmax(0, 1fr); }
  .se-mh .team.away { text-align: left; }
  .se-mh .vs { display: none; }
  .se-row { grid-template-columns: minmax(0, 1fr); }
  .se-row .r { text-align: left; }
  .se-bar-row { grid-template-columns: minmax(5.5rem, 1fr) minmax(3rem, 2fr) 4rem; }
  .se-table { font-size: .84rem; }
  .se-table th, .se-table td { padding: .45rem .35rem; }
  .se-kv { grid-template-columns: minmax(0, 1fr); gap: 0; }
  .se-kv dd { margin-bottom: .35rem; }
}
</style>
"""
).substitute(TOKENS)

LEVELS = {"ok", "info", "warn", "bad", "neutral"}
MISSING = {"Brak danych", "brak danych", "—", ""}
PROFILE_STYLE = {
    "high_hit": ("Wysoka trafność", "green"),
    "standard": ("Standardowy", "blue"),
    "higher_payout": ("Wyższa wypłata", "amber"),
    "tasiemka": ("Tasiemka", "violet"),
    "safe_solo": ("Pewny solo", "teal"),  # TASK 014: optional, off by default
    "my_coupon": ("Mój kupon", "slate"),
    "my_coupon_after": ("Mój kupon (po fakcie)", "slate"),
}


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def num(value) -> str:
    """A number, odds, date or amount that never wraps."""
    return f'<span class="se-num">{esc(value)}</span>'


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


PLACEHOLDER = "wybierz…"


def polish_placeholders() -> None:
    """TASK 019 §4.5: no English "Choose an option" — every selectbox and multiselect without
    its own placeholder gets the Polish one (idempotent)."""
    from streamlit.delta_generator import DeltaGenerator

    for name in ("selectbox", "multiselect"):
        original = getattr(DeltaGenerator, name)
        if getattr(original, "_polish", False):
            continue

        def wrapper(self, *args, _original=original, **kwargs):
            if kwargs.get("placeholder") is None:
                kwargs["placeholder"] = PLACEHOLDER
            return _original(self, *args, **kwargs)

        wrapper._polish = True
        wrapper.__doc__ = original.__doc__
        setattr(DeltaGenerator, name, wrapper)
        setattr(st, name, getattr(st._main, name))


def details(label: str, *, expanded: bool = False, icon: str = ":material/tune:"):
    """Level 3: technical details on demand (closed by default)."""
    return st.expander(label, expanded=expanded, icon=icon)


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


def pill(text: str, level: str = "neutral", *, wrap: bool = False) -> str:
    tone = level if level in LEVELS else "neutral"
    return f'<span class="se-pill {tone}{" wrap" if wrap else ""}">{esc(text)}</span>'


def pills(items: Iterable[tuple[str, str]]) -> None:
    _html("".join(pill(text, level) for text, level in items))


def profile_badge(name: str, label: str | None = None) -> str:
    """Coupon profile identity (Tasiemka is violet and never looks like Standardowy)."""
    default, colour = PROFILE_STYLE.get(name, (name, "slate"))
    return f'<span class="se-profile {colour}">{esc(label or default)}</span>'


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


def kpi(label: str, value: str, hint: str = "", tone: str = "") -> str:
    return card(
        f'<div class="se-kpi"><div class="label">{esc(label)}</div>'
        f'<div class="value" title="{esc(value)}">{esc(value)}</div>'
        + (f'<div class="hint">{esc(hint)}</div>' if hint else "")
        + "</div>",
        tone=tone,
    )


def metric_cards(items: Sequence[tuple]) -> None:
    """KPI grid: (label, value, hint[, tone]). Cards stay at least 220 px wide."""
    _html('<div class="se-grid">' + "".join(kpi(*item) for item in items) + "</div>")


def stat_strip(items: Sequence[tuple[str, str]], *, emphasis: Sequence[int] = ()) -> str:
    """Compact label/value tiles (coupon summary): numbers never wrap."""
    return (
        '<div class="se-stats">'
        + "".join(
            f'<div class="se-stat{" em" if i in emphasis and value not in MISSING else ""}">'
            f'<div class="l">{esc(label)}'
            f'</div><div class="v">{esc(value)}</div></div>'
            for i, (label, value) in enumerate(items)
        )
        + "</div>"
    )


def status_strip(items: Sequence[tuple[str, str, str, str]]) -> None:
    """Top status of a page: (label, value, tone, hint) with a coloured dot."""
    _html(
        '<div class="se-status">'
        + "".join(
            f'<div class="{tone if tone in LEVELS else "neutral"}"><div class="l">{esc(label)}'
            f'</div><div class="v">{esc(value)}</div>'
            + (f'<div class="h">{esc(hint)}</div>' if hint else "")
            + "</div>"
            for label, value, tone, hint in items
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


def market_block(
    title: str,
    rows: Sequence[tuple[str, float | None, str]],
    tag: tuple[str, str] | None = None,
    note: str = "",
) -> str:
    """One market (1X2, Over/Under, BTTS): bars and its validation tag."""
    head = f"<h5><span>{esc(title)}</span>" + (pill(*tag) if tag else "") + "</h5>"
    foot = f'<div class="se-muted" style="font-size:.8rem">{esc(note)}</div>' if note else ""
    return card('<div class="se-market">' + head + probability_bars(rows) + foot + "</div>")


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


def compare_table(
    home: str, away: str, rows: Sequence[tuple[str, str | None, str | None, str, str]]
) -> str:
    """Team comparison: (parameter, home value, away value, better side, note). A missing
    value is shown as "brak danych", never as zero."""
    body = []
    for label, left, right, better, note in rows:
        cells = []
        for side, value in (("home", left), ("away", right)):
            if value is None:
                cells.append('<td class="v none">brak danych</td>')
            else:
                mark = " better" if better == side else ""
                cells.append(f'<td class="v{mark}">{esc(value)}</td>')
        body.append(
            f"<tr><td>{esc(label)}"
            + (f"<small>{esc(note)}</small>" if note else "")
            + "</td>"
            + "".join(cells)
            + "</tr>"
        )
    return (
        '<div class="se-table-wrap"><table class="se-table"><thead><tr><th>Parametr</th>'
        f'<th class="v">{esc(home)}</th><th class="v">{esc(away)}</th></tr></thead><tbody>'
        + "".join(body)
        + "</tbody></table></div>"
    )


def table(columns: Sequence[str], body: Sequence[Sequence], numeric: Sequence[int] = ()) -> str:
    """A friendly HTML table: numeric columns centred and never wrapped, None as "—"."""
    head = "".join(
        f'<th class="v">{esc(col)}</th>' if i in numeric else f"<th>{esc(col)}</th>"
        for i, col in enumerate(columns)
    )
    rows_ = []
    for row in body:
        cells = []
        for i, value in enumerate(row):
            shown = "—" if value is None else value
            cells.append(
                f'<td class="v">{esc(shown)}</td>' if i in numeric else f"<td>{esc(shown)}</td>"
            )
        rows_.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<div class="se-table-wrap"><table class="se-table"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows_)}</tbody></table></div>"
    )


FACTOR = {
    "model": ("w modelu", "ok"),
    "warning": ("tylko ostrzeżenie", "warn"),
    "missing": ("brak danych", "neutral"),
    "info": ("informacja", "info"),
}


def factors(items: Sequence[tuple[str, str]]) -> str:
    """Risk factors: (text, kind) where kind tells whether the model used it."""
    rows = []
    for text, kind in items:
        tag, tone = FACTOR.get(kind, FACTOR["info"])
        rows.append(
            f'<div class="se-factor"><div class="x">{esc(text)}</div>{pill(tag, tone)}</div>'
        )
    return '<div class="se-factors">' + "".join(rows) + "</div>"


def summary(points: Sequence[tuple[str, str]]) -> str:
    """Short labelled sentences ("Za prognozą", "Niepewność", ...)."""
    return (
        '<div class="se-summary">'
        + "".join(
            f'<div><div class="k">{esc(k)}</div><div class="t">{esc(t)}</div></div>'
            for k, t in points
        )
        + "</div>"
    )


def match_header(home: str, away: str, meta: str, badges: Sequence[str] = ()) -> str:
    return card(
        f'<div class="se-mh"><div class="team">{esc(home)}</div><div class="vs">vs</div>'
        f'<div class="team away">{esc(away)}</div><div class="meta">{esc(meta)}</div>'
        f'<div class="badges">{"".join(badges)}</div></div>',
        tone="accent",
    )


def list_row(
    *,
    title: str,
    meta: str = "",
    value: str = "",
    value_hint: str = "",
    badges: Sequence[str] = (),
    tone: str = "",
) -> str:
    """One readable row: title and meta on the left, the main number on the right."""
    right = (
        f'<div class="r"><b>{esc(value)}</b>'
        + (f"<small>{esc(value_hint)}</small>" if value_hint else "")
        + "</div>"
        if value or value_hint
        else ""
    )
    return (
        f'<div class="se-row {tone}"><div><div class="t">{esc(title)}</div>'
        + (f'<div class="m">{esc(meta)}</div>' if meta else "")
        + (f'<div class="b">{"".join(badges)}</div>' if badges else "")
        + "</div>"
        + right
        + "</div>"
    )


def rows(items: Sequence[str]) -> None:
    _html('<div class="se-rows">' + "".join(items) + "</div>")


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


def card_grid(cards: Sequence[str], *, wide: bool = True, kind: str = "") -> None:
    variant = kind or ("wide" if wide else "")
    _html(f'<div class="se-grid{" " + variant if variant else ""}">' + "".join(cards) + "</div>")


def side_rows(rows: Sequence[tuple[str, str]]) -> None:
    _html(
        "".join(
            f'<div class="se-side-row"><span>{esc(k)}</span><b>{esc(v)}</b></div>' for k, v in rows
        )
    )


def legs_html(legs: Sequence[dict]) -> str:
    """Coupon legs as an ordered list: number, match and pick, odds; details below."""
    out = []
    for i, leg in enumerate(legs, 1):
        out.append(
            '<div class="se-leg">'
            f'<div class="no">{i}.</div>'
            f'<div><div class="ev">{esc(leg["event"])}</div>'
            f'<div class="sub">{esc(leg["when"])}</div>'
            f'<div class="pick">{esc(leg["pick"])}</div>'
            + (f'<div class="sub">{esc(leg["detail"])}</div>' if leg.get("detail") else "")
            + "</div>"
            f'<div class="odds">{esc(leg["odds"])}'
            + (f"<small>{esc(leg['chance'])}</small>" if leg.get("chance") else "")
            + "</div>"
            + (f'<div class="note">{esc(leg["note"])}</div>' if leg.get("note") else "")
            + "</div>"
        )
    return '<div class="se-legs">' + "".join(out) + "</div>"


def brand() -> None:
    _html(
        '<div class="se-brand">SPORTS EDGE AI'
        "<small>Analiza sportowa i generator kuponów STS</small></div>"
    )
