"""One coupon, one history (TASK 010): stable coupon identity, lineage and saving rules.

* IDENTITY of a coupon = who made it (the builder or the user) + profile + analysis day +
  policy version + the sorted SELECTION ids. ``selection_id`` is stable between STS imports
  (``digest(["selection", market_key, key])``); ``odds_id`` belongs to one STS document and
  never enters the identity. Prices and the stake are attributes of a version.
* SLOT = builder + profile + analysis day: at most one OFFICIAL coupon of the day per profile.
* LINEAGE (``coupon_lineage``, append-only) records for every coupon its relation to the
  official one: ``official``, ``duplicate``, ``price_update``, ``revision`` or ``user``.

Saving a builder proposal (rule ``official_rule = "first"`` in probability_policy.toml):

| situation                                   | what is stored                              |
|---------------------------------------------|---------------------------------------------|
| same identity, same prices                  | nothing new — the existing version returns  |
| same identity, other prices (a new STS PDF) | a price update linked to the official coupon|
| other legs, same profile and day            | a revision linked to the official coupon    |
| the first save of a profile in a day        | the official coupon of the day              |

The official coupon is never replaced — least of all after one of its matches started, which
would be choosing legs after seeing results (a lost leg removed afterwards). Settlement covers
every coupon (complete history); statistics, reassessment and alerts take only ``official``
and ``user`` coupons. A coupon without a lineage row (created by older code) still counts.
"""

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sports_edge.analysis.coupon_builder import BuilderReport, Proposal
from sports_edge.analysis.edge_engine import Candidate
from sports_edge.bookmakers import load_profiles
from sports_edge.ingestion.types import digest, iso
from sports_edge.insights import pick_label
from sports_edge.paper import create_coupon, latest_settlement
from sports_edge.probability_lab import load_probability_policy
from sports_edge.settings import AppPaths
from sports_edge.storage import Store

WARSAW = ZoneInfo("Europe/Warsaw")
RELATIONS = ("official", "duplicate", "price_update", "revision", "user")
COUNTED = ("official", "user")
VERSIONS = ("price_update", "revision")
NOT_COUNTED = ("duplicate", "price_update", "revision")
RELATION_LABELS = {
    "official": "Oficjalny kupon dnia",
    "user": "Kupon użytkownika",
    "duplicate": "Duplikat",
    "price_update": "Aktualizacja kursów",
    "revision": "Rewizja",
}
BUILDER = "kreator"
USER_PROFILES = ("my_coupon", "my_coupon_after")
DUPLICATE_MESSAGE = "Ten kupon jest już zapisany"


def counted_sql(alias: str = "c") -> str:
    """SQL condition: the coupon counts in statistics, reassessment and alerts."""
    marks = ",".join(f"'{r}'" for r in NOT_COUNTED)
    return (
        f"NOT EXISTS (SELECT 1 FROM coupon_lineage g WHERE g.coupon_id={alias}.id "
        f"AND g.relation IN ({marks}))"
    )


def local_day(moment: str | datetime) -> str:
    when = datetime.fromisoformat(moment) if isinstance(moment, str) else moment
    return when.astimezone(WARSAW).date().isoformat()


def coupon_identity(
    *, source: str, profile: str | None, day: str, policy_version: str | None, selection_ids
) -> str:
    return digest(
        ["coupon-identity", source, profile or "", day, policy_version or "", sorted(selection_ids)]
    )


def slot_key(*, source: str, profile: str | None, day: str) -> str:
    return digest(["coupon-slot", source, profile or "", day])


def manual_key(*, mode: str, selection_ids, stake, day: str, copy: int = 1) -> str:
    """Key of a coupon saved by the user: mode + selections + stake + day — never the clock,
    so a double click or a Streamlit rerun returns the same coupon."""
    amount = str(Decimal(str(stake)).quantize(Decimal("0.01")))
    parts = ["coupon-manual", mode, sorted(selection_ids), amount, day]
    return digest(parts if copy == 1 else [*parts, copy])


def _signature(legs) -> tuple:
    """Prices of a version: (selection id, price) in a stable order."""
    return tuple(sorted((s, str(Decimal(str(p)).quantize(Decimal("0.001")))) for s, p in legs))


def _odds_legs(store: Store, odds_ids: list[str]) -> list[tuple[str, str]]:
    if not odds_ids:
        return []
    marks = ",".join("?" for _ in odds_ids)
    rows = {
        r["id"]: r
        for r in store.rows(
            f"SELECT id, selection_id, price FROM odds WHERE id IN ({marks})", tuple(odds_ids)
        )
    }
    return [(rows[o]["selection_id"], rows[o]["price"]) for o in odds_ids if o in rows]


