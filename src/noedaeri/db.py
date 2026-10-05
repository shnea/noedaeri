from pathlib import Path

import psycopg
from psycopg.rows import dict_row


class Database:
    def __init__(self, url: str):
        self.url = url

    def connect(self):
        try:
            return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5)
        except psycopg.OperationalError:
            raise RuntimeError("database_unavailable") from None

    def migrate(self):
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(756902)")
            connection.execute(Path(__file__).with_name("schema.sql").read_text())
