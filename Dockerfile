# Yu Notice Bot V2 - Docker Image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
# poppler-utils: Required for PDF processing (pdf2image)
RUN apt-get update && apt-get install -y \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for Docker layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# Healthcheck (optional but recommended for production)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Default command: run once mode for GitHub Actions
# For local development, override with docker-compose
CMD ["python", "main.py", "--once"]
