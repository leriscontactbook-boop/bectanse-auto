FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./

CMD ["sh", "-c", "python -m trading_journal.migrate && exec gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --timeout 180"]
