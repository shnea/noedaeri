import os
import secrets
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from noedaeri.api import create_app
from noedaeri.auth import COOKIE, digest
from noedaeri.config import Settings


@pytest.fixture
def app(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for PostgreSQL integration checks")
    schema = "test_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    settings = Settings(
        database_url=url + f" options=-csearch_path={schema}",
        worker_key=secrets.token_urlsafe(32),
        public_origin="https://testserver",
        storage_root=Path(tmp_path),
        integration_key=secrets.token_urlsafe(32),
        webhook_secret=secrets.token_urlsafe(32),
        webhook_url="https://receiver.example/completion",
        free_floor=0,
        upload_limit=4 * 1024 * 1024,
        storage_limit=20 * 1024 * 1024,
    )
    application = create_app(settings)
    application.state.settings = settings
    application.state.webhooks.transport = httpx.MockTransport(lambda request: httpx.Response(204))
    with TestClient(application, base_url="https://testserver") as client:
        application.state.client = client
        yield application
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def login(app, status="approved", role="user"):
    user_id, token, csrf = uuid4(), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with app.state.db.connect() as conn:
        conn.execute(
            "INSERT INTO users(id,issuer,subject,status,role) VALUES(%s,%s,%s,%s,%s)",
            (user_id, "https://issuer.example", str(user_id), status, role),
        )
        conn.execute(
            "INSERT INTO sessions VALUES(%s,%s,%s,now()+interval '1 hour')",
            (digest(token), user_id, csrf),
        )
    client = app.state.client
    client.cookies.set(COOKIE, token)
    client.headers.update({"Origin": "https://testserver", "X-CSRF-Token": csrf})
    return client, user_id
