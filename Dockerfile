FROM python:3.11-slim

WORKDIR /app

# Install gettext for locale compilation
RUN apt-get update && apt-get install -y --no-install-recommends gettext && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Compile locales (non-fatal if it fails)
RUN bash compile_locales.sh || true

CMD ["python3", "start.py"]
