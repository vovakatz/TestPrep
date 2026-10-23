"""TestPrep - certification exam preparation app."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth, cli, db, seed

ROOT = Path(__file__).resolve().parent.parent


def _secret_key(instance_path: Path) -> str:
    key_file = instance_path / "secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32), encoding="utf-8")
    return key_file.read_text(encoding="utf-8").strip()


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_path=str(ROOT / "instance"))
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config.update(
        DATABASE=os.environ.get("TESTPREP_DB", str(Path(app.instance_path) / "testprep.sqlite")),
        CONTENT_DIR=str(ROOT / "content"),
        SESSION_COOKIE_SAMESITE="Lax",
        MICROSOFT_TENANT="common",
    )
    # Any TESTPREP_<KEY> environment variable sets config <KEY>, e.g. TESTPREP_GOOGLE_CLIENT_ID.
    app.config.from_prefixed_env("TESTPREP")
    if config:
        app.config.update(config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _secret_key(Path(app.instance_path))
    if app.config.get("BEHIND_PROXY"):
        # Trust X-Forwarded-Proto/Host from one reverse proxy so sign-in callback URLs use the public address.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    conn = db.connect(app.config["DATABASE"])
    try:
        db.init_schema(conn)
        result = seed.seed(conn, Path(app.config["CONTENT_DIR"]))
        if result:
            app.logger.info(
                "Loaded %(questions)d questions and %(flashcards)d flash cards for %(certifications)d certifications",
                result,
            )
    finally:
        conn.close()

    from .views import bp

    auth.init_app(app)
    cli.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(bp)
    return app
