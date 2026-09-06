# A container for the dashboard. Deliberately does *not* contain any data.
#
# The clean tables carry customer names, street addresses and 3,340 client IPs, so
# baking a snapshot into an image would put them in every registry, layer cache and
# `docker save` tarball that image ever touches - somewhere nobody is auditing and
# nothing can revoke. `.dockerignore` excludes `data/` and `dataset/`, and a test
# fails if this file starts copying them.
#
# The snapshot arrives as a read-only mount at runtime:
#
#   docker build -t dtp-dashboard .
#   docker run --rm -p 8501:8501 \
#     -v "$PWD/data/versions:/app/data/versions:ro" \
#     -e DTP_DASHBOARD_PASSWORD="$(cat secret.txt)" \
#     dtp-dashboard
#
# Read `docs/04-qa-and-launch.md` before putting this anywhere public. The password
# gate keeps a passer-by out; it is not identity, and this data wants more than that.

FROM python:3.12-slim AS base

# PYTHONDONTWRITEBYTECODE: the image is read-only in practice, so .pyc files are
# layer weight and nothing else. PYTHONUNBUFFERED so container logs appear as they
# happen rather than when a buffer fills.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, from the pinned file, so a code change does not reinstall
# pandas. `requirements.txt` is the pinned set; `pip install -e .` below adds only the
# package metadata and the console script.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY dashboard/ ./dashboard/
COPY config/ ./config/
RUN pip install --no-deps -e .

# Non-root, and it owns nothing it does not need to write. The snapshot mount is
# read-only; Streamlit needs a writable home for its own state.
RUN useradd --create-home --uid 10001 dtp \
    && mkdir -p /app/data/versions \
    && chown -R dtp:dtp /home/dtp
USER dtp

EXPOSE 8501

# Streamlit's own endpoint, so an orchestrator can tell "starting" from "wedged".
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u; \
u.urlopen('http://127.0.0.1:8501/_stcore/health').read()"

# 0.0.0.0 because the container's loopback is not reachable from outside it.
# headless suppresses the browser launch and the e-mail prompt; usage stats off
# because sending telemetry from a deployment of this data is not a decision this
# file gets to make.
CMD ["streamlit", "run", "dashboard/app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
