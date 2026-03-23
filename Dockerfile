# ──────────────────────────────────────────────────────────────────────────────
# SaferPlaces Multiagent — Flask interface
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Build tools needed for native extensions (e.g. triangle, rasterio fallback)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

# Copy package metadata first so dep-install layer is cached independently
COPY pyproject.toml README.md ./

# Copy source tree
COPY src/ ./src/

# Frontend static files are mounted at runtime via docker-compose volume.
# Create the mount point so the path exists even without the volume.
RUN mkdir -p /app/frontend

# Install package with all optional extras + gunicorn (prod WSGI server)
RUN pip install --no-cache-dir -e ".[leafmap,cesium]" gunicorn

EXPOSE 5000

# Gunicorn reads its config (workers, threads, timeout…) from the file below.
# Override individual settings via environment variables — see gunicorn.conf.py.
CMD ["gunicorn", \
     "--config", "src/saferplaces_multiagent/agent_interface/flask_server/prod/gunicorn.conf.py", \
     "saferplaces_multiagent.agent_interface.flask_server.prod.wsgi:app"]
