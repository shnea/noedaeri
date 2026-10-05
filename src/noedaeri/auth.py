import base64
import hashlib
import secrets
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from .config import Settings
from .db import Database

COOKIE = "__Host-noedaeri"
LOGIN_COOKIE = "__Host-noedaeri-login"


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


class Auth:
    def __init__(self, settings: Settings, db: Database):
        self.settings, self.db = settings, db

    def user(self, request: Request, approved=True, admin=False):
        token = request.cookies.get(COOKIE, "")
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT u.*, s.csrf FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.digest=%s AND s.expires_at>now()",
                (digest(token),),
            ).fetchone()
        if not row:
            raise HTTPException(401, "login_required")
        if approved and row["status"] != "approved":
            raise HTTPException(403, "approval_required")
        if admin and row["role"] != "admin":
            raise HTTPException(403, "admin_required")
        if request.method not in {"GET", "HEAD"}:
            if request.headers.get(
                "origin"
            ) != self.settings.public_origin or not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), row["csrf"]
            ):
                raise HTTPException(403, "csrf_failed")
        return row

    def metadata(self):
        if not self.settings.issuer:
            raise HTTPException(503, "login_not_configured")
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.get(self.settings.issuer + "/.well-known/openid-configuration")
            response.raise_for_status()
            meta = response.json()
        if meta["issuer"] != self.settings.issuer:
            raise ValueError("Issuer mismatch")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not meta[key].startswith("https://"):
                raise ValueError("OIDC endpoints must use HTTPS")
        return meta

    def login(self):
        meta = self.metadata()
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        with self.db.connect() as conn:
            conn.execute("DELETE FROM login_attempts WHERE expires_at<now()")
            conn.execute(
                "INSERT INTO login_attempts VALUES(%s,%s,%s,now()+interval '10 minutes')",
                (digest(state), verifier, nonce),
            )
        url = (
            meta["authorization_endpoint"]
            + "?"
            + urlencode(
                {
                    "client_id": self.settings.client_id,
                    "redirect_uri": self.settings.redirect_uri,
                    "response_type": "code",
                    "scope": "openid profile email",
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge.decode().rstrip("="),
                    "code_challenge_method": "S256",
                }
            )
        )
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(
            LOGIN_COOKIE, state, secure=True, httponly=True, samesite="lax", max_age=600, path="/"
        )
        return response

    def callback(self, request: Request, state: str, code: str):
        if not state or not secrets.compare_digest(request.cookies.get(LOGIN_COOKIE, ""), state):
            raise HTTPException(400, "invalid_login_state")
        with self.db.connect() as conn:
            attempt = conn.execute(
                "DELETE FROM login_attempts WHERE digest=%s AND expires_at>now() RETURNING *",
                (digest(state),),
            ).fetchone()
        if not attempt:
            raise HTTPException(400, "login_expired")
        meta = self.metadata()
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.post(
                meta["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.settings.client_id,
                    "code": code,
                    "code_verifier": attempt["verifier"],
                    "redirect_uri": self.settings.redirect_uri,
                },
            )
            response.raise_for_status()
            token = response.json()["id_token"]
            keys = client.get(meta["jwks_uri"])
            keys.raise_for_status()
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise ValueError("Unsupported signing algorithm")
        key = next(key for key in keys.json()["keys"] if key.get("kid") == header.get("kid"))
        claims = jwt.decode(
            token,
            jwt.PyJWK.from_dict(key).key,
            algorithms=["RS256"],
            audience=self.settings.client_id,
            issuer=self.settings.issuer,
            options={"require": ["exp", "iat", "iss", "sub", "aud", "nonce"]},
        )
        if claims["nonce"] != attempt["nonce"] or not claims["sub"]:
            raise ValueError("Invalid OIDC claims")
        if claims.get("azp", self.settings.client_id) != self.settings.client_id:
            raise ValueError("Invalid authorized party")
        bootstrap = (
            bool(self.settings.admin_subject)
            and claims["iss"] == self.settings.admin_issuer
            and claims["sub"] == self.settings.admin_subject
        )
        session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db.connect() as conn:
            user = conn.execute(
                "INSERT INTO users(id, issuer, subject, status, role) VALUES(%s,%s,%s,%s,%s) "
                "ON CONFLICT(issuer,subject) DO UPDATE SET "
                "role=CASE WHEN %s THEN 'admin' ELSE users.role END, "
                "status=CASE WHEN %s THEN 'approved' ELSE users.status END RETURNING id",
                (
                    uuid4(),
                    claims["iss"],
                    claims["sub"],
                    "approved" if bootstrap else "pending",
                    "admin" if bootstrap else "user",
                    bootstrap,
                    bootstrap,
                ),
            ).fetchone()
            conn.execute("DELETE FROM sessions WHERE expires_at<now()")
            conn.execute(
                "INSERT INTO sessions VALUES(%s,%s,%s,now()+interval '8 hours')",
                (digest(session), user["id"], csrf),
            )
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="lax")
        response.set_cookie(
            COOKIE, session, secure=True, httponly=True, samesite="lax", max_age=28800, path="/"
        )
        return response
