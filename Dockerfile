FROM python:3.11-slim

WORKDIR /app

# Unbuffered stdout/stderr so Python output is flushed immediately to
# Railway's log stream. Without this, a crash during module import (e.g.
# a CREATE TABLE failure during db.ensure_table()) produces a traceback
# that sits in the 4KB block buffer and is lost when the process exits —
# Railway sees only "Attempt #N failed with service unavailable" and no
# clue what went wrong. The cost is essentially zero (Python flushes on
# every newline instead of every 4KB) and the debuggability gain is
# huge, so this belongs in every long-running Python container.
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# gettext / locales/ removed in Layer 5 — see core/locales.py.
# The translator is now a passthrough stub, so no build-time
# compilation step is needed.

CMD ["python3", "start.py"]
