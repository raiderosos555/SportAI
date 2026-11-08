"""Retraining engine and champion–challenger comparison (TASK 007 §P5).

A REVIEW is due every ``every_days`` (default 7). It trains a challenger of a scope only
when the minimum conditions hold — enough new results since the champion was trained, no
challenger already waiting, enough days since the last training — and otherwise records
why it skipped. A challenger is trained under protocol v2 (decisions on the selection
window, the final holdout reported only), runs in shadow mode and is promoted only by the
user (AKTYWUJ NOWY MODEL / POZOSTAW POPRZEDNI MODEL). Automatic promotion does not exist.

TASK 011: a waiting challenger no longer blocks the review for ever. At the review a
challenger EXPIRES (new ``model_events`` row "expired", the old rows stay) when a newer
challenger of its scope and family replaced it (it gets no shadow forecasts any more) or when
it is older than ``expiry_days`` without a single settled match in common with the champion.
Expiry never activates anything. The evidence of a challenger is counted in MATCHES
(``prospective_eval.paired_matches``) and the decision panel says plainly whether it waits
for evidence (n/300), whether the evidence is sufficient (the decision is the user's) or
whether it is identical to the champion in the selection window.

TASK 012: challengers are trained under protocol v3 (``rolling``: windows counted back from
the training date), so a retraining can change the hyperparameters and calibration maps; the
conditions above are unchanged. Only when the v3 windows are not feasible (too short a
history) the challenger is trained under protocol v2 as before. Nothing activates a model.

TASK 015 (the evidence clock):
* a challenger of the SAME protocol as the champion that is identical to it (the same
  windows, hyperparameters, calibration maps and selection-window metrics; the holdout of v2
  differs at most by matches played after the champion's training) expires at once — it
  could never produce a different forecast. A new ``model_events`` row, the old rows stay;
* while a scope has no v3 version at all, the review trains the first v3 challenger without
  the "150 new matches" and "7 days" conditions (both count new data within ONE protocol; v3
  differs from the v2 champion anyway). It still needs at least one new match since the
  champion's training and no other challenger waiting. Later v3 challengers follow the usual
  conditions. The v3 gate blocks only the activation — never the training or the shadow mode;
* the panel shows n/300 with an ESTIMATED date from the settlement pace of the last 14 days.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sports_edge import learning
from sports_edge import prospective_eval as pe
from sports_edge.collection import last_run, record_job
from sports_edge.ingestion.types import iso
from sports_edge.storage import Store

WARSAW = ZoneInfo("Europe/Warsaw")
ESTIMATE_DAYS = 14  # TASK 015 §2.4: the settlement pace behind the estimated date of n/300
FIRST_V3_NOTE = (
    "pierwszy challenger v3 w zakresie — bez warunku {n} nowych meczów i {d} dni od treningu "
    "(inny protokół niż champion v2)"
)

EVERY_DAYS = 7
MIN_NEW_RESULTS = 150
MIN_DAYS_BETWEEN_TRAININGS = 7
# TASK 011: the prospective verdict needs settled MATCHES in common (policy file, default
# 300 — prospective_policy.toml [challengers]); MIN_PAIRED is kept for older callers only.
MIN_PAIRED = 100
STATUSES = (
    "Oczekuje na nowe dane",
    "Dane wystarczające",
    "Trening w toku",
    "Walidacja w toku",
    "Challenger gotowy",
    "Odrzucony",
    "Aktywowany",
    "Przywrócono starszą wersję",
)


def due(store: Store, *, now: datetime, every_days: int = EVERY_DAYS) -> bool:
    """A review is due ``every_days`` after the last FINISHED review — one that ended with
    "too little data to train" counts as finished (TASK 008 P0)."""
    last = last_run(store, "retraining_review")
    return last is None or now - last >= timedelta(days=every_days)


def new_matches(
    store: Store, competitions, *, since: str | None, now: datetime, identities=None
) -> int:
    """Unique, verified matches available after ``since`` (one match counts once, however
    many sources confirmed it — TASK 008 P0)."""
    from sports_edge.results_engine import effective_results

    rows = effective_results(
        store,
        sport="football",
        until=now,
        competitions=tuple(competitions),
        identities=identities,
    )
    return sum(1 for r in rows if since is None or r["available_at"] > since)


def has_v3(store: Store, scope: str) -> bool:
    """TASK 015 §2.2: does the scope have any version of protocol v3 (of either family)?"""
    from sports_edge import rolling
    from sports_edge.services import ELO_FAMILY, GOALS_FAMILY, newest_protocol_version

    return any(
        newest_protocol_version(store, scope, family, rolling.PROTOCOL) is not None
        for family in (ELO_FAMILY, GOALS_FAMILY)
    )


def training_conditions(
    store: Store, scope: str, competitions, *, now: datetime, identities=None
) -> dict:
    """{ready, reasons, first_v3, new} for training a new challenger of one scope.

    TASK 015 §2.2: without any v3 version in the scope (``first_v3``) the "150 new matches"
    and "7 days since the last training" conditions do not apply — they compare new data
    within one protocol. At least one new match since the champion's training is still
    needed (protocol v3 refuses the same data), and a waiting challenger still blocks."""
    from sports_edge.services import ELO_FAMILY, scope_version

    reasons, waived = [], []  # ``waived``: the usual conditions the first v3 does not need
    champion = scope_version(store, competitions, ELO_FAMILY)
    since = champion["trained_through"] if champion else None
    new = new_matches(store, competitions, since=since, now=now, identities=identities)
    first = new > 0 and champion is not None and not has_v3(store, scope)
    if new < MIN_NEW_RESULTS:
        text = f"nowych unikalnych meczów od treningu: {new} < {MIN_NEW_RESULTS}"
        (waived if first else reasons).append(text)
    leaving = {vid for _family, vid, _why in expiring(store, scope, now=now)}
    if [v for v in learning.challengers(store, scope, ELO_FAMILY) if v not in leaving]:
        reasons.append("challenger tego zakresu czeka już na decyzję (tryb cienia)")
    last = [e for e in learning.events(store, scope=scope) if e["kind"] == "challenger_ready"]
    if last and now - datetime.fromisoformat(last[-1]["created_at"]) < timedelta(
        days=MIN_DAYS_BETWEEN_TRAININGS
    ):
        text = f"ostatni trening mniej niż {MIN_DAYS_BETWEEN_TRAININGS} dni temu"
        (waived if first else reasons).append(text)
    return {
        "ready": not reasons,
        "reasons": reasons,
        "first_v3": first,
        "new": new,
        "waived": waived,  # TASK 015: the usual conditions hold only when this is empty
    }


def conditions(
    store: Store, scope: str, competitions, *, now: datetime, identities=None
) -> tuple[bool, list[str]]:
    """(ready, reasons) for training a new challenger of one scope."""
    found = training_conditions(store, scope, competitions, now=now, identities=identities)
    return found["ready"], found["reasons"]


def review(
    store: Store,
    paths,
    *,
    now: datetime,
    trigger: str = "manual",
    force: bool = False,
    scopes: tuple[str, ...] | None = None,
    train=None,
    allow_training: bool = True,
) -> dict:
    """One weekly review of every scope. ``train`` (scope → report) defaults to protocol v3
    (TASK 012: rolling windows; v2 only when v3 is not feasible; never an activation).

    TASK 015: identical challengers expire first; the first v3 challenger of a scope is
    trained without the "150 new matches" and "7 days" conditions — under protocol v3 only
    (no v2 fallback: a v2 challenger on the same protocol as the champion keeps the usual
    conditions)."""
    from sports_edge.services import MODEL_SCOPES, run_protocol_v3, train_challengers

    first: dict[str, dict] = {}

    def default_train(scope: str) -> dict:
        if not first.get(scope, {}).get("first_v3"):
            return train_challengers(store, paths, now=now, scope=scope, force=force)
        try:
            return run_protocol_v3(store, paths, now=now, scope=scope, force=force)
        except ValueError as error:
            # v3 not feasible (too short a history): the usual v2 fallback only when the
            # usual conditions hold too — never a v2 challenger without them
            if "oknach protokołu v3" not in str(error) or first[scope]["waived"]:
                raise
            return train_challengers(store, paths, now=now, scope=scope, force=force)

    train = train or default_train
    out = {}
    expired: dict[str, list[dict]] = {}
    for scope in scopes or tuple(MODEL_SCOPES):
        expired[scope] = expire_challengers(store, scope, now=now)
        found = training_conditions(store, scope, MODEL_SCOPES[scope], now=now)
        ready, reasons = found["ready"], found["reasons"]
        first[scope] = found
        if not ready and not force:
            out[scope] = {"status": "skipped", "reasons": reasons}
            learning.record(
                store,
                kind="review",
                actor="system" if trigger != "manual" else "user",
                reason="Przegląd: pominięto trening — " + "; ".join(reasons),
                now=now,
                scope=scope,
                payload={"reasons": reasons, "trigger": trigger},
            )
            continue
        if not allow_training and not force:
            reasons = ["dane wystarczające, ale trening challengera jest wyłączony w automatyzacji"]
            out[scope] = {"status": "skipped", "reasons": reasons}
            learning.record(
                store,
                kind="review",
                actor="system",
                reason="Przegląd: " + reasons[0],
                now=now,
                scope=scope,
                payload={"trigger": trigger},
            )
            continue
        note = (
            FIRST_V3_NOTE.format(n=MIN_NEW_RESULTS, d=MIN_DAYS_BETWEEN_TRAININGS)
            if found["first_v3"]
            else None
        )
        if note:
            learning.record(
                store,
                kind="review",
                actor="system" if trigger != "manual" else "user",
                reason="Przegląd: " + note + f"; nowych meczów od treningu: {found['new']}",
                now=now,
                scope=scope,
                payload={"trigger": trigger, "first_v3": True, "new_matches": found["new"]},
            )
        try:
            report = train(scope) or {}
            out[scope] = {"status": "trained", "summary": report.get("summary")}
        except ValueError as error:
            out[scope] = {"status": "failed", "reasons": [str(error)]}
        if note:
            out[scope]["first_v3"] = True
    for scope, found in expired.items():
        if found:  # TASK 011: challengers that stopped blocking at this review
            out[scope]["expired"] = found
    statuses = {v["status"] for v in out.values()}
    record_job(
        store,
        "retraining_review",
        trigger=trigger,
        started=now,
        finished=now,
        status="ok"
        if "trained" in statuses
        else "skipped"
        if statuses == {"skipped"}
        else "partial",
        reason="; ".join(
            f"{scope}: {', '.join(v.get('reasons') or [])}"
            for scope, v in out.items()
            if v["status"] != "trained"
        )[:500],
        payload=out,
    )
    return out


# ---------------------------------------------------------------- champion vs challenger


def elo_reports(store: Store) -> dict[str, dict]:
    """Elo version id → report of the Dixon–Coles experiment that measured it. The Elo
    report of protocol v2 has no statuses of its own (no ``predictive`` key): the goals
    experiment of its scope measures them (``elo_predictive``) for one Elo version."""
    from sports_edge.models.registry import model_overview
    from sports_edge.services import GOALS_FAMILY

    out: dict[str, dict] = {}
    for row in model_overview(store):  # newest first: the newest measurement wins
        if row["family"] != GOALS_FAMILY:
            continue
        report = json.loads(row["validation_json"])
        if report.get("elo_version_id"):
            out.setdefault(report["elo_version_id"], report)
    return out


def _decision(row: dict | None, measured: dict[str, dict] | None = None) -> dict:
    """What the panel shows about one version: selection window, holdout and the status of
    every market. Elo statuses come from ``measured`` (``elo_reports``) — TASK 011 fix: they
    were read from a ``predictive`` key that an Elo report never has, so none were shown."""
    if row is None:
        return {}
    report = json.loads(row["validation_json"])
    if report.get("predictive") is not None:
        statuses = {k: v.get("status") for k, v in report["predictive"].items()}
    else:
        found = (measured or {}).get(row["version_id"]) or {}
        statuses = {k: v.get("status") for k, v in (found.get("elo_predictive") or {}).items()}
    return {
        "version_id": row["version_id"],
        "version": row["version"],
        "protocol": report.get("protocol", "v1"),
        "decision": report.get("decision") if report.get("protocol") in ("v2", "v3") else None,
        "windows": report.get("split") if report.get("protocol") == "v3" else None,
        "holdout": report.get("holdout"),
        "statuses": statuses,
        "hyperparameters": report.get("hyperparameters"),
        "created_at": row["created_at"],
        "trained_through": row.get("trained_through"),
    }


SAME_FIELDS = ("n", "log_loss", "brier", "ece", "accuracy")


def identical_in_selection(champion: dict, challenger: dict) -> bool:
    """Both versions under protocol v2 with the same selection-window metrics and the same
    hyperparameters: the challenger brings nothing new to compare."""
    old, new = champion.get("decision") or {}, challenger.get("decision") or {}
    if not old or not new:
        return False
    same = all(old.get(k) == new.get(k) for k in SAME_FIELDS)
    return same and champion.get("hyperparameters") == challenger.get("hyperparameters")


def paired(
    store: Store, champion: str, challenger: str, *, seed: int = 7, markets: set | None = None
) -> dict:
    """Both versions on the SAME settled forecasts, counted in MATCHES (TASK 011; see
    ``prospective_eval.paired_matches``). ``markets`` limits the market keys."""
    rows = pe.market_forecasts(store)
    if markets is not None:
        rows = [r for r in rows if r["market_key"] in markets]
    policy = replace(pe.load_policy(), seed=seed)
    found = pe.paired_matches(store, champion, challenger, policy=policy, rows=rows)
    return {**found, "n": found["matches"]}


EVIDENCE_STATES = {
    "none": "brak challengera",
    "identical": "identyczny z championem w oknie selekcji",
    "waiting": "czeka na dowody",
    "sufficient": "dowody wystarczające — decyzja należy do Ciebie",
}


def comparison(
    store: Store, scope: str, *, rows: list[dict] | None = None, now: datetime | None = None
) -> dict:
    """Champion and the waiting challenger of a scope with a plain conclusion and the state
    of the evidence (TASK 011 §5.1). Nothing here activates a model."""
    from sports_edge.services import (
        ELO_FAMILY,
        GOALS_FAMILY,
        MODEL_SCOPES,
        challenger_versions,
        scope_version,
    )

    policy = pe.load_policy()
    measured = elo_reports(store)
    out = {}
    waiting = challenger_versions(store).get(scope, {})
    for key, family in (("elo", ELO_FAMILY), ("goals", GOALS_FAMILY)):
        champion = scope_version(store, MODEL_SCOPES[scope], family)
        challenger = waiting.get(key)
        entry = {
            "family": family,
            "champion": _decision(champion, measured),
            "challenger": _decision(challenger, measured),
        }
        if champion and challenger:
            if rows is None:
                rows = pe.market_forecasts(store)
            entry["prospective"] = pe.paired_matches(
                store, champion["version_id"], challenger["version_id"], policy=policy, rows=rows
            )
            entry["age_days"] = _age_days(
                store, scope, family, challenger["version_id"], now or datetime.now(UTC)
            )
            pro = entry["prospective"]
            entry["estimate"] = evidence_estimate(
                rows,
                scope,
                family,
                matches=pro.get("matches", 0),
                needed=pro.get("needed") or policy.decision_matches,
                now=now or datetime.now(UTC),
            )
        entry["state"], entry["state_label"] = evidence_state(entry, policy)
        entry["conclusion"] = conclusion(entry)
        out[key] = entry
    return out


def evidence_estimate(
    rows: list[dict],
    scope: str,
    family: str,
    *,
    matches: int,
    needed: int,
    now: datetime,
    days: int = ESTIMATE_DAYS,
) -> dict:
    """TASK 015 §2.4: when n/300 common settled matches may be reached — an ESTIMATE.

    The pace is the number of settled matches of the SERVED versions of this scope and
    family (shadow forecasts of a challenger are made for the same matches) that started in
    the last ``days`` days; when the prospective record is younger than that, its own span
    (from the first settled match) is used and named. No settled match in that period: "za
    mało danych" — never a guessed pace."""
    start = now - timedelta(days=days)
    starts: dict[str, datetime] = {}
    first = None
    for r in rows:
        if r["role"] != "active" or r["family"] != family or r["scope"] != scope:
            continue
        if r["market_key"] == "double_chance":  # counted through 1X2, as in paired_matches
            continue
        kickoff = datetime.fromisoformat(r["start_at"])
        first = kickoff if first is None or kickoff < first else first
        if start <= kickoff < now:
            starts[r["match"]] = kickoff
    out = {"matches": matches, "needed": needed, "pace_matches": len(starts), "days": days}
    if matches >= needed:
        return {**out, "state": "reached", "eta": None, "label": f"{matches}/{needed} — osiągnięto"}
    if not starts or first is None:
        return {
            **out,
            "state": "insufficient",
            "eta": None,
            "label": f"za mało danych (brak rozliczonych meczów w ostatnich {days} dniach)",
        }
    span = min(float(days), max(1.0, (now - max(first, start)).total_seconds() / 86400))
    per_day = len(starts) / span
    eta = (now + timedelta(days=(needed - matches) / per_day)).astimezone(WARSAW).date()
    basis = (
        f"tempo z ostatnich {days} dni"
        if span >= days
        else f"tempo z {span:.0f} dni (od pierwszego rozliczenia)"
    )
    per_week = per_day * 7
    return {
        **out,
        "state": "estimated",
        "eta": eta.isoformat(),
        "span_days": round(span, 1),
        "per_week": round(per_week, 1),
        "label": (
            f"szacunek: ok. {eta:%d.%m.%Y} ({basis}: {len(starts)} rozliczonych meczów, "
            f"ok. {per_week:.0f} tygodniowo)"
        ),
    }