def _coupon_legs(store: Store, coupon_id: str) -> list[tuple[str, str]]:
    return [
        (r["selection_id"], r["price"])
        for r in store.rows(
            "SELECT selection_id, price FROM coupon_legs WHERE coupon_id=?", (coupon_id,)
        )
    ]


def lineage_of(store: Store, coupon_id: str) -> dict | None:
    rows = store.rows("SELECT * FROM coupon_lineage WHERE coupon_id=?", (coupon_id,))
    return rows[0] if rows else None


def _record(store: Store, *, coupon_id, official_id, relation, identity, slot, now, reason):
    if relation not in RELATIONS:
        raise ValueError(f"Nieznana relacja kuponu: {relation}")
    if lineage_of(store, coupon_id) is not None:
        return
    store.insert(
        "coupon_lineage",
        id=digest(["lineage", coupon_id]),
        coupon_id=coupon_id,
        official_coupon_id=official_id,
        relation=relation,
        identity_key=identity,
        slot_key=slot,
        recorded_at=iso(now),
        reason=reason,
    )


@dataclass
class SaveResult:
    coupon_id: str
    relation: str  # official | duplicate | price_update | revision | user
    official_id: str
    official_created_at: str = ""
    message: str = ""
    changes: dict = field(default_factory=dict)
    # a duplicate: which stored version it repeats (official, price_update or revision)
    version_relation: str = ""
    version_created_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def changes_between(store: Store, official_id: str, coupon_id: str) -> dict:
    """What changed against the official coupon: legs added and removed, price changes,
    total odds and payout."""
    if official_id == coupon_id:
        return {}
    labels = {
        r["selection_id"]: r
        for r in store.rows(
            """SELECT l.coupon_id, l.selection_id, l.price, e.home, e.away, m.market_key, m.line,
            s.key selection FROM coupon_legs l JOIN selections s ON s.id=l.selection_id
            JOIN markets m ON m.id=s.market_id JOIN events e ON e.id=m.event_id
            WHERE l.coupon_id IN (?, ?)""",
            (official_id, coupon_id),
        )
    }
    before = dict(_coupon_legs(store, official_id))
    after = dict(_coupon_legs(store, coupon_id))

    def name(selection_id: str) -> str:
        row = labels.get(selection_id)
        if row is None:
            return selection_id[:8]
        event = f"{row['home']} - {row['away']}"
        return f"{event}: {pick_label(row['market_key'], row['line'], row['selection'], event)}"

    def payout(coupon: dict) -> str | None:
        """The payout shown on the coupon card (after the STS stake coefficient and tax)."""
        stored = json.loads(coupon["proposal_json"] or "{}").get("payout")
        return (
            str(Decimal(stored).quantize(Decimal("0.01"))) if stored not in (None, "None") else None
        )

    totals = {
        r["id"]: r
        for r in store.rows(
            "SELECT id, total_odds, stake, proposal_json FROM coupons WHERE id IN (?, ?)",
            (official_id, coupon_id),
        )
    }
    old, new = totals.get(official_id), totals.get(coupon_id)
    return {
        "added": [name(s) for s in after if s not in before],
        "removed": [name(s) for s in before if s not in after],
        "prices": [
            {"selection": name(s), "before": before[s], "after": after[s]}
            for s in after
            if s in before and Decimal(before[s]) != Decimal(after[s])
        ],
        "total_odds": [old["total_odds"], new["total_odds"]] if old and new else None,
        "payout": [payout(old), payout(new)] if old and new else None,
    }


# ---------------------------------------------------------------- the builder


def prediction_ids_of(legs) -> dict[str, str]:
    """TASK 013 §4: odds id → id of the forecast the leg's chance comes from (candidates or
    payload legs); a leg without a forecast is left out (its ``prediction_id`` stays empty)."""
    out = {}
    for leg in legs:
        if leg is None:
            continue
        odds_id = leg["odds_id"] if isinstance(leg, dict) else leg.odds_id
        found = (
            leg.get("prediction_id")
            if isinstance(leg, dict)
            else getattr(leg, "prediction_id", None)
        )
        if found:
            out[odds_id] = found
    return out


