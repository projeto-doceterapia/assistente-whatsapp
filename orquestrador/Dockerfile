# syntax=docker/dockerfile:1

FROM python:3.12.7-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

FROM python:3.12.7-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
RUN useradd --system --no-create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
LABEL org.opencontainers.image.source="https://github.com/projeto-doceterapia/assistente-whatsapp" \
      org.opencontainers.image.title="orquestrador" \
      org.opencontainers.image.description="WhatsApp stock assistant orchestrator"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
