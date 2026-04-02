FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# /app/data is a persistent volume for SQLite
VOLUME ["/app/data"]

CMD ["python", "bot.py"]