def save_builder(
    store: Store,
    *,
    odds_ids: list[str],
    stake: Decimal,
    profile_name: str,
    payload: dict,
    policy_version: str,
    day: str,
    analysis_id: str | None,
    now: datetime,
    sts,
    note: str,
    prediction_ids: dict[str, str] | None = None,
) -> SaveResult:
    """One builder proposal under the rules of the module docstring. ``prediction_ids``
    (odds id → forecast id, TASK 013) are stored with the legs; they never enter the coupon
    identity (another forecast of the same selection is the same coupon)."""
    legs = _odds_legs(store, odds_ids)
    selections = [s for s, _ in legs]
    signature = _signature(legs)
    identity = coupon_identity(
        source=BUILDER,
        profile=profile_name,
        day=day,
        policy_version=policy_version,
        selection_ids=selections,
    )
    slot = slot_key(source=BUILDER, profile=profile_name, day=day)
    official = store.rows(
        "SELECT * FROM coupon_lineage WHERE slot_key=? AND relation='official' LIMIT 1", (slot,)
    )
    key = digest(["coupon-builder", identity, signature])

    def create(relation: str, official_id: str | None, reason: str) -> str:
        coupon_id = create_coupon(
            store,
            odds_ids=odds_ids,
            stake=stake,
            mode="paper_system",
            profile=sts,
            now=now,
            analysis_id=analysis_id,
            prediction_ids=prediction_ids,
            note=note,
            proposal=payload,
            key=key,
        )
        _record(
            store,
            coupon_id=coupon_id,
            official_id=official_id or coupon_id,
            relation=relation,
            identity=identity,
            slot=slot,
            now=now,
            reason=reason,
        )
        return coupon_id

    if not official:
        coupon_id = create("official", None, "pierwszy zapis profilu w dniu (reguła „first”)")
        created = store.get("coupons", coupon_id)["created_at"]
        return SaveResult(coupon_id, "official", coupon_id, created, "Oficjalny kupon dnia")
    official = official[0]
    official_id = official["coupon_id"]
    official_created = store.get("coupons", official_id)["created_at"]
    same = store.rows(
        """SELECT g.coupon_id, g.relation, c.created_at FROM coupon_lineage g
        JOIN coupons c ON c.id=g.coupon_id
        WHERE g.slot_key=? AND g.identity_key=? AND g.relation IN ('official','price_update',
        'revision') ORDER BY c.created_at DESC, c.rowid DESC""",
        (slot, identity),
    )
    if same:
        # any stored version with these prices (also an older one: prices may come back)
        match = next(
            (r for r in same if _signature(_coupon_legs(store, r["coupon_id"])) == signature),
            None,
        )
        if match is not None:
            since = local_time(official_created)
            if match["relation"] == "official":
                message = f"Bez zmian — kupon dnia z {since} jest aktualny"
            else:
                version = "rewizja" if match["relation"] == "revision" else "aktualizacja kursów"
                message = (
                    f"Bez zmian — {version} z {local_time(match['created_at'])} jest aktualna; "
                    f"kupon dnia z {since} pozostaje w statystykach"
                )
            return SaveResult(
                match["coupon_id"],
                "duplicate",
                official_id,
                official_created,
                message,
                changes_between(store, official_id, match["coupon_id"]),
                version_relation=match["relation"],
                version_created_at=match["created_at"],
            )
        coupon_id = create("price_update", official_id, "te same nogi, nowe kursy (nowy PDF STS)")
        return SaveResult(
            coupon_id,
            "price_update",
            official_id,
            official_created,
            "Kursy zaktualizowane",
            changes_between(store, official_id, coupon_id),
        )
    coupon_id = create("revision", official_id, "inne nogi tego samego profilu i dnia")
    return SaveResult(
        coupon_id,
        "revision",
        official_id,
        official_created,
        f"Rewizja z {local_time(store.get('coupons', coupon_id)['created_at'])}",
        changes_between(store, official_id, coupon_id),
    )


def local_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(WARSAW).strftime("%H:%M")


def proposal_payload(proposal: Proposal, *, policy_version: str) -> dict:
    """What the coupon view shows and what is stored with a saved proposal (JSON-safe)."""
    return {
        "profile": proposal.profile,
        "label": proposal.label,
        "policy_version": policy_version,
        "level": proposal.level,
        "probability": {
            "calibrated": proposal.p_calibrated,
            "conservative": proposal.p_conservative,
            "basis": proposal.joint_basis,
        },
        "total_odds": str(proposal.total_odds),
        "stake": str(proposal.stake),
        "payout": str(proposal.payout),
        "net_profit": str(proposal.net_profit),
        "ev_net": str(proposal.ev_net),
        "legs": [_leg(leg) for leg in proposal.legs],
        "reasons": list(proposal.reasons),
        "warnings": list(proposal.warnings),
    }