def _age_days(store: Store, scope: str, family: str, version_id: str, now: datetime):
    ready = _ready_at(store, scope, family, version_id)
    return None if ready is None else round((now - ready).total_seconds() / 86400, 1)


def evidence_state(entry: dict, policy: pe.EvalPolicy | None = None) -> tuple[str, str]:
    """(code, label): none / identical / waiting (n/300) / sufficient."""
    if not entry.get("challenger"):
        return "none", EVIDENCE_STATES["none"]
    pro = entry.get("prospective") or {}
    n = pro.get("matches", 0)
    needed = pro.get("needed") or (policy or pe.load_policy()).decision_matches
    if identical_in_selection(entry["champion"], entry["challenger"]):
        return "identical", f"{EVIDENCE_STATES['identical']} (wspólne mecze: {n})"
    if not pro.get("enough"):
        return "waiting", f"{EVIDENCE_STATES['waiting']} ({n}/{needed})"
    return "sufficient", EVIDENCE_STATES["sufficient"]


def verdict(pro: dict) -> str | None:
    if not pro.get("matches"):
        return None
    if pro["ci95"][1] < 0:
        return "challenger lepszy"
    if pro["ci95"][0] > 0:
        return "challenger gorszy"
    return "brak istotnej różnicy"


def conclusion(entry: dict) -> str:
    champion, challenger = entry["champion"], entry["challenger"]
    if not challenger:
        return "Brak challengera — model aktywny bez zmian."
    parts = []
    if champion.get("protocol") not in ("v2", "v3"):
        parts.append(
            "Aktywna wersja ma statusy z protokołu v1 (holdout użyty do decyzji) — są zawieszone."
        )
    new = challenger.get("decision") or {}
    old = champion.get("decision") or {}
    if new and old and challenger.get("protocol") == "v3" and champion.get("protocol") != "v3":
        windows = challenger.get("windows") or {}
        parts.append(
            "Challenger z protokołu v3 (okna ruchome: selekcja od "
            f"{windows.get('selection_from', '—')} do daty treningu). Jego okno selekcji to inny "
            f"okres niż u aktywnej wersji v2, więc log loss {new['log_loss']} i {old['log_loss']} "
            "nie są porównywalne — rozstrzyga porównanie w trybie cienia na tych samych meczach."
        )
    elif new and old:
        delta = new["log_loss"] - old["log_loss"]
        parts.append(
            f"W oknie selekcji challenger ma log loss {new['log_loss']} wobec {old['log_loss']} "
            + ("(lepiej)." if delta < 0 else "(gorzej)." if delta > 0 else "(tak samo).")
        )
        if identical_in_selection(champion, challenger):
            parts.append(
                "Te same hiperparametry i miary okna selekcji co aktywna wersja — w oknie "
                "selekcji challenger jest identyczny z championem."
            )
    elif new:
        parts.append(
            f"Challenger: okno selekcji n={new.get('n')}, log loss {new.get('log_loss')}, "
            f"ECE {new.get('ece')} — bez czystego porównania z wersją v1."
        )
    validated = [
        k for k, v in (challenger.get("statuses") or {}).items() if v == "validated_predictive"
    ]
    if validated:
        parts.append("Rynki zwalidowane w oknie selekcji: " + ", ".join(validated) + ".")
    pro = entry.get("prospective") or {}
    if pro.get("matches"):
        short = f" — za mało wspólnych meczów na decyzję ({pro['matches']}/{pro['needed']})"
        parts.append(
            f"Prospektywnie (te same mecze, n={pro['matches']}): {verdict(pro)}"
            + ("" if pro["enough"] else short)
            + "."
        )
    else:
        parts.append("Prospektywnie: brak jeszcze wspólnych rozliczonych meczów (tryb cienia).")
    return " ".join(parts)


