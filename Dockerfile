FROM python:3.12-slim

# Install FFmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py client.py ./

EXPOSE 5000

# Default entrypoint runs python so arguments can specify server.py or client.py
ENTRYPOINT ["python3"]
CMD ["server.py", "--help"]
