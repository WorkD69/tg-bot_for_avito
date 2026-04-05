FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /backups

COPY backup.py /backup.py

CMD ["python3", "/backup.py"]