def _ready_at(store: Store, scope: str, family: str, version_id: str) -> datetime | None:
    found = [
        e["created_at"]
        for e in learning.events(store, scope=scope, family=family)
        if e["kind"] == "challenger_ready" and e["model_version_id"] == version_id
    ]
    return datetime.fromisoformat(found[0]) if found else None


# TASK 015 §2.1: the parts of a version report that decide its forecasts. Two versions of
# the same protocol with all of them equal give the same forecast for every match (team
# strengths are computed from the same results at the time of the forecast).
MODEL_PARTS = (
    "protocol",
    "split",
    "hyperparameters",
    "decision",
    "calibration",
    "calibrators",
    "team_total_calibrators",
    "half_life_days",
    "window_days",
    "refit_days",
    "markets_served",
    "predictive",
    "elo_predictive",
)
IDENTICAL_REASON = (
    "identyczny z championem (ten sam protokół {protocol}: te same okna, hiperparametry, "
    "kalibratory i miary okna selekcji{holdout}) — nie może dać innej prognozy"
)


def _holdout_same(old: dict, new: dict) -> tuple[bool, str]:
    """The holdout of both versions: the same numbers, or (protocol v2: the holdout runs up
    to the training date and is only reported) the same window extended by matches played
    after the champion's training — never fewer matches."""
    a, b = old.get("holdout"), new.get("holdout")
    if a == b:
        return True, "" if not a else ", ten sam holdout"
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False, ""
    same_start = (old.get("split") or {}).get("holdout_from") == (new.get("split") or {}).get(
        "holdout_from"
    )
    if same_start and (b.get("n") or 0) >= (a.get("n") or 0) > 0:
        return True, (
            f", holdout od {(old.get('split') or {}).get('holdout_from')} poszerzony tylko o "
            f"nowsze mecze: n {a.get('n')} → {b.get('n')}"
        )
    return False, ""


