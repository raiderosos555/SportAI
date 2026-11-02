"""SQLite operational store. Append-only records: corrections add rows, nothing is updated."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 6
ARCHIVED_BOOKMAKERS = ("superbet", "unknown")
ARCHIVED_SOURCES = ("superbet_pdf_manual", "generic_pdf_manual")
# Columns added after v2; applied with ALTER TABLE to old and new databases alike.
ADDED_COLUMNS = (
    ("imports", "fetched_at", "TEXT"),
    ("imports", "source_url", "TEXT"),
    ("historical_results", "available_at_basis", "TEXT NOT NULL DEFAULT 'assumed_lag_36h'"),
    ("historical_results", "start_at", "TEXT"),
    ("historical_results", "details_json", "TEXT NOT NULL DEFAULT '{}'"),
    # v4: results providers (provenance of every observation, corrections as new rows)
    ("historical_results", "provider", "TEXT"),
    ("historical_results", "source_event_id", "TEXT"),
    ("historical_results", "status", "TEXT NOT NULL DEFAULT 'finished'"),
    ("historical_results", "home_raw", "TEXT"),
    ("historical_results", "away_raw", "TEXT"),
    ("historical_results", "match_confidence", "TEXT"),
    ("historical_results", "published_at", "TEXT"),
    ("historical_results", "observed_at", "TEXT"),
    ("data_imports", "summary_json", "TEXT NOT NULL DEFAULT '{}'"),
    # v5: coupon proposals of the probability-first builder (profile, policy, prediction)
    ("coupons", "profile", "TEXT"),
    ("coupons", "policy_version", "TEXT"),
    ("coupons", "predicted_probability", "TEXT"),
    ("coupons", "proposal_json", "TEXT"),
)
MIGRATIONS = {
    3: "STS ONLY, archive, odds metadata",
    4: "results providers: provenance columns and update reports",
    5: "sports statistics store, feature snapshots, coupon proposals",
    6: "TASK 007: model history, prediction settlements, job runs, simulations, LLM usage",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS archived_entities (
 id TEXT PRIMARY KEY, entity_type TEXT NOT NULL CHECK(entity_type IN ('bookmaker','source')),
 entity_id TEXT NOT NULL, reason TEXT NOT NULL, archived_at TEXT NOT NULL,
 UNIQUE(entity_type, entity_id)
);
CREATE TABLE IF NOT EXISTS schema_migrations (
 id TEXT PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL,
 description TEXT NOT NULL, backup_path TEXT
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, terms_ref TEXT NOT NULL,
 retention TEXT NOT NULL, checked_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bookmakers (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
 bookmaker_id TEXT NOT NULL REFERENCES bookmakers(id), parser TEXT NOT NULL,
 parser_verified INTEGER NOT NULL CHECK(parser_verified IN (0,1)),
 raw_sha256 TEXT NOT NULL, file_name TEXT, raw_path TEXT,
 data_kind TEXT NOT NULL CHECK(data_kind IN ('prospective','archive','synthetic','demo')),
 offer_kind TEXT NOT NULL, generated_at TEXT, ingested_at TEXT NOT NULL,
 rows_total INTEGER NOT NULL, rows_ok INTEGER NOT NULL, rows_review INTEGER NOT NULL,
 unparsed INTEGER NOT NULL, quality REAL NOT NULL, summary_json TEXT NOT NULL,
 UNIQUE(raw_sha256, parser, data_kind)
);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, bookmaker_id TEXT NOT NULL REFERENCES bookmakers(id),
 sport TEXT NOT NULL, sport_raw TEXT NOT NULL, league TEXT NOT NULL, country TEXT,
 home TEXT, away TEXT, participants_json TEXT NOT NULL, name_raw TEXT NOT NULL,
 start_at TEXT, offer_date TEXT, first_import_id TEXT NOT NULL REFERENCES imports(id)
);
CREATE TABLE IF NOT EXISTS markets (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id), market_key TEXT NOT NULL,
 period TEXT NOT NULL, overtime TEXT NOT NULL, line TEXT, label_raw TEXT NOT NULL,
 rules_ref TEXT NOT NULL, mapping_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS selections (
 id TEXT PRIMARY KEY, market_id TEXT NOT NULL REFERENCES markets(id), key TEXT NOT NULL,
 label_raw TEXT NOT NULL, UNIQUE(market_id, key)
);
CREATE TABLE IF NOT EXISTS quote_sets (
 id TEXT PRIMARY KEY, market_id TEXT NOT NULL REFERENCES markets(id),
 import_id TEXT NOT NULL REFERENCES imports(id), offer_code TEXT, page INTEGER NOT NULL,
 source_text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('ok','review')),
 quality REAL NOT NULL, issues_json TEXT NOT NULL, valid_at TEXT, ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS odds (
 id TEXT PRIMARY KEY, quote_set_id TEXT NOT NULL REFERENCES quote_sets(id),
 selection_id TEXT NOT NULL REFERENCES selections(id), price TEXT NOT NULL,
 price_text TEXT NOT NULL, UNIQUE(quote_set_id, selection_id)
);
CREATE TABLE IF NOT EXISTS review_items (
 id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES imports(id), page INTEGER NOT NULL,
 source_text TEXT NOT NULL, reason TEXT NOT NULL, context TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_decisions (
 id TEXT PRIMARY KEY, target_kind TEXT NOT NULL CHECK(target_kind IN ('quote_set','review_item')),
 target_id TEXT NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('confirmed','rejected')),
 note TEXT NOT NULL, decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
 id TEXT PRIMARY KEY, sport TEXT NOT NULL, alias_key TEXT NOT NULL, canonical_key TEXT NOT NULL,
 origin TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(sport, alias_key, canonical_key)
);
CREATE TABLE IF NOT EXISTS event_links (
 id TEXT PRIMARY KEY, event_a TEXT NOT NULL REFERENCES events(id),
 event_b TEXT NOT NULL REFERENCES events(id),
 status TEXT NOT NULL CHECK(status IN ('auto','confirmed','rejected')),
 confidence REAL NOT NULL, method TEXT NOT NULL, evidence_json TEXT NOT NULL,
 decided_at TEXT NOT NULL, CHECK(event_a <> event_b)
);
CREATE TABLE IF NOT EXISTS models (
 id TEXT PRIMARY KEY, family TEXT NOT NULL, sport TEXT NOT NULL, description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_versions (
 id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id), version TEXT NOT NULL,
 created_at TEXT NOT NULL, trained_through TEXT, training_data_json TEXT NOT NULL,
 validation_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('experimental','validated','retired'))
);
CREATE TABLE IF NOT EXISTS predictions (
 id TEXT PRIMARY KEY, model_version_id TEXT NOT NULL REFERENCES model_versions(id),
 event_id TEXT REFERENCES events(id), market_key TEXT NOT NULL, period TEXT NOT NULL,
 line TEXT, created_at TEXT NOT NULL, probabilities_json TEXT NOT NULL,
 uncertainty_json TEXT NOT NULL, inputs_json TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('prospective','backtest','simulation'))
);
CREATE TABLE IF NOT EXISTS analyses (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, analysis_date TEXT NOT NULL,
 policy_json TEXT NOT NULL, summary_json TEXT NOT NULL, items_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coupons (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, analysis_id TEXT REFERENCES analyses(id),
 kind TEXT NOT NULL CHECK(kind IN ('solo','ako')),
 mode TEXT NOT NULL CHECK(mode IN
   ('paper_system','paper_manual','simulation','backtest','real_manual')),
 bookmaker_id TEXT NOT NULL REFERENCES bookmakers(id), stake TEXT NOT NULL,
 currency TEXT NOT NULL, cost_profile_id TEXT NOT NULL, total_odds TEXT NOT NULL,
 first_start_at TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coupon_legs (
 id TEXT PRIMARY KEY, coupon_id TEXT NOT NULL REFERENCES coupons(id),
 odds_id TEXT NOT NULL REFERENCES odds(id), selection_id TEXT NOT NULL REFERENCES selections(id),
 price TEXT NOT NULL, prediction_id TEXT REFERENCES predictions(id),
 UNIQUE(coupon_id, selection_id)
);
CREATE TABLE IF NOT EXISTS results (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id), source TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('completed','void','postponed','cancelled','abandoned')),
 home_score INTEGER, away_score INTEGER, ht_home INTEGER, ht_away INTEGER,
 details_json TEXT NOT NULL, verified INTEGER NOT NULL CHECK(verified IN (0,1)),
 recorded_at TEXT NOT NULL, supersedes_id TEXT REFERENCES results(id)
);
CREATE TABLE IF NOT EXISTS settlements (
 id TEXT PRIMARY KEY, coupon_id TEXT NOT NULL REFERENCES coupons(id), outcome TEXT NOT NULL,
 payout TEXT NOT NULL, net_profit TEXT NOT NULL, cash_flow_json TEXT NOT NULL,
 legs_json TEXT NOT NULL, settled_at TEXT NOT NULL,
 supersedes_id TEXT REFERENCES settlements(id)
);
CREATE TABLE IF NOT EXISTS experiments (
 id TEXT PRIMARY KEY, hypothesis TEXT NOT NULL, registered_at TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('simulation','backtest','prospective_paper','real_manual')),
 config_json TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS data_imports (
 id TEXT PRIMARY KEY, source_name TEXT NOT NULL, license_note TEXT NOT NULL,
 file_sha256 TEXT NOT NULL, file_name TEXT, kind TEXT NOT NULL, rows INTEGER NOT NULL,
 ingested_at TEXT NOT NULL, UNIQUE(file_sha256, kind)
);
CREATE TABLE IF NOT EXISTS historical_results (
 id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES data_imports(id),
 sport TEXT NOT NULL, competition TEXT NOT NULL, season TEXT, match_date TEXT NOT NULL,
 home TEXT NOT NULL, away TEXT NOT NULL, home_score INTEGER NOT NULL,
 away_score INTEGER NOT NULL, available_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmark_odds (
 id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES data_imports(id),
 result_id TEXT NOT NULL REFERENCES historical_results(id), operator TEXT NOT NULL,
 moment TEXT NOT NULL CHECK(moment IN ('opening','pre_match','closing','unknown')),
 market_key TEXT NOT NULL, selection TEXT NOT NULL, price TEXT NOT NULL, observed_at TEXT,
 UNIQUE(result_id, operator, moment, market_key, selection)
);
CREATE TABLE IF NOT EXISTS stat_observations (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, sport TEXT NOT NULL, competition TEXT, season TEXT,
 entity_type TEXT NOT NULL CHECK(entity_type IN ('event','team','player','competition')),
 entity_key TEXT NOT NULL, event_date TEXT, source TEXT NOT NULL, source_ref TEXT,
 payload_json TEXT NOT NULL, data_version TEXT NOT NULL,
 quality TEXT NOT NULL CHECK(quality IN ('complete','partial','unverified')),
 published_at TEXT, fetched_at TEXT NOT NULL, observed_at TEXT NOT NULL,
 available_at TEXT NOT NULL, available_basis TEXT NOT NULL, import_id TEXT
);
CREATE TABLE IF NOT EXISTS feature_snapshots (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id), decision_time TEXT NOT NULL,
 feature_set TEXT NOT NULL, features_json TEXT NOT NULL, inputs_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_events (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
 model_version_id TEXT REFERENCES model_versions(id), scope TEXT, family TEXT,
 kind TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('system','user','migration')),
 reason TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS prediction_settlements (
 id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL REFERENCES predictions(id),
 revision INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('settled','unresolved','cancelled','postponed','void',
   'uncertain','source_conflict','missing_stat')),
 outcome_json TEXT NOT NULL, result_ref TEXT, source TEXT, settled_at TEXT NOT NULL,
 supersedes_id TEXT REFERENCES prediction_settlements(id), reason TEXT NOT NULL DEFAULT '',
 UNIQUE(prediction_id, revision)
);
CREATE TABLE IF NOT EXISTS job_runs (
 id TEXT PRIMARY KEY, job TEXT NOT NULL, trigger TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('ok','skipped','failed','partial')),
 reason TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS simulation_runs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('budget','historical')),
 created_at TEXT NOT NULL, policy_version TEXT, config_json TEXT NOT NULL,
 result_json TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_usage (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
 purpose TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
 cache_read_tokens INTEGER NOT NULL DEFAULT 0, cost_usd TEXT NOT NULL, status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS model_event_role ON model_events(scope, family, kind, created_at);
CREATE INDEX IF NOT EXISTS prediction_settlement ON prediction_settlements(prediction_id);
CREATE INDEX IF NOT EXISTS job_run ON job_runs(job, started_at);
CREATE INDEX IF NOT EXISTS prediction_event ON predictions(event_id, market_key);
CREATE INDEX IF NOT EXISTS stat_entity ON stat_observations(kind, entity_key, available_at);
CREATE INDEX IF NOT EXISTS feature_event ON feature_snapshots(event_id, decision_time);
CREATE INDEX IF NOT EXISTS event_day ON events(bookmaker_id, offer_date);
CREATE INDEX IF NOT EXISTS market_event ON markets(event_id);
CREATE INDEX IF NOT EXISTS quote_market ON quote_sets(market_id, valid_at);
CREATE INDEX IF NOT EXISTS odds_quote ON odds(quote_set_id);
CREATE INDEX IF NOT EXISTS result_event ON results(event_id);
CREATE INDEX IF NOT EXISTS history_date ON historical_results(sport, match_date);
CREATE TRIGGER IF NOT EXISTS odds_same_market BEFORE INSERT ON odds
WHEN (SELECT market_id FROM selections WHERE id=NEW.selection_id) <>
     (SELECT market_id FROM quote_sets WHERE id=NEW.quote_set_id)
BEGIN SELECT RAISE(ABORT, 'incompatible_market'); END;
"""


