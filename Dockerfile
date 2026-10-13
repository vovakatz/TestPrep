FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt gunicorn

COPY ddprep/ ddprep/
COPY content/ content/
COPY app.py .

# instance/ holds the SQLite DB and the session secret key; mount it (see compose.yaml) to keep progress.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p instance \
    && chown app:app instance
USER app

EXPOSE 5000

# One worker: the app seeds SQLite on startup and the DB isn't built for concurrent writers.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "ddprep:create_app()"]
