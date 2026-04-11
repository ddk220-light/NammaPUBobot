FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# gettext / locales/ removed in Layer 5 — see core/locales.py.
# The translator is now a passthrough stub, so no build-time
# compilation step is needed.

CMD ["python3", "start.py"]
