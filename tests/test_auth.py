import json
import time
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from noedaeri.auth import LOGIN_COOKIE, digest


@pytest.mark.parametrize("fault", [None, "nonce", "signature", "audience", "expired"])
def test_oidc_signature_nonce_replay_and_no_implicit_admin(app, monkeypatch, fault):
    from noedaeri.auth import Auth

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "fixture", "alg": "RS256", "use": "sig"})
    issuer = app.state.settings.issuer
    metadata = {
        "issuer": issuer,
        "token_endpoint": "https://issuer.example/token",
        "jwks_uri": "https://issuer.example/keys",
    }
    monkeypatch.setattr(Auth, "metadata", lambda _: metadata)
    subject, nonce = str(uuid4()), "fixture-nonce"
    claims = {
        "iss": issuer,
        "sub": subject,
        "aud": "app",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": nonce,
    }
    if fault == "nonce":
        claims["nonce"] = "wrong"
    elif fault == "audience":
        claims["aud"] = "wrong"
    elif fault == "expired":
        claims["exp"] = int(time.time()) - 60
    signing_key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if fault == "signature"
        else key
    )
    token = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "fixture"})
    real_client = httpx.Client

    def handler(request):
        if request.url.path == "/token":
            assert b"code_verifier=fixture-verifier" in request.content
            return httpx.Response(200, json={"id_token": token})
        return httpx.Response(200, json={"keys": [jwk]})

    monkeypatch.setattr(
        "noedaeri.auth.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    state = "fixture-state"
    with app.state.db.connect() as conn:
        conn.execute(
            "INSERT INTO login_attempts VALUES(%s,%s,%s,now()+interval '1 minute')",
            (digest(state), "fixture-verifier", nonce),
        )
    client = app.state.client
    client.cookies.set(LOGIN_COOKIE, state)
    response = client.get(
        "/auth/callback", params={"state": state, "code": "fixture-code"}, follow_redirects=False
    )
    if fault:
        assert response.status_code == 400
        with app.state.db.connect() as conn:
            assert conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 0
        return
    assert response.status_code == 303
    assert "Secure" in response.headers["set-cookie"]
    with app.state.db.connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE subject=%s", (subject,)).fetchone()
    assert user["role"] == "user"
    assert user["status"] == "pending"
    assert (
        client.get(
            "/auth/callback",
            params={"state": state, "code": "fixture-code"},
            follow_redirects=False,
        ).status_code
        == 400
    )


@pytest.mark.parametrize("state", ["", "wrong"])
def test_login_state_must_match_cookie(app, state):
    assert (
        app.state.client.get(
            "/auth/callback", params={"state": state, "code": "x"}, follow_redirects=False
        ).status_code
        == 400
    )


@pytest.mark.parametrize("provider_available", [True, False])
def test_logout_revokes_session_and_links_provider(app, monkeypatch, provider_available):
    from urllib.parse import parse_qs, urlsplit

    from conftest import login

    from noedaeri.auth import COOKIE, Auth

    def metadata(_):
        if not provider_available:
            raise httpx.ConnectError("unavailable")
        return {"end_session_endpoint": "https://issuer.example/logout"}

    monkeypatch.setattr(Auth, "metadata", metadata)
    client, _ = login(app)
    token = client.cookies.get(COOKIE)
    response = client.post("/auth/logout", follow_redirects=False)
    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    with app.state.db.connect() as conn:
        assert not conn.execute(
            "SELECT 1 FROM sessions WHERE digest=%s", (digest(token),)
        ).fetchone()
    # Replay the old cookie: server-side revocation must hold independently of deletion.
    client.cookies.set(COOKIE, token)
    assert client.get("/api/me").status_code == 401
    target = response.json()["logout_url"]
    if provider_available:
        assert urlsplit(target).netloc == "issuer.example"
        assert parse_qs(urlsplit(target).query) == {
            "client_id": ["app"],
            "post_logout_redirect_uri": ["https://testserver/"],
        }
    else:
        assert target is None
