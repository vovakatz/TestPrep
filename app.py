"""Start the DDPrep web app.

    python app.py                 # http://127.0.0.1:5000
    python app.py --port 8080 --open
"""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

from ddprep import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Datadog Fundamentals exam prep")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--open", action="store_true", help="open the app in your browser")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--db", help="SQLite file to use (default: instance/ddprep.sqlite)")
    parser.add_argument("--content", help="content directory to load (default: content/)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = {}
    if args.db:
        config["DATABASE"] = args.db
    if args.content:
        config["CONTENT_DIR"] = args.content
    app = create_app(config)
    if args.open:
        threading.Timer(1.0, webbrowser.open, [f"http://{args.host}:{args.port}/"]).start()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
