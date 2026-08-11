FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim

RUN groupadd -r -g 10001 appuser && useradd -r -u 10001 -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# CACHE_BUST changes per commit (set by dev/cli/images.sh) so the source COPY+build is not served from a stale layer.
ARG CACHE_BUST=dev
COPY app/ ./app/
COPY alembic.ini .

USER 10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
