import time
from urllib.parse import parse_qs, urlparse

import pytest
from authlib.integrations.base_client import OAuthError
from joserfc import jwt
from joserfc.jwk import RSAKey

from testprep import auth, create_app
from testprep.db import connect

MS_ISSUER = "https://login.microsoftonline.com/{tenantid}/v2.0"


def oauth_client(app, provider):
    return app.extensions["testprep_auth"]["oauth"].create_client(provider)


def fake_provider(monkeypatch, app, provider, userinfo=None, error=None, issuer="https://accounts.google.com"):
    """Stub the token exchange so the callback sees these verified ID token claims (or raises error)."""
    client = oauth_client(app, provider)

    def authorize_access_token(**kwargs):
        if error:
            raise error
        return {"access_token": "at", "userinfo": userinfo}

    monkeypatch.setattr(client, "load_server_metadata", lambda: {"issuer": issuer})
    monkeypatch.setattr(client, "authorize_access_token", authorize_access_token)


def google_user(sub="g-1", email="Ana@Example.com", name="Ana", verified=True):
    return {"sub": sub, "email": email, "email_verified": verified, "name": name}


def callback(client, provider="google"):
    return client.get(f"/auth/{provider}/callback?code=c&state=s")


def users(app):
    conn = connect(app.config["DATABASE"])
    rows = conn.execute(
        "SELECT u.id, u.name, i.provider, i.email FROM users u JOIN identities i ON i.user_id = u.id ORDER BY u.id"
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def test_every_page_requires_sign_in(anon_client):
    for path in ["/", "/exams", "/flashcards", "/progress", "/profile", "/exams/1", "/nope"]:
        res = anon_client.get(path)
        assert res.status_code == 302 and urlparse(res.headers["Location"]).path == "/login", path
    res = anon_client.get("/exams?tab=history")
    assert parse_qs(urlparse(res.headers["Location"]).query)["next"] == ["/exams?tab=history"]
    assert anon_client.post("/exams", data={"mode": "random"}).headers["Location"] == "/login"

    res = anon_client.post("/api/exams/1/answer", json={"position": 1, "choice_id": 1})
    assert res.status_code == 401 and "signed out" in res.get_json()["error"]

    page = anon_client.get("/login")
    assert page.status_code == 200
    assert b"Continue with Google" in page.data and b"Continue with Microsoft" in page.data
    assert b"Dashboard" not in page.data  # no app nav before sign-in
    assert anon_client.get("/static/css/app.css").status_code == 200


def test_login_page_without_providers(tmp_path, content_dir, monkeypatch):
    for key in ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET"]:
        monkeypatch.delenv(f"TESTPREP_{key}", raising=False)
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "x.sqlite"), "CONTENT_DIR": str(content_dir),
                      "SECRET_KEY": "test"})
    client = app.test_client()
    assert b"No sign-in providers are configured" in client.get("/login").data
    assert client.get("/auth/google").status_code == 404


def test_settings_come_from_prefixed_env(tmp_path, content_dir, monkeypatch):
    monkeypatch.setenv("TESTPREP_GOOGLE_CLIENT_ID", "from-env")
    monkeypatch.setenv("TESTPREP_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TESTPREP_SESSION_COOKIE_SECURE", "true")
    app = create_app({"DATABASE": str(tmp_path / "x.sqlite"), "CONTENT_DIR": str(content_dir), "SECRET_KEY": "t"})
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.extensions["testprep_auth"]["providers"] == ["google"]
    assert oauth_client(app, "google").client_id == "from-env"


def test_sign_in_creates_profile_once_and_returns_to_next(app, anon_client, monkeypatch):
    fake_provider(monkeypatch, app, "google", google_user())
    with anon_client.session_transaction() as sess:
        sess["next"] = "/progress"
    res = callback(anon_client)
    assert res.status_code == 302 and res.headers["Location"] == "/progress"
    assert users(app) == [(1, "Ana", "google", "ana@example.com")]
    page = anon_client.get("/")
    assert page.status_code == 200 and b"Ana" in page.data

    res = anon_client.post("/logout")
    assert res.headers["Location"] == "/login"
    assert anon_client.get("/").status_code == 302

    assert callback(anon_client).headers["Location"] == "/"  # same account: same profile, default landing page
    assert users(app) == [(1, "Ana", "google", "ana@example.com")]
    assert anon_client.get("/profile").status_code == 200


def test_new_accounts_get_unique_profile_names(app, monkeypatch):
    for sub, provider, issuer in [("g-1", "google", None), ("g-2", "google", None), ("m-1", "microsoft", MS_ISSUER)]:
        claims = {"sub": sub, "name": "Ana", "email": "a@x.com", "email_verified": True}
        fake_provider(monkeypatch, app, provider, claims, issuer=issuer or "https://accounts.google.com")
        callback(app.test_client(), provider)
    assert [u[1] for u in users(app)] == ["Ana", "Ana 2", "Ana 3"]


