FROM python:3.12-slim

WORKDIR /app

RUN mkdir -p /app/artifacts /app/data

ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV FLOWCHECK_ARTIFACTS_DIR=/app/artifacts
ENV FLOWCHECK_DATABASE_PATH=/app/data/flowcheck.db

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./backend/

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
