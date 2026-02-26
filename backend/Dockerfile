FROM python:3.13-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright + Chromium with all required system libraries
RUN playwright install --with-deps chromium

# Copy source
COPY . .

# Render injects $PORT at runtime; default to 8000 for local Docker use
ENV PORT=8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