def _leg(leg: Candidate) -> dict:
    return {
        "event": leg.event,
        "event_id": leg.event_id,
        "league": leg.league,
        "start_at": leg.start_at,
        "market": leg.market_label,
        "market_key": leg.market_key,
        "line": leg.line,
        "selection": leg.selection,
        "selection_id": leg.selection_id,
        "offer_code": leg.offer_code,
        "odds": str(leg.odds),
        "odds_id": leg.odds_id,
        "p_model": leg.p_model,
        "p_conservative": leg.p_low,
        "p_market": leg.p_market,
        "predictive_status": leg.predictive_status,
        "calibration": leg.calibration,
        "model": leg.model,
        "model_version_id": leg.model_version_id,
        "document_at": leg.document_at,
        "odds_age_hours": leg.odds_age_hours,
        "freshness": leg.freshness,
        "prediction_id": getattr(leg, "prediction_id", None),  # TASK 013: the forecast of the leg
    }


def save_proposals(
    store: Store, paths: AppPaths, built: BuilderReport, *, analysis_id: str, now, day: str
) -> dict[str, dict]:
    """Every proposal under the lineage rules: {profile: SaveResult as dict | {"error"}}."""
    rules = load_probability_policy(paths.config / "probability_policy.toml")
    sts = load_profiles(paths.config)["sts"]
    out: dict[str, dict] = {}
    for name, proposal in built.proposals.items():
        if proposal is None:
            continue
        try:
            out[name] = save_builder(
                store,
                odds_ids=[leg.odds_id for leg in proposal.legs],
                stake=proposal.stake,
                profile_name=name,
                payload=proposal_payload(proposal, policy_version=rules.version),
                policy_version=rules.version,
                day=day,
                analysis_id=analysis_id,
                now=now,
                sts=sts,
                note=f"Kreator kuponów — {proposal.label}",
                prediction_ids=prediction_ids_of(proposal.legs),
            ).as_dict()
        except ValueError as error:
            out[name] = {"error": str(error)}
    return out


def save_coupon_proposals(
    store: Store, paths: AppPaths, built: BuilderReport, *, analysis_id: str, now, day: str
) -> dict[str, str]:
    """Every proposal of the builder (TASK 010: one official coupon per profile and day)."""
    saved = save_proposals(store, paths, built, analysis_id=analysis_id, now=now, day=day)
    return {
        name: result["coupon_id"] if "coupon_id" in result else f"nie zapisano: {result['error']}"
        for name, result in saved.items()
    }


def saved_proposals(store: Store, day: str) -> dict[str, dict]:
    """The OFFICIAL coupon of each profile for an analysis day (with its settlement and the
    newest later version, if any). Days saved before TASK 010 without lineage fall back to
    the first saved coupon of the profile."""
    out: dict[str, dict] = {}
    for row in store.rows(
        f"""SELECT c.*, g.relation, g.official_coupon_id FROM coupons c
        JOIN analyses a ON a.id=c.analysis_id
        LEFT JOIN coupon_lineage g ON g.coupon_id=c.id
        WHERE a.analysis_date=? AND c.proposal_json IS NOT NULL AND {counted_sql()}
        ORDER BY c.created_at, c.rowid""",
        (day,),
    ):
        if row["profile"] in out:
            continue
        settlement = latest_settlement(store, row["id"])
        newest = store.rows(
            """SELECT g.coupon_id, g.relation, c.created_at FROM coupon_lineage g
            JOIN coupons c ON c.id=g.coupon_id WHERE g.official_coupon_id=? AND g.relation IN
            ('price_update','revision') ORDER BY c.created_at DESC, c.rowid DESC LIMIT 1""",
            (row["id"],),
        )
        out[row["profile"]] = {
            **json.loads(row["proposal_json"]),
            "coupon_id": row["id"],
            "created_at": row["created_at"],
            "relation": row["relation"] or "official",
            "outcome": settlement["outcome"] if settlement else None,
            "settled_net_profit": settlement["net_profit"] if settlement else None,
            "newest_version": (
                {
                    **newest[0],
                    "changes": changes_between(store, row["id"], newest[0]["coupon_id"]),
                }
                if newest
                else None
            ),
        }
    return out


# ---------------------------------------------------------------- the user


