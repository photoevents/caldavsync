# CalDAVSync — container image
# Runs the sync as a long-lived process (daemon mode) by default.
#
# NOTE: First-time Google OAuth needs a browser, which a container doesn't
# have. Authorize once on a desktop (`python main.py --once`) to generate
# token.json, then mount that token into the container. It auto-refreshes,
# so the container never needs a browser afterwards.

FROM python:3.13-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code only (secrets/state are mounted at runtime — see .dockerignore).
COPY *.py ./

ENV PYTHONUNBUFFERED=1

# Override CMD to change mode, e.g. `--once` or `--once --dry-run`.
ENTRYPOINT ["python", "main.py"]
CMD ["--daemon"]
