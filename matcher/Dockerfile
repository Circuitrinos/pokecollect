FROM python:3.12-slim

# tesseract: reads collector numbers to break ties between reprints
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY tools/ ./tools/

# Same uid as the rest of PokéCollect; /data must be writable by it.
RUN groupadd --system --gid 3010 pokecollect \
 && useradd --system --uid 3010 --gid 3010 --no-create-home pokecollect
USER 3010:3010

EXPOSE 8000
# One worker on purpose: the index (~800 MB) lives in process memory.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