def save_manual(
    store: Store,
    *,
    odds_ids: list[str],
    stake: Decimal,
    mode: str,
    sts,
    now: datetime,
    note: str = "",
    proposal: dict | None = None,
    analysis_id: str | None = None,
    late_entry: bool = False,
    confirm_copy: bool = False,
    prediction_ids: dict[str, str] | None = None,
) -> SaveResult:
    """A coupon saved by the user (Mój kupon, Historia, Diagnostyka). The same coupon saved
    again (a double click, a rerun) returns the existing one. A second IDENTICAL real coupon
    (placed twice in STS) needs ``confirm_copy`` — a deliberate decision, never a click.
    ``prediction_ids`` (odds id → forecast id, TASK 013) are stored with the legs."""
    legs = _odds_legs(store, odds_ids)
    selections = [s for s, _ in legs]
    day = local_day(now)
    key = manual_key(mode=mode, selection_ids=selections, stake=stake, day=day)
    existing = store.get("coupons", key)
    if existing is not None and not (confirm_copy and mode == "real_manual"):
        return SaveResult(key, "duplicate", key, existing["created_at"], DUPLICATE_MESSAGE)
    copy = 1
    while existing is not None:
        copy += 1
        key = manual_key(mode=mode, selection_ids=selections, stake=stake, day=day, copy=copy)
        existing = store.get("coupons", key)
    coupon_id = create_coupon(
        store,
        odds_ids=odds_ids,
        stake=stake,
        mode=mode,
        profile=sts,
        now=now,
        analysis_id=analysis_id,
        prediction_ids=prediction_ids,
        note=note,
        proposal=proposal,
        key=key,
        late_entry=late_entry,
    )
    _record(
        store,
        coupon_id=coupon_id,
        official_id=coupon_id,
        relation="user",
        identity=key,
        slot=key,
        now=now,
        reason="zapis użytkownika" + (f" (świadomie {copy}. identyczny)" if copy > 1 else ""),
    )
    return SaveResult(coupon_id, "user", coupon_id, iso(now), "Zapisano")


def my_coupon_payload(rows: list[Candidate], summary: dict, *, policy_version: str) -> dict:
    """What is stored with a coupon built by the user (MÓJ KUPON), JSON-safe."""
    return {
        "profile": "my_coupon",
        "label": "MÓJ KUPON",
        "source": "user",
        "policy_version": policy_version,
        "level": "validated_predictive" if summary["validated"] else "experimental",
        "probability": {
            "calibrated": summary["probability"],
            "conservative": summary["probability_low"],
            "basis": summary["probability_note"],
        },
        "total_odds": str(summary["total_odds"]),
        "stake": str(summary["stake"]),
        "payout": str(summary.get("payout")),
        "net_profit": str(summary.get("net_profit")),
        "legs": [_leg(leg) for leg in rows],
    }


def save_my_coupon_result(
    store: Store,
    paths: AppPaths,
    cart: list[dict],
    rows: list[Candidate | None],
    *,
    stake: Decimal,
    now: datetime,
    real: bool = False,
    late: bool = False,
    confirm_copy: bool = False,
) -> SaveResult:
    """Save MÓJ KUPON as a virtual coupon (or a manually recorded real one). Refused while a
    blocking problem remains (stale price, started event, contradiction, one match twice).

    ``late``: a coupon already placed in STS, recorded after its matches started. It is a
    real entry with prices observed before each start, kept apart from prospective coupons
    (profile "my_coupon_after") because choosing legs after seeing results is hindsight."""
    from sports_edge import my_coupon as mine

    sts = load_profiles(paths.config)["sts"]
    first = min((row.start_at for row in rows if row is not None and row.start_at), default=None)
    on_date = datetime.fromisoformat(first).date() if late and first else now.date()
    summary = mine.summary(cart, rows, stake=stake, cost=sts.cost, on_date=on_date, late=late)
    blocking = [i.message for i in summary["issues"] if i.severity == "block"]
    if blocking:
        raise ValueError("Kupon nie może być zapisany: " + "; ".join(blocking))
    present = [row for row in rows if row is not None]
    rules = load_probability_policy(paths.config / "probability_policy.toml")
    payload = my_coupon_payload(present, summary, policy_version=rules.version)
    if late:
        payload.update(
            profile="my_coupon_after",
            label="MÓJ KUPON — wpis po fakcie",
            entered_after_start=True,
        )
    return save_manual(
        store,
        odds_ids=[row.odds_id for row in present],
        stake=stake,
        mode="real_manual" if real or late else "paper_manual",
        sts=sts,
        now=now,
        note="Mój kupon"
        + (
            " — postawiony w STS, wpis po rozpoczęciu meczu"
            if late
            else " — postawiony w STS (wpis ręczny)"
            if real
            else ""
        ),
        proposal=payload,
        late_entry=late,
        confirm_copy=confirm_copy,
        prediction_ids=prediction_ids_of(present),
    )


