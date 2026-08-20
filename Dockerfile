FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .

RUN mkdir -p /app/data && chown -R 65532:65532 /app/data

USER 65532:65532
CMD ["python", "bot.py"]
