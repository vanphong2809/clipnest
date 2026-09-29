FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home appuser
COPY --chown=appuser:appuser main.py .
USER appuser
EXPOSE 8000
# Một worker: job, semaphore và rate limiter nằm trong bộ nhớ tiến trình.
# Không tin X-Forwarded-For chưa xác thực; xem README khi triển khai sau proxy.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-proxy-headers"]