class Store:
    def __init__(self, path: Path | str, *, backup_dir: Path | None = None):
        memory = str(path) == ":memory:"
        if not memory:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.connection = sqlite3.connect(
            str(path), isolation_level=None, timeout=15, check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA recursive_triggers=ON")
        self.version_before = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if self.version_before > SCHEMA_VERSION:
            raise RuntimeError("Database was created by a newer version of the application")
        self.migration_backup: Path | None = None
        if not memory and 0 < self.version_before < SCHEMA_VERSION and backup_dir is not None:
            stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
            self.migration_backup = backup_dir / f"pre-migration-v{SCHEMA_VERSION}_{stamp}.db"
            self.backup_to(self.migration_backup)
        if not memory:
            self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        for table, column, ddl in ADDED_COLUMNS:
            existing = {r[1] for r in self.connection.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        self.tables = {
            r[0]
            for r in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self._columns: dict[str, set[str]] = {}
        for table in sorted(self.tables):
            for action in ("UPDATE", "DELETE"):
                self.connection.execute(
                    f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action}
                    BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append_only'); END"""
                )
        if self.version_before < SCHEMA_VERSION:
            self._migrate()
        self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _migrate(self) -> None:
        """One logged migration run from ``version_before`` to the current schema.

        v3: Superbet and generic sources leave the active flow; their rows stay archived.
        v4: only new nullable/default columns (ALTER TABLE above); existing rows are kept.
        v5: new append-only tables (statistics, feature snapshots) and nullable coupon columns.
        v6: new append-only tables only (model history, prediction settlements, job runs,
        simulation runs, LLM usage); no existing row is touched. Model roles of the versions
        that already exist are recorded by the application (``learning.ensure_roles``).
        """
        now = datetime.now(UTC).isoformat(timespec="microseconds")
        steps = [v for v in sorted(MIGRATIONS) if v > self.version_before]
        with self.transaction():
            if 3 in steps:
                for kind, table, ids in (
                    ("bookmaker", "bookmakers", ARCHIVED_BOOKMAKERS),
                    ("source", "sources", ARCHIVED_SOURCES),
                ):
                    for entity in ids:
                        if self.get(table, entity) is not None:
                            self.insert(
                                "archived_entities",
                                id=f"{kind}:{entity}",
                                entity_type=kind,
                                entity_id=entity,
                                reason="TASK 003: aplikacja obsługuje wyłącznie STS",
                                archived_at=now,
                            )
            self.insert(
                "schema_migrations",
                id=f"v{SCHEMA_VERSION}:{now}",
                version=SCHEMA_VERSION,
                applied_at=now,
                description=f"from v{self.version_before}: "
                + "; ".join(f"v{v} {MIGRATIONS[v]}" for v in steps),
                backup_path=str(self.migration_backup) if self.migration_backup else None,
            )

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def rows(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.connection.execute(sql, params)]

    def scalar(self, sql: str, params: tuple = ()):
        row = self.connection.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def get(self, table: str, key: str) -> dict | None:
        if table not in self.tables:
            raise ValueError("unknown_table")
        rows = self.rows(f"SELECT * FROM {table} WHERE id=?", (key,))
        return rows[0] if rows else None

    def insert(self, table: str, **values) -> bool:
        """Insert once. Same id with identical values is a no-op; differing values fail."""
        if table not in self.tables:
            raise ValueError("unknown_table")
        if table not in self._columns:
            self._columns[table] = {
                r[1] for r in self.connection.execute(f"PRAGMA table_info({table})")
            }
        if not set(values) <= self._columns[table] or "id" not in values:
            raise ValueError("unknown_column")
        existing = self.get(table, values["id"])
        if existing is not None:
            if any(existing[k] != v for k, v in values.items()):
                raise ValueError("identity_conflict")
            return False
        keys = list(values)
        self.connection.execute(
            f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
            tuple(values[k] for k in keys),
        )
        return True

    def backup_to(self, target: Path) -> None:
        """Consistent snapshot through SQLite's online backup API."""
        snapshot(self.connection, target)


def snapshot(source: sqlite3.Connection, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
    finally:
        destination.close()


def snapshot_file(database: Path, target: Path) -> None:
    """Copy a database file without opening it through Store (no migration side effects)."""
    source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        snapshot(source, target)
    finally:
        source.close()


def archived_ids(store: Store, entity_type: str) -> set[str]:
    return {
        r["entity_id"]
        for r in store.rows(
            "SELECT entity_id FROM archived_entities WHERE entity_type=?", (entity_type,)
        )
    }
