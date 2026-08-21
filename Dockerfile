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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
