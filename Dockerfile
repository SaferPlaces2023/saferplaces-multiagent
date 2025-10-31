FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# strumenti di build se servono wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    libexpat1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# venv dedicata
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# install deps (sfrutta cache docker)
COPY . /app
RUN /venv/bin/pip install --upgrade pip && \
    /venv/bin/pip install gunicorn && \
    /venv/bin/pip install '.[dev,leafmap]'

ENV PYTHONPATH="/app"

# utente non-root
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app /venv
USER appuser

# config gunicorn via env
ENV GUNICORN_WORKERS=3 \
    GUNICORN_THREADS=2 \
    GUNICORN_BIND="0.0.0.0:80" \
    LOG_LEVEL=info

EXPOSE 80

# usa il path assoluto della venv: evita problemi di PATH
CMD ["/venv/bin/gunicorn", "saferplaces_agent.agent_interface.flask_server.prod.wsgi:app", "-c", "src/saferplaces_agent/agent_interface/flask_server/prod/gunicorn.conf.py"]
