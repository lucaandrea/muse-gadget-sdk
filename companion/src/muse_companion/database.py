"""Small PostgreSQL adapter for the companion's fixed, parameterized SQL.

The service remains a single worker. A transaction advisory lock gives its
SQLite-style compound operations the same serialization across CLI connections.
No caller-controlled identifiers or SQL enter this adapter.
"""
import re


def postgres_sql(sql, has_params=False):
    sql = sql.strip().rstrip(";")
    ignore = sql.startswith("INSERT OR IGNORE INTO ")
    if ignore:
        sql = sql.replace("INSERT OR IGNORE INTO ", "INSERT INTO ", 1) + " ON CONFLICT DO NOTHING"
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
    sql = re.sub(r"\bBLOB\b", "BYTEA", sql)
    sql = sql.replace("X''", "''::bytea")
    if has_params:
        # Percent escaping is required even inside SQL string literals. Question
        # marks in quoted literals are text, never parameter placeholders.
        parts = re.split(r"('(?:''|[^'])*')", sql.replace("%", "%%"))
        sql = "".join(part if index % 2 else part.replace("?", "%s") for index, part in enumerate(parts))
    return sql


class Postgres:
    def __init__(self, url):
        import psycopg
        from psycopg.rows import dict_row
        try:
            self.connection = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=15)
        except psycopg.Error:
            raise RuntimeError("Cannot connect to the configured PostgreSQL database") from None

    @property
    def in_transaction(self):
        from psycopg.pq import TransactionStatus
        return self.connection.info.transaction_status != TransactionStatus.IDLE

    def execute(self, sql, params=()):
        if sql == "BEGIN IMMEDIATE":
            self.connection.execute("BEGIN")
            return self.connection.execute("SELECT pg_advisory_xact_lock(710329645)")
        table = re.fullmatch(r"PRAGMA table_info\(([a-z_]+)\)", sql)
        if table:
            return self.connection.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (table[1],))
        version = re.fullmatch(r"PRAGMA user_version=(\d+)", sql)
        if version:
            return self.connection.execute("INSERT INTO settings(key,value) VALUES ('storage_schema',%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (version[1],))
        if sql.startswith("PRAGMA"):
            raise ValueError("Unsupported PostgreSQL compatibility query")
        return self.connection.execute(postgres_sql(sql, bool(params)), params or None)

    def executescript(self, script):
        # Schema is a constant from Store, never user input, with no procedural SQL.
        for statement in script.split(";"):
            statement = statement.strip()
            if statement and not statement.startswith("PRAGMA"):
                self.execute(statement)

    def close(self):
        self.connection.close()
