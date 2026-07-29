"""Luna handshake: enroll → link (certified) → signed sync → unlink."""

import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import init_db
from app.main import app


@pytest.fixture(scope="module", autouse=True)
async def _ready():
    await init_db()
    yield


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _sign(secret: str, body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return raw, {
        "X-Luna-Tenant": "",  # filled by caller
        "X-Luna-Ts": ts,
        "X-Luna-Signature": sig,
        "Content-Type": "application/json",
    }


async def _signup(c: AsyncClient, email: str, username: str) -> dict:
    await c.post("/api/auth/signup", json={"email": email, "username": username, "password": "pw12345"})
    token = (await c.post("/api/auth/login", json={
        "email": email, "username": username, "password": "pw12345"})).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_enroll_link_sync_unlink():
    async with _client() as c:
        # 1. enroll — secret + link code issued exactly once
        r = await c.post("/api/luna/enroll", json={
            "install_id": "abc123def456", "luna_name": "Roy's Luna", "luna_version": "1.2.0"})
        assert r.status_code == 200, r.text
        first = r.json()
        assert first["secret"] and first["link_code"] and not first["linked"]
        tenant, secret = first["tenant"], first["secret"]

        # re-enroll: existing, secret never re-issued
        again = (await c.post("/api/luna/enroll", json={"install_id": "abc123def456"})).json()
        assert again["existing"] is True and again["secret"] is None
        assert again["link_code"] == first["link_code"]  # still valid, unchanged

        # 2. link by a signed-in user → certified
        h = await _signup(c, "luna-owner@example.com", "lunaowner")
        r = await c.post("/api/me/link-luna", json={"code": first["link_code"]}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["certified_at"]

        me = (await c.get("/api/me/luna-installs", headers=h)).json()
        assert me["certified_at"] and len(me["installs"]) == 1

        # linked install no longer exposes a link code
        linked = (await c.post("/api/luna/enroll", json={"install_id": "abc123def456"})).json()
        assert linked["linked"] is True and linked["link_code"] is None

        # 3. signed sync
        body = {
            "installed": [
                {"name": "plugin-web-access", "version": "0.4.0", "settings": False},
                {"name": "plugin-mcp", "version": "0.2.0", "settings": True},
            ],
            "base_url": "http://localhost:8300",
        }
        raw, headers = _sign(secret, body)
        headers["X-Luna-Tenant"] = tenant
        r = await c.post("/api/luna/sync", content=raw, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["installed_count"] == 2

        inst = (await c.get("/api/me/installed", headers=h)).json()
        assert inst["installed"]["plugin-web-access"] == "0.4.0"
        assert inst["settings"] == {"plugin-mcp": True}
        assert inst["luna_base_url"] == "http://localhost:8300"

        # bad signature rejected
        raw2, headers2 = _sign("wrong-secret", body)
        headers2["X-Luna-Tenant"] = tenant
        assert (await c.post("/api/luna/sync", content=raw2, headers=headers2)).status_code == 401

        # stale timestamp rejected
        ts = str(int(time.time()) - 4000)
        sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
        r = await c.post("/api/luna/sync", content=raw, headers={
            "X-Luna-Tenant": tenant, "X-Luna-Ts": ts, "X-Luna-Signature": sig,
            "Content-Type": "application/json"})
        assert r.status_code == 401

        # 4. unlink last install → certification cleared
        install_row_id = me["installs"][0]["id"]
        r = await c.delete(f"/api/me/luna-installs/{install_row_id}", headers=h)
        assert r.status_code == 200 and r.json()["certified"] is False


async def test_link_code_is_single_account():
    async with _client() as c:
        enroll = (await c.post("/api/luna/enroll", json={"install_id": "second-install-01"})).json()
        h1 = await _signup(c, "owner-a@example.com", "ownera")
        h2 = await _signup(c, "owner-b@example.com", "ownerb")
        assert (await c.post("/api/me/link-luna", json={"code": enroll["link_code"]}, headers=h1)).status_code == 200
        # code consumed — second account cannot use it
        assert (await c.post("/api/me/link-luna", json={"code": enroll["link_code"]}, headers=h2)).status_code == 404