def _shadow_differs(store: Store, champion: str, challenger: str) -> bool:
    """A guard: stored forecasts of both versions for the same match, market and data state
    (the same ``data_snapshot``) that differ — then the versions are NOT identical."""
    rows = store.rows(
        """SELECT a.probabilities_json pa, b.probabilities_json pb, a.inputs_json ia,
        b.inputs_json ib FROM predictions a JOIN predictions b ON b.event_id=a.event_id
        AND b.market_key=a.market_key AND b.period=a.period AND b.line IS a.line
        WHERE a.model_version_id=? AND b.model_version_id=? AND a.kind='prospective'
        AND b.kind='prospective' LIMIT 5000""",
        (champion, challenger),
    )
    for r in rows:
        if json.loads(r["ia"] or "{}").get("data_snapshot") != json.loads(r["ib"] or "{}").get(
            "data_snapshot"
        ):
            continue
        pa, pb = json.loads(r["pa"]), json.loads(r["pb"])
        if set(pa) != set(pb) or any(abs(float(pa[k]) - float(pb[k])) > 1e-9 for k in pa):
            return True
    return False


def identical_to_champion(store: Store, champion: dict | None, challenger: dict) -> str | None:
    """TASK 015 §2.1: why the challenger is identical to the champion (None: it is not).
    Only versions of the SAME protocol; v1 versions (holdout used) are never compared."""
    if champion is None or champion["version_id"] == challenger["version_id"]:
        return None
    old = json.loads(champion["validation_json"] or "{}")
    new = json.loads(challenger["validation_json"] or "{}")
    protocol = old.get("protocol")
    if protocol not in ("v2", "v3") or new.get("protocol") != protocol:
        return None
    if not old.get("decision") or any(old.get(k) != new.get(k) for k in MODEL_PARTS):
        return None
    same, holdout = _holdout_same(old, new)
    if not same or _shadow_differs(store, champion["version_id"], challenger["version_id"]):
        return None
    return IDENTICAL_REASON.format(protocol=protocol, holdout=holdout)


