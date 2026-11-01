"""Sports Intelligence (TASK 005): match statistics store and pre-match features as of a time.

* ``collect_match_stats`` stores PitchAPI statistics of recent finished matches as
  append-only ``stat_observations`` (available from the moment they were observed).
* ``features_as_of(event, decision_time)`` builds the pre-match picture of both teams from
  data available strictly before ``decision_time``: form over the last 5 / 10 matches, home or
  away form, goals, rest days, fixture congestion, form against Elo expectations (strength of
  opponents) and averages of xG, shots, corners and cards. Every snapshot is saved.

Statistics are descriptive in TASK 005: no model uses them until a validated experiment shows
that they improve the forecasts.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta

from sports_edge.datasources.competitions import COMPETITIONS
from sports_edge.datasources.pitchapi import (
    PitchApiProvider,
    parse_lineups,
    parse_stats,
    season_label,
)
from sports_edge.ingestion.types import canonical, digest, iso
from sports_edge.matching import TeamIdentities, match_participant
from sports_edge.models.elo import Elo, HistoricalMatch, walk_forward
from sports_edge.offers.sources import SourceBlocked
from sports_edge.offers.text import NameNormalizer
from sports_edge.provenance import data_snapshot
from sports_edge.results_engine import effective_results, known_names
from sports_edge.storage import Store

STAT_KEYS = {
    "expected_goals": "xG",
    "total_shots": "strzały",
    "ShotsOnTarget": "strzały celne",
    "corners": "rzuty rożne",
    "yellow_cards": "żółte kartki",
    "red_cards": "czerwone kartki",
    "fouls": "faule",
    "BallPossesion": "posiadanie piłki %",
    "big_chance": "duże okazje",
}
FEATURE_SET = "form-stats-v1"


def collect_match_stats(
    store: Store,
    provider: PitchApiProvider,
    *,
    competition: str,
    teams: set[str],
    now: datetime,
    normalizer: NameNormalizer,
    aliases: dict[tuple[str, str], str],
    identities: TeamIdentities | None = None,
    last: int = 5,
    cap: int = 60,
) -> dict:
    """Statistics of the last ``last`` finished matches of each team (canonical names)."""
    if competition not in provider.competitions:
        return {"fetched": 0, "skipped": "rozgrywki poza zakresem PitchAPI"}
    try:
        matches, _data, _url = provider.season_matches(competition, season_label(now.date()))
    except SourceBlocked as error:
        return {"fetched": 0, "error": str(error)}
    rows = effective_results(
        store, sport="football", until=now, competitions=(competition,), identities=identities
    )
    known = known_names(rows, "football", normalizer) if rows else {}

    def canonical_name(name: str) -> str | None:
        if not known:
            return name  # no history yet: the source spelling is the identity (bootstrap)
        found = match_participant(name, "football", known, normalizer, aliases)
        return found.target if found.status in {"exact", "alias"} else None

    finished = sorted(
        (m for m in matches if m.get("status") == "finished"),
        key=lambda m: m.get("time_utc") or "",
        reverse=True,
    )
    wanted, per_team = [], defaultdict(int)
    for match in finished:
        home = canonical_name((match.get("home_team") or {}).get("name") or "")
        away = canonical_name((match.get("away_team") or {}).get("name") or "")
        involved = [t for t in (home, away) if t in teams and per_team[t] < last]
        if not involved:
            continue
        for team in involved:
            per_team[team] += 1
        wanted.append((match, home, away))
    fetched, already, errors = 0, 0, []
    for match, home, away in wanted:
        key = f"pitchapi:{match['id']}"
        if store.scalar(
            "SELECT COUNT(*) FROM stat_observations WHERE kind='event_statistics' AND entity_key=?",
            (key,),
        ):
            already += 1
            continue
        if fetched >= cap:
            break
        try:
            payload, _raw, url = provider.get(f"/v1/matches/{match['id']}/stats")
        except SourceBlocked as error:
            errors.append(f"{match['id']}: {error}")
            break
        periods = parse_stats(payload)
        quality = "complete" if periods.get("All") else "partial"
        body = {"home": home, "away": away, "periods": periods, "url": url}
        with store.transaction():
            store.insert(
                "stat_observations",
                id=digest(["stat", "event_statistics", key, canonical(body)]),
                kind="event_statistics",
                sport="football",
                competition=competition,
                season=season_label(now.date()),
                entity_type="event",
                entity_key=key,
                event_date=(match.get("time_utc") or match.get("date") or "")[:10],
                source="pitchapi",
                source_ref=match["id"],
                payload_json=canonical(body),
                data_version="pitchapi-v1",
                quality=quality,
                published_at=None,
                fetched_at=iso(now),
                observed_at=iso(now),
                available_at=iso(now),
                available_basis="observed_at_fetch",
            )
        fetched += 1
    return {"fetched": fetched, "already": already, "errors": errors, "wanted": len(wanted)}


def fetch_lineups(
    store: Store,
    provider: PitchApiProvider,
    *,
    competition: str,
    home: str,
    away: str,
    start_at: datetime,
    now: datetime,
    normalizer: NameNormalizer,
    aliases: dict[tuple[str, str], str],
    identities: TeamIdentities | None = None,
) -> dict:
    """Lineups of one upcoming match, on demand (clubs publish them about an hour before).

    The fixture is found through the same verified name mapping as the results; the response
    is stored as an observation. Lineups are shown to the user only — no model uses them.
    """
    if competition not in provider.competitions:
        return {"status": "rozgrywki poza zakresem PitchAPI"}
    try:
        matches, _data, _url = provider.season_matches(competition, season_label(start_at.date()))
    except SourceBlocked as error:
        return {"status": f"PitchAPI: {error}"}
    rows = effective_results(
        store, sport="football", until=now, competitions=(competition,), identities=identities
    )
    known = known_names(rows, "football", normalizer)

    def canonical_name(name: str) -> str | None:
        found = match_participant(name, "football", known, normalizer, aliases)
        return found.target if found.status in {"exact", "alias"} else None

    fixture, gap = None, timedelta(hours=36)
    for match in matches:  # the same pairing nearest to the STS kick-off time
        when = match.get("time_utc")
        if not when:
            continue
        distance = abs(datetime.fromisoformat(when.replace("Z", "+00:00")) - start_at)
        if distance > gap:
            continue
        sides = [
            canonical_name((match.get(f"{s}_team") or {}).get("name") or "")
            for s in ("home", "away")
        ]
        if sides == [home, away]:
            fixture, gap = match, distance
    if fixture is None:
        return {"status": "nie znaleziono meczu w terminarzu PitchAPI"}
    try:
        payload, _raw, url = provider.get(f"/v1/matches/{fixture['id']}/lineups")
    except SourceBlocked as error:
        return {"status": f"składy niedostępne ({error})", "match_id": fixture["id"]}
    lineups = parse_lineups(payload)
    if not any(side["starters"] for side in lineups.values()):
        return {"status": "składy jeszcze nieopublikowane", "match_id": fixture["id"]}
    body = {"home": home, "away": away, "lineups": lineups, "url": url}
    confirmed = all(side["confirmed"] for side in lineups.values())
    key = f"pitchapi:{fixture['id']}"
    with store.transaction():
        store.insert(
            "stat_observations",
            id=digest(["stat", "lineups", key, canonical(body)]),
            kind="lineups",
            sport="football",
            competition=competition,
            season=season_label(start_at.date()),
            entity_type="event",
            entity_key=key,
            event_date=start_at.date().isoformat(),
            source="pitchapi",
            source_ref=fixture["id"],
            payload_json=canonical(body),
            data_version="pitchapi-v1",
            quality="complete" if confirmed else "partial",
            published_at=None,
            fetched_at=iso(now),
            observed_at=iso(now),
            available_at=iso(now),
            available_basis="observed_at_fetch",
        )
    return {"status": "ok", "lineups": lineups, "confirmed": confirmed, "observed_at": iso(now)}


def latest_lineups(store: Store, *, home: str, away: str, day: str) -> dict | None:
    """Newest stored lineups of a match (canonical team names, match day)."""
    for row in store.rows(
        """SELECT * FROM stat_observations WHERE kind='lineups' AND event_date=?
        ORDER BY observed_at DESC""",
        (day,),
    ):
        body = json.loads(row["payload_json"])
        if (body["home"], body["away"]) == (home, away):
            return {**body, "observed_at": row["observed_at"], "quality": row["quality"]}
    return None


def _team_stats(store: Store, competition: str, team: str, decision_time: datetime, last: int):
    rows = store.rows(
        """SELECT * FROM stat_observations WHERE kind='event_statistics' AND competition=?
        AND available_at<=? ORDER BY event_date DESC""",
        (competition, iso(decision_time)),
    )
    seen, picked = set(), []
    for row in rows:
        body = json.loads(row["payload_json"])
        if team not in (body.get("home"), body.get("away")) or row["entity_key"] in seen:
            continue
        if row["event_date"] >= decision_time.date().isoformat():
            continue  # statistics of the event itself or later never describe the past
        seen.add(row["entity_key"])
        side = 0 if body["home"] == team else 1
        picked.append((row, body, side))
        if len(picked) >= last:
            break
    averages = {}
    for key, label in STAT_KEYS.items():
        own, opp = [], []
        for _row, body, side in picked:
            values = (body.get("periods") or {}).get("All", {}).get(key)
            if values and values[side] is not None and values[1 - side] is not None:
                own.append(values[side])
                opp.append(values[1 - side])
        if own:
            averages[key] = {
                "label": label,
                "for": round(sum(own) / len(own), 2),
                "against": round(sum(opp) / len(opp), 2),
                "matches": len(own),
            }
    return averages, [r["id"] for r, _b, _s in picked]


def _expected_points(rows: list[dict]) -> dict[str, dict]:
    """Pre-match Elo probabilities of every past match (as-of walk-forward, one pool)."""
    matches = [
        HistoricalMatch(
            available_at=datetime.fromisoformat(r["available_at"]),
            match_date=r["match_date"],
            home=r["home"],
            away=r["away"],
            home_score=r["home_score"],
            away_score=r["away_score"],
            competition=r["competition"],
            result_id=r["id"],
        )
        for r in rows
    ]
    out = {}
    for item in walk_forward(
        matches, Elo(k=20.0, home_advantage=60.0, draw_margin=0.26), three_way=True
    ):
        out[item["match"].result_id] = item["forecast"]
    return out


def _letter(row: dict, team: str) -> str:
    gf, ga = (
        (row["home_score"], row["away_score"])
        if row["home"] == team
        else (row["away_score"], row["home_score"])
    )
    return "W" if gf > ga else "R" if gf == ga else "P"


def _form(rows: list[dict], team: str, decision_day: str, forecasts: dict, *, venue=None, last=5):
    past = [
        r
        for r in rows
        if team in (r["home"], r["away"])
        and r["match_date"] < decision_day
        and (venue is None or (venue == "home") == (r["home"] == team))
    ][-last:]
    if not past:
        return None
    wins = draws = losses = scored = conceded = btts = over = 0
    points = expected = 0.0
    for r in past:
        home = r["home"] == team
        gf, ga = (r["home_score"], r["away_score"]) if home else (r["away_score"], r["home_score"])
        scored += gf
        conceded += ga
        btts += gf > 0 and ga > 0
        over += gf + ga > 2.5
        if gf > ga:
            wins, points = wins + 1, points + 3
        elif gf == ga:
            draws, points = draws + 1, points + 1
        else:
            losses += 1
        f = forecasts.get(r["id"])
        if f:
            win, draw = (f["home"], f["draw"]) if home else (f["away"], f["draw"])
            expected += 3 * win + draw
    n = len(past)
    return {
        "matches": n,
        "sequence": "".join(_letter(r, team) for r in past),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "points_per_game": round(points / n, 2),
        "goals_for": round(scored / n, 2),
        "goals_against": round(conceded / n, 2),
        "btts_rate": round(btts / n, 2),
        "over_2_5_rate": round(over / n, 2),
        "points_vs_elo_expectation": round(points - expected, 2) if forecasts else None,
    }


def features_as_of(
    store: Store,
    event: dict,
    decision_time: datetime,
    *,
    normalizer: NameNormalizer,
    aliases: dict[tuple[str, str], str],
    identities: TeamIdentities | None = None,
    save: bool = True,
) -> dict | None:
    """Pre-match features of both teams from data available before ``decision_time``."""
    competition = next(
        (
            c
            for c, comp in COMPETITIONS.items()
            if event.get("league") in comp.sts_leagues and comp.sport == event.get("sport")
        ),
        None,
    )
    if competition is None:
        return None
    rows = effective_results(
        store,
        sport=event["sport"],
        until=decision_time,
        competitions=(competition,),
        identities=identities,
    )
    if not rows:
        return None
    known = known_names(rows, event["sport"], normalizer)
    decision_day = decision_time.date().isoformat()
    forecasts = _expected_points(rows) if event["sport"] == "football" else {}
    teams, used_stats = {}, []
    for side in ("home", "away"):
        found = match_participant(event[side] or "", event["sport"], known, normalizer, aliases)
        if found.status not in {"exact", "alias"}:
            teams[side] = {"sts": event[side], "matched": False, "status": found.status}
            continue
        team = found.target
        dates = [
            r["match_date"]
            for r in rows
            if team in (r["home"], r["away"]) and r["match_date"] < decision_day
        ]
        last_day = max(dates) if dates else None
        start = (
            datetime.fromisoformat(event["start_at"]) if event.get("start_at") else decision_time
        )
        stats, ids = _team_stats(store, competition, team, decision_time, 5)
        used_stats.extend(ids)
        teams[side] = {
            "sts": event[side],
            "team": team,
            "matched": True,
            "form5": _form(rows, team, decision_day, forecasts, last=5),
            "form10": _form(rows, team, decision_day, forecasts, last=10),
            "venue_form5": _form(rows, team, decision_day, forecasts, venue=side, last=5),
            "rest_days": (start.date() - datetime.fromisoformat(last_day).date()).days
            if last_day
            else None,
            "matches_last_7": sum(
                d >= (start.date() - timedelta(days=7)).isoformat() for d in dates
            ),
            "matches_last_14": sum(
                d >= (start.date() - timedelta(days=14)).isoformat() for d in dates
            ),
            "stats5": stats,
        }
    features = {"competition": competition, "teams": teams}
    inputs = {
        "decision_time": iso(decision_time),
        "results_snapshot": data_snapshot(r["id"] for r in rows),
        "stat_observations": sorted(set(used_stats)),  # a match of both teams counts once
        "feature_set": FEATURE_SET,
        "rule": "wyłącznie dane z available_at ≤ decision_time i mecze sprzed dnia decyzji",
    }
    if save:
        key = digest(
            ["features", event["id"], iso(decision_time), FEATURE_SET, canonical(features)]
        )
        with store.transaction():
            store.insert(
                "feature_snapshots",
                id=key,
                event_id=event["id"],
                decision_time=iso(decision_time),
                feature_set=FEATURE_SET,
                features_json=canonical(features),
                inputs_json=canonical(inputs),
                created_at=iso(decision_time),
            )
    return {"features": features, "inputs": inputs}
