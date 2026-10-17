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

CMD ["python", "-m", "scripts.analyze", "Compare BTC e ETH nos últimos 30 dias", "--assets", "BTC", "ETH", "--period", "últimos 30 dias"]
