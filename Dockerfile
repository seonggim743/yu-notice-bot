# Yu Notice Bot V2 - Docker Image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
# System dependencies:
# - poppler-utils: Useful for PDF tools (optional now but good to have)
# - libreoffice: For converting HWP/DOCX/XLSX to PDF
# - fonts-nanum: Korean fonts for correct rendering
# - curl: For downloading files if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libreoffice \
    libreoffice-l10n-ko \
    fonts-nanum \
    libxml2-utils \
    default-jre \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for Docker layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install Playwright browsers (Chromium only for efficiency)
RUN mkdir -p /ms-playwright
RUN playwright install chromium
RUN playwright install-deps chromium

# NOTE: We do NOT copy source code here.
# This image is intended to be a "runtime environment" for GitHub Actions.
# The source code will be mounted or checked out at runtime.