def expiring(
    store: Store, scope: str, *, now: datetime, rows: list[dict] | None = None
) -> list[tuple[str, str, str]]:
    """(family, version id, reason) of the waiting challengers that stop blocking the review:
    replaced by a newer challenger of the same scope and family, older than ``expiry_days``
    without any settled match in common with the champion, or (TASK 015) identical to the
    champion — at once, without waiting."""
    from sports_edge.models.registry import model_overview
    from sports_edge.services import ELO_FAMILY, GOALS_FAMILY, MODEL_SCOPES, scope_version

    policy = pe.load_policy()
    versions = None
    out = []
    for family in (ELO_FAMILY, GOALS_FAMILY):
        waiting = learning.challengers(store, scope, family)
        for i, version_id in enumerate(waiting):
            if i < len(waiting) - 1:
                why = (
                    "zastąpiony nowszym challengerem tego zakresu — nie dostaje już prognoz cienia"
                )
                out.append((family, version_id, why))
                continue
            champion = scope_version(store, MODEL_SCOPES[scope], family)
            if champion is None:
                continue
            ready = _ready_at(store, scope, family, version_id)
            if ready is not None and now - ready >= timedelta(days=policy.expiry_days):
                if rows is None:
                    rows = pe.market_forecasts(store)
                found = pe.paired_matches(
                    store, champion["version_id"], version_id, policy=policy, rows=rows
                )
                if found["matches"] == 0:
                    why = (
                        "brak wspólnych rozliczonych meczów z aktywną wersją po "
                        f"{policy.expiry_days} dniach"
                    )
                    out.append((family, version_id, why))
                    continue
            if versions is None:
                versions = {r["version_id"]: r for r in model_overview(store)}
            row = versions.get(version_id)
            why = identical_to_champion(store, champion, row) if row else None
            if why:
                out.append((family, version_id, why))
    return out


