"""Admin commands, e.g. `python -m flask --app ddprep profiles list`."""

from __future__ import annotations

import click
from flask import Flask
from flask.cli import AppGroup

from .db import get_db

HISTORY_TABLES = ("exams", "flashcard_sessions", "flashcard_reviews")

profiles = AppGroup("profiles", help="List profiles and link sign-in accounts to them.")


@profiles.command("list")
def list_profiles() -> None:
    """Show every profile, its exam count and the accounts that sign in to it."""
    rows = get_db().execute(
        "SELECT u.id, u.name, (SELECT COUNT(*) FROM exams e WHERE e.user_id = u.id) AS exams, "
        "GROUP_CONCAT(i.provider || ' ' || COALESCE(i.email, i.subject), ', ') AS accounts "
        "FROM users u LEFT JOIN identities i ON i.user_id = u.id GROUP BY u.id ORDER BY u.id"
    ).fetchall()
    for r in rows:
        click.echo(f"{r['id']:>4}  {r['name']:<40}  {r['exams']:>4} exams  {r['accounts'] or '(no account)'}")


@profiles.command("link")
@click.argument("profile")
@click.argument("email")
def link(profile: str, email: str) -> None:
    """Make the account with EMAIL sign in to PROFILE, e.g. a profile from before sign-in existed.

    Sign in with that account once first. History it built up in its own profile moves to PROFILE,
    and that profile is deleted.
    """
    conn = get_db()
    target = conn.execute("SELECT id, name FROM users WHERE name = ?", (profile,)).fetchone()
    if target is None:
        raise click.ClickException(f"No profile called {profile!r}. See `python -m flask --app ddprep profiles list`.")
    sources = {
        r["user_id"]
        for r in conn.execute("SELECT user_id FROM identities WHERE email = ?", (email.strip().lower(),))
    }
    if not sources:
        raise click.ClickException(f"No account with email {email!r} has signed in yet. Sign in once, then retry.")
    with conn:
        conn.execute("UPDATE identities SET user_id = ? WHERE email = ?", (target["id"], email.strip().lower()))
        for old in sources - {target["id"]}:
            if conn.execute("SELECT 1 FROM identities WHERE user_id = ?", (old,)).fetchone():
                continue  # another account still signs in to it
            for table in HISTORY_TABLES:
                conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (target["id"], old))
            conn.execute("DELETE FROM users WHERE id = ?", (old,))
    click.echo(f"{email} now signs in to {target['name']!r}. Sign out and back in to see it.")


def init_app(app: Flask) -> None:
    app.cli.add_command(profiles)
