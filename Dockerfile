FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home appuser
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=appuser:appuser . .
RUN chown appuser:appuser /app

USER appuser

EXPOSE 8000

# Aplicação HTTP (FastAPI). Documentação interativa em /docs.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