def test_microsoft_uses_preferred_username_not_email(app, anon_client, monkeypatch):
    claims = {"sub": "m-1", "tid": "t", "name": "Bo", "preferred_username": "Bo@Contoso.com", "email": "spoof@x.com"}
    fake_provider(monkeypatch, app, "microsoft", claims, issuer=MS_ISSUER)
    callback(anon_client, "microsoft")
    assert users(app) == [(1, "Bo", "microsoft", "bo@contoso.com")]


def test_allowed_emails(app, monkeypatch):
    app.config["ALLOWED_EMAILS"] = "boss@example.com, @example.org"
    attempts = [
        (google_user("g-1", "ana@example.com"), False),
        (google_user("g-2", "boss@example.com"), True),
        (google_user("g-3", "dev@example.org"), True),
        (google_user("g-4", "boss@example.com", verified=False), False),  # unverified email doesn't count
    ]
    for claims, allowed in attempts:
        client = app.test_client()
        fake_provider(monkeypatch, app, "google", claims)
        res = callback(client)
        assert (res.headers["Location"] != "/login") is allowed, claims
        assert (client.get("/").status_code == 200) is allowed, claims
    assert [u[3] for u in users(app)] == ["boss@example.com", "dev@example.org"]
    rejected = app.test_client()
    fake_provider(monkeypatch, app, "google", google_user("g-5", "eve@evil.com"))
    callback(rejected)
    assert "isn&#39;t allowed" in rejected.get("/login").get_data(as_text=True)


def test_failed_or_cancelled_sign_in(app, anon_client, monkeypatch):
    fake_provider(monkeypatch, app, "google", error=OAuthError("access_denied"))
    assert callback(anon_client).headers["Location"] == "/login"
    assert b"sign-in didn" in anon_client.get("/login").data
    assert anon_client.get("/").status_code == 302 and users(app) == []
    assert anon_client.get("/auth/github/callback").status_code == 404


def test_session_is_dropped_when_profile_is_deleted(client):
    assert client.get("/").status_code == 200
    res = client.post("/profile", data={"action": "delete"})
    assert res.headers["Location"] == "/login"
    assert client.get("/").status_code == 302


def test_safe_next():
    assert auth._safe_next("/exams?x=1") == "/exams?x=1"
    for bad in [None, "", "https://evil.com/", "//evil.com", "/\\evil.com", "exams"]:
        assert auth._safe_next(bad) is None, bad


def test_allowed_rules():
    assert auth._allowed(None, "") and auth._allowed("a@x.com", None)
    assert auth._allowed("a@x.com", "x.com") and auth._allowed("a@x.com", ["@x.com"])
    assert not auth._allowed("a@xx.com", "x.com") and not auth._allowed(None, "x.com")


@pytest.fixture
def ms_token(app, monkeypatch):
    """Sign real ID tokens for the Microsoft client, checked by Authlib against a local key set."""
    key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    client = oauth_client(app, "microsoft")
    metadata = {"issuer": MS_ISSUER, "jwks": {"keys": [key.as_dict(private=False)]},
                "id_token_signing_alg_values_supported": ["RS256"]}
    monkeypatch.setattr(client, "load_server_metadata", lambda: metadata)

    def parse(**overrides):
        claims = {"iss": "https://login.microsoftonline.com/tenant-1/v2.0", "tid": "tenant-1", "sub": "m-1",
                  "aud": "microsoft-client", "iat": int(time.time()), "exp": int(time.time()) + 600, "nonce": "n"}
        claims.update(overrides)
        token = {"id_token": jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key), "access_token": "at"}
        return client.parse_id_token(token, nonce="n", claims_options=auth._claims_options(client))

    return parse


def test_microsoft_issuer_must_match_token_tenant(ms_token):
    assert ms_token()["sub"] == "m-1"
    with pytest.raises(Exception, match="iss"):
        ms_token(tid="tenant-2")
    with pytest.raises(Exception, match="aud"):
        ms_token(aud="someone-else")
    with pytest.raises(Exception, match="nonce"):
        ms_token(nonce="replayed")


def test_cli_links_account_to_existing_profile(app, monkeypatch):
    conn = connect(app.config["DATABASE"])
    legacy = conn.execute("INSERT INTO users (name, created_at) VALUES ('Vova', '2026-01-01')").lastrowid
    conn.commit()
    conn.close()
    client = app.test_client()
    fake_provider(monkeypatch, app, "google", google_user(name="Vladimir"))
    callback(client)
    client.post("/exams", data={"mode": "random"})  # history built up before linking

    runner = app.test_cli_runner()
    assert "(no account)" in runner.invoke(args=["profiles", "list"]).output
    result = runner.invoke(args=["profiles", "link", "vova", "ana@example.com"])
    assert result.exit_code == 0, result.output
    assert users(app) == [(legacy, "Vova", "google", "ana@example.com")]
    conn = connect(app.config["DATABASE"])
    assert conn.execute("SELECT user_id FROM exams").fetchall()[0][0] == legacy
    conn.close()

    assert client.get("/").status_code == 302  # old profile is gone; signing in again lands in Vova
    callback(client)
    assert b"Vova" in client.get("/").data
    assert runner.invoke(args=["profiles", "link", "Nobody", "ana@example.com"]).exit_code != 0
    assert runner.invoke(args=["profiles", "link", "Vova", "missing@example.com"]).exit_code != 0