def expire_challengers(store: Store, scope: str, *, now: datetime) -> list[dict]:
    """Record the expiry of every challenger returned by ``expiring`` (idempotent). The old
    rows stay as they were; an expired challenger is never activated."""
    out = []
    for family, version_id, why in expiring(store, scope, now=now):
        identical = why.startswith("identyczny")
        prefix = "Wygasły: " if identical else "Wygasły (brak dowodów): "
        learning.record(
            store,
            kind="expired",
            actor="system",
            reason=prefix + why,
            now=now,
            version_id=version_id,
            scope=scope,
            family=family,
            payload={"reason": why, "identical": identical},
            key=["expired", scope, family, version_id],
        )
        out.append(
            {"family": family, "version_id": version_id, "reason": why, "identical": identical}
        )
    return out


def scope_status(store: Store, scope: str, *, now: datetime, running: str | None = None) -> str:
    """One of the eight statuses of the panel."""
    from sports_edge.services import ELO_FAMILY, MODEL_SCOPES

    if running in ("Trening w toku", "Walidacja w toku"):
        return running
    if learning.challengers(store, scope, ELO_FAMILY):
        return "Challenger gotowy"
    found = learning.events(store, scope=scope, family=ELO_FAMILY)
    last = next(
        (
            e
            for e in reversed(found)
            if e["kind"] in {"activated", "rejected", "kept_previous", "rolled_back"}
        ),
        None,
    )
    if last is not None and last["kind"] == "rolled_back":
        return "Przywrócono starszą wersję"
    if last is not None and last["kind"] in {"rejected", "kept_previous"}:
        return "Odrzucony"
    ready, _reasons = conditions(store, scope, MODEL_SCOPES[scope], now=now)
    if ready:
        return "Dane wystarczające"
    if last is not None and last["kind"] == "activated" and last["actor"] == "user":
        return "Aktywowany"
    return "Oczekuje na nowe dane"


def timeline(store: Store, scope: str) -> dict[str, str | None]:
    """Separate moments (TASK 008 P0): last review, training, validation and activation.
    Under protocol v2 a challenger is validated as part of its training run."""
    found = learning.events(store, scope=scope)
    trained = [e["created_at"] for e in found if e["kind"] == "challenger_ready"]
    activated = [e["created_at"] for e in found if e["kind"] in learning.ROLE_KINDS]
    review = last_run(store, "retraining_review")
    return {
        "review": review.isoformat() if review else None,
        "training": trained[-1] if trained else None,
        "validation": trained[-1] if trained else None,
        "activation": activated[-1] if activated else None,
    }


def history(store: Store) -> list[dict]:
    """Learning history table (newest first)."""
    rows = learning.events(store)
    return [
        {
            "Kiedy": r["created_at"],
            "Zakres": r["scope"] or "—",
            "Model": r["family"] or "—",
            "Wersja": (r["model_version_id"] or "")[:12],
            "Zdarzenie": r["label"],
            "Kto": {"user": "użytkownik", "system": "system", "migration": "migracja"}[r["actor"]],
            "Powód": r["reason"],
        }
        for r in reversed(rows)
    ]


def stamp(now: datetime) -> str:
    return iso(now)
