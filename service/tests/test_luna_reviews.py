"""009 C: reviews written from inside a Luna pane — signed, no account.

The pane never redirects the owner to a website that doesn't know them: the
install enrolls once, signs each request, and the marketplace answers with
`is_mine` so the pane can show/edit the owner's own review.
"""

import hashlib
import hmac
import json
import re
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import init_db
from app.main import app
from app.packaging import package_dir_to_zip

REPO = Path(__file__).resolve().parents[2]
HW2 = REPO / "examples" / "hello_world_2"

MP = "luna-review-mp"
PLUGIN = "hello-world-2"
VERSION = "0.1.1"


def _own_artifact() -> bytes:
    """A version of the example plugin unique to this module.

    Artifact bytes are content-addressed and shared across marketplaces, so
    publishing the identical zip another test publishes would make that test's
    purge assertions depend on ordering. Bump the version to get our own bytes.
    """
    tmp = Path(tempfile.mkdtemp(prefix="luna-review-plugin-")) / "hello_world_2"
    shutil.copytree(HW2, tmp)
    manifest = tmp / "luna-plugin.toml"
    manifest.write_text(
        re.sub(r'(?m)^version\s*=.*$', f'version = "{VERSION}"', manifest.read_text())
    )
    return package_dir_to_zip(tmp)


@pytest.fixture(scope="module", autouse=True)
async def _ready():
    await init_db()
    async with _client() as c:
        h = await _signup(c, "lrpub@example.com", "lrpublisher")
        await c.post("/api/orgs", json={"name": "LR Co", "slug": "lr-co"}, headers=h)
        await c.post("/api/orgs/lr-co/marketplaces",
                     json={"name": "LR MP", "slug": MP, "visibility": "public"}, headers=h)
        up = await c.post(f"/api/marketplaces/{MP}/upload",
                          files={"artifact": ("p.zip", _own_artifact(), "application/zip")},
                          headers=h)
        assert up.status_code == 200, up.text
    yield


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _signup(c: AsyncClient, email: str, username: str) -> dict:
    await c.post("/api/auth/signup",
                 json={"email": email, "username": username, "password": "pw12345"})
    token = (await c.post("/api/auth/login", json={
        "email": email, "username": username, "password": "pw12345"})).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _enroll(c: AsyncClient, install_id: str, name: str = "Roy's Luna") -> tuple[str, str]:
    r = await c.post("/api/luna/enroll", json={"install_id": install_id, "luna_name": name})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["tenant"], body["secret"]


async def _signed(c: AsyncClient, tenant: str, secret: str, path: str, payload: dict):
    """Same signing the plugin does in reviews.py::signed_call."""
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return await c.post(path, content=raw, headers={
        "Content-Type": "application/json",
        "X-Luna-Tenant": tenant,
        "X-Luna-Ts": ts,
        "X-Luna-Signature": sig,
    })


async def test_install_writes_reads_edits_deletes():
    async with _client() as c:
        tenant, secret = await _enroll(c, "pane-install-01")
        base = f"/api/luna/reviews/{MP}/{PLUGIN}"

        # no account, no link, no certification — just a signed install
        r = await _signed(c, tenant, secret, base, {
            "rating": 5, "title": "Great", "body": "Works well.",
            "author": "roy", "plugin_version": VERSION})
        assert r.status_code == 200, r.text
        assert r.json()["rating_count"] == 1

        listing = (await _signed(c, tenant, secret, f"{base}/list", {"sort": "recent"})).json()
        assert listing["can_review"] is True
        assert listing["summary"]["count"] == 1
        (row,) = listing["reviews"]
        assert row["is_mine"] is True
        assert row["author"] == "roy"
        # the install had the plugin installed → the badge is earned
        assert row["verified_install"] is True
        assert listing["mine"] == row["id"]

        # editing replaces, one review per install
        r = await _signed(c, tenant, secret, base, {
            "rating": 4, "title": "Solid", "body": "Still good.", "author": "roy",
            "plugin_version": VERSION})
        assert r.status_code == 200
        listing = (await _signed(c, tenant, secret, f"{base}/list", {})).json()
        assert listing["summary"]["count"] == 1 and listing["summary"]["average"] == 4.0
        assert listing["reviews"][0]["edited"] is True

        # a review with no account still renders on the public page
        public = (await c.get(f"/api/catalog/{MP}/{PLUGIN}/reviews")).json()
        assert public["summary"]["count"] == 1
        assert public["reviews"][0]["author"] == "roy"
        assert public["reviews"][0]["is_mine"] is False

        # 1-2 stars need words
        r = await _signed(c, tenant, secret, base, {"rating": 1, "title": "no"})
        assert r.status_code == 400

        # delete is scoped to this install's own review
        assert (await _signed(c, tenant, secret, f"{base}/delete", {})).status_code == 200
        assert (await _signed(c, tenant, secret, f"{base}/delete", {})).status_code == 404
        detail = (await c.get(f"/api/catalog/{MP}/{PLUGIN}")).json()
        assert detail["rating_count"] == 0 and detail["rating_average"] == 0.0


async def test_installs_are_separate_authors():
    async with _client() as c:
        t1, s1 = await _enroll(c, "pane-install-a", "Luna A")
        t2, s2 = await _enroll(c, "pane-install-b", "Luna B")
        base = f"/api/luna/reviews/{MP}/{PLUGIN}"

        # author falls back to the Luna's name when the pane sends none
        await _signed(c, t1, s1, base, {"rating": 5, "body": "from A"})
        await _signed(c, t2, s2, base, {"rating": 3, "body": "from B"})

        a = (await _signed(c, t1, s1, f"{base}/list", {})).json()
        assert a["summary"]["count"] == 2 and a["summary"]["average"] == 4.0
        mine = [r for r in a["reviews"] if r["is_mine"]]
        assert len(mine) == 1 and mine[0]["author"] == "Luna A"
        # written without a plugin_version → not a verified install
        assert mine[0]["verified_install"] is False

        b = (await _signed(c, t2, s2, f"{base}/list", {})).json()
        assert [r for r in b["reviews"] if r["is_mine"]][0]["author"] == "Luna B"

        for tenant, secret in ((t1, s1), (t2, s2)):
            assert (await _signed(c, tenant, secret, f"{base}/delete", {})).status_code == 200


async def test_bad_signature_and_unknown_tenant_rejected():
    async with _client() as c:
        tenant, secret = await _enroll(c, "pane-install-c")
        base = f"/api/luna/reviews/{MP}/{PLUGIN}"
        r = await _signed(c, tenant, "wrong-secret", base, {"rating": 5, "body": "x"})
        assert r.status_code == 401
        r = await _signed(c, "no-such-tenant", secret, f"{base}/list", {})
        assert r.status_code == 401


async def test_publisher_install_cannot_review_own_plugin():
    async with _client() as c:
        h = await _signup(c, "lrpub@example.com", "lrpublisher")
        tenant, secret = await _enroll(c, "pane-install-pub")
        code = (await c.post("/api/luna/enroll",
                             json={"install_id": "pane-install-pub"})).json()["link_code"]
        assert (await c.post("/api/me/link-luna", json={"code": code}, headers=h)).status_code == 200
        r = await _signed(c, tenant, secret, f"/api/luna/reviews/{MP}/{PLUGIN}",
                          {"rating": 5, "body": "my own plugin"})
        assert r.status_code == 403
