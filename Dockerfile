# ============================================================================
# RatePath (ريت باث) — 24/7 Autonomous Cloud Worker (Hugging Face Spaces)
# Python 3.11 Slim • Non-Root UID 1000 • Port 7860 (/health & /live_feed.json)
# ============================================================================

FROM python:3.11-slim

ENV PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

# Create non-root user (UID 1000 required by Hugging Face Spaces Docker runtime)
RUN useradd -m -u 1000 user

WORKDIR /app

# Install lightweight banking-grade Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy contract schema, backend engine, and public feed target directory
COPY --chown=user:user shared /app/shared
COPY --chown=user:user backend /app/backend
COPY --chown=user:user frontend/public /app/frontend/public

USER user

EXPOSE 7860

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/health', timeout=5)" || exit 1

CMD ["python", "-m", "backend.cloud_worker"]
