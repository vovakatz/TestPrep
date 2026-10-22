"""Sign-in with Google or Microsoft (OpenID Connect).

Every page except the sign-in flow needs a signed-in user. Each account gets its own profile: the first
sign-in creates one, later sign-ins with the same provider account reuse it.
"""

from __future__ import annotations

from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .db import get_db, now

bp = Blueprint("auth", __name__)

# name -> (button label, OpenID discovery URL). A provider is enabled once its client id and secret are set.
PROVIDERS = {
    "google": ("Google", "https://accounts.google.com/.well-known/openid-configuration"),
    "microsoft": ("Microsoft", "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"),
}
PUBLIC_ENDPOINTS = {"auth.login", "auth.start", "auth.callback", "static"}
NAME_MAX = 40


def init_app(app: Flask) -> None:
    oauth = OAuth(app)
    enabled = []
    for name, (_label, metadata_url) in PROVIDERS.items():
        client_id = app.config.get(f"{name.upper()}_CLIENT_ID")
        client_secret = app.config.get(f"{name.upper()}_CLIENT_SECRET")
        if not (client_id and client_secret):
            continue
        oauth.register(
            name,
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url=metadata_url.format(tenant=app.config.get("MICROSOFT_TENANT") or "common"),
            client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
        )
        enabled.append(name)
    if not enabled:
        app.logger.warning("No sign-in providers are configured, so nobody can sign in. See README, Sign-in.")
    app.extensions["testprep_auth"] = {"oauth": oauth, "providers": enabled}


def _client(provider: str):
    ext = current_app.extensions["testprep_auth"]
    if provider not in ext["providers"]:
        abort(404)
    return ext["oauth"].create_client(provider)


def _safe_next(target: str | None) -> str | None:
    """Only follow same-site paths after sign-in, never another host."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None


def _claims_options(client) -> dict:
    issuer = client.load_server_metadata()["issuer"]

    def valid_issuer(claims, value) -> bool:
        # Microsoft's multi-tenant endpoints publish the issuer as ".../{tenantid}/v2.0".
        return value == issuer.replace("{tenantid}", str(claims.get("tid", "")))

    return {"iss": {"essential": True, "validate": valid_issuer}, "aud": {"essential": True, "value": client.client_id}}


def _identity(provider: str, claims: dict) -> tuple[str | None, str | None, str]:
    """(subject, email, display name) from verified ID token claims."""
    if provider == "microsoft":
        # Microsoft doesn't verify the optional `email` claim; `preferred_username` is the sign-in name.
        email = claims.get("preferred_username") or claims.get("email")
    else:
        email = claims.get("email") if claims.get("email_verified") else None
    name = (claims.get("name") or (email or "").split("@")[0] or "Learner").strip()
    return claims.get("sub"), (email or "").strip().lower() or None, name


def _allowed(email: str | None, rules) -> bool:
    """ALLOWED_EMAILS: comma-separated addresses and/or domains. Empty means any account may sign in."""
    if isinstance(rules, str):
        rules = rules.split(",")
    rules = {r.strip().lower().lstrip("@") for r in rules or [] if r.strip()}
    if not rules:
        return True
    return bool(email) and (email in rules or email.rsplit("@", 1)[-1] in rules)


def _unique_name(conn, name: str) -> str:
    base = name[:NAME_MAX]
    candidate, n = base, 2
    while conn.execute("SELECT 1 FROM users WHERE name = ?", (candidate,)).fetchone():
        suffix = f" {n}"
        candidate, n = base[: NAME_MAX - len(suffix)] + suffix, n + 1
    return candidate


def sign_in(conn, provider: str, subject: str, email: str | None, name: str) -> int:
    """Return the profile linked to this account, creating it on first sign-in."""
    row = conn.execute(
        "SELECT user_id FROM identities WHERE provider = ? AND subject = ?", (provider, subject)
    ).fetchone()
    if row:
        user_id = row["user_id"]
        conn.execute(
            "UPDATE identities SET email = ?, last_login_at = ? WHERE provider = ? AND subject = ?",
            (email, now(), provider, subject),
        )
    else:
        user_id = conn.execute(
            "INSERT INTO users (name, created_at) VALUES (?, ?)", (_unique_name(conn, name), now())
        ).lastrowid
        conn.execute(
            "INSERT INTO identities (user_id, provider, subject, email, created_at, last_login_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, provider, subject, email, now(), now()),
        )
    conn.commit()
    return user_id


@bp.before_app_request
def require_login():
    """Load the signed-in user into g.user and send everyone else to the sign-in page."""
    user_id = session.get("user_id")
    g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() if user_id else None
    if g.user is None and user_id is not None:
        session.clear()  # the profile was deleted
    if g.user is not None or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "You've been signed out. Reload the page to sign in again."}), 401
    next_url = request.full_path.rstrip("?") if request.method == "GET" else None
    return redirect(url_for("auth.login", next=next_url))


@bp.route("/login")
def login():
    next_url = _safe_next(request.args.get("next"))
    if g.user is not None:
        return redirect(next_url or url_for("main.dashboard"))
    providers = [{"name": n, "label": PROVIDERS[n][0]} for n in current_app.extensions["testprep_auth"]["providers"]]
    return render_template("login.html", providers=providers, next_url=next_url)


@bp.route("/auth/<provider>")
def start(provider: str):
    client = _client(provider)
    session["next"] = _safe_next(request.args.get("next"))
    try:
        return client.authorize_redirect(
            url_for("auth.callback", provider=provider, _external=True), prompt="select_account"
        )
    except Exception:  # discovery document unreachable
        current_app.logger.exception("Couldn't start %s sign-in", provider)
        flash(f"Couldn't reach {PROVIDERS[provider][0]}. Please try again in a moment.", "error")
        return redirect(url_for("auth.login"))


@bp.route("/auth/<provider>/callback")
def callback(provider: str):
    client = _client(provider)
    label = PROVIDERS[provider][0]
    try:
        token = client.authorize_access_token(claims_options=_claims_options(client))
    except Exception as exc:  # cancelled, state mismatch, invalid token or network: Authlib, joserfc and requests
        current_app.logger.warning("%s sign-in failed: %r", label, exc)
        flash(f"{label} sign-in didn't complete. Please try again.", "error")
        return redirect(url_for("auth.login"))

    subject, email, name = _identity(provider, token.get("userinfo") or {})
    if not subject:
        flash(f"{label} didn't return an account id. Please try again.", "error")
        return redirect(url_for("auth.login"))
    if not _allowed(email, current_app.config.get("ALLOWED_EMAILS")):
        current_app.logger.warning("Rejected %s sign-in for %s", label, email or f"subject {subject}")
        flash(f"{email or 'This account'} isn't allowed to use TestPrep.", "error")
        return redirect(url_for("auth.login"))

    next_url = session.get("next")
    user_id = sign_in(get_db(), provider, subject, email, name)
    session.clear()  # start a fresh session on every sign-in
    session.permanent = True
    session["user_id"] = user_id
    current_app.logger.info("%s signed in with %s", email or f"subject {subject}", label)
    return redirect(next_url or url_for("main.dashboard"))


@bp.post("/logout")
def logout():
    session.clear()
    flash("You've signed out.", "success")
    return redirect(url_for("auth.login"))
