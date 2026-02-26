FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Reinstall chromium to match the pinned playwright version
RUN playwright install chromium

COPY . .

ENV PORT=8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
