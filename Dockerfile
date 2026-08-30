FROM python:3.11-slim

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini .
COPY scripts/ ./scripts/

RUN chmod +x ./scripts/entrypoint.sh && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000

ENTRYPOINT ["./scripts/entrypoint.sh"]
# Shell form so ${PORT} expands at runtime: Render assigns the port and expects
# the process to bind to it, while compose and local runs want the fixed 8000.
# The inner `exec` keeps uvicorn as PID 1 so it still receives stop signals.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
