# Use multi-stage build for efficiency
FROM --platform=linux/arm64 python:3.11-slim as builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final stage
FROM --platform=linux/arm64 python:3.11-slim

WORKDIR /app

COPY --from=builder /install /usr/local
COPY ./app ./app

# Expose FastAPI port
EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
