FROM python:3.11-slim

WORKDIR /app

# Install from pre-downloaded wheels (no internet needed inside container)
COPY requirements.txt .
COPY wheels/ /wheels/
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt

# Copy source
COPY . .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