def save_my_coupon(store, paths, cart, rows, *, stake, now, real=False, late=False) -> str:
    """The coupon id of :func:`save_my_coupon_result` (the signature of TASK 007)."""
    return save_my_coupon_result(
        store, paths, cart, rows, stake=stake, now=now, real=real, late=late
    ).coupon_id


# ---------------------------------------------------------------- backfill


def _is_builder(coupon: dict) -> bool:
    return (
        coupon["mode"] == "paper_system"
        and coupon["proposal_json"] is not None
        and coupon["profile"] not in USER_PROFILES
    )


def backfill_lineage(store: Store, *, now: datetime) -> dict[str, int]:
    """Lineage of every coupon that has none yet (idempotent; nothing is ever updated).

    Builder coupons are grouped by identity in creation order: the first coupon of a profile
    and day is official, a later coupon with the same identity and prices is a duplicate,
    with other prices a price update, with other legs a revision. User coupons group by mode +
    selections + stake + day: the first is ``user``, the next ones are duplicates."""
    done = {r["coupon_id"]: r for r in store.rows("SELECT * FROM coupon_lineage")}
    legs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in store.rows("SELECT coupon_id, selection_id, price FROM coupon_legs"):
        legs[row["coupon_id"]].append((row["selection_id"], row["price"]))
    officials: dict[str, str] = {}
    versions: dict[str, list[tuple]] = defaultdict(list)
    manual_first: dict[str, str] = {}
    pending, counts = [], Counter()
    for coupon in store.rows(
        """SELECT c.*, a.analysis_date FROM coupons c LEFT JOIN analyses a ON a.id=c.analysis_id
        ORDER BY c.created_at, c.rowid"""
    ):
        selections = [s for s, _ in legs[coupon["id"]]]
        signature = _signature(legs[coupon["id"]])
        stored = done.get(coupon["id"])
        if _is_builder(coupon):
            day = coupon["analysis_date"] or local_day(coupon["first_start_at"])
            slot = slot_key(source=BUILDER, profile=coupon["profile"], day=day)
            identity = coupon_identity(
                source=BUILDER,
                profile=coupon["profile"],
                day=day,
                policy_version=coupon["policy_version"],
                selection_ids=selections,
            )
            if stored is not None:
                if stored["relation"] == "official":
                    officials[slot] = coupon["id"]
                if stored["relation"] != "duplicate":
                    versions[identity].append((coupon["id"], signature))
                continue
            official = officials.get(slot)
            if official is None:
                relation, official = "official", coupon["id"]
                officials[slot] = official
                reason = "backfill: pierwszy kupon profilu w dniu"
            elif versions[identity]:
                same = any(known == signature for _id, known in versions[identity])
                relation = "duplicate" if same else "price_update"
                reason = "backfill: ta sama tożsamość, " + (
                    "te same kursy" if same else "inne kursy"
                )
            else:
                relation, reason = "revision", "backfill: inne nogi tego samego profilu i dnia"
            if relation != "duplicate":
                versions[identity].append((coupon["id"], signature))
        else:
            day = local_day(coupon["created_at"])
            identity = slot = manual_key(
                mode=coupon["mode"], selection_ids=selections, stake=coupon["stake"], day=day
            )
            if stored is not None:
                manual_first.setdefault(slot, stored["official_coupon_id"])
                continue
            official = manual_first.get(slot)
            if official is None:
                relation, official, reason = "user", coupon["id"], "backfill: zapis użytkownika"
                manual_first[slot] = official
            else:
                relation, reason = "duplicate", "backfill: ten sam kupon użytkownika ponownie"
        pending.append((coupon["id"], official, relation, identity, slot, reason))
        counts[relation] += 1
    if pending:
        with store.transaction():
            for coupon_id, official, relation, identity, slot, reason in pending:
                _record(
                    store,
                    coupon_id=coupon_id,
                    official_id=official,
                    relation=relation,
                    identity=identity,
                    slot=slot,
                    now=now,
                    reason=reason,
                )
    return dict(counts)
