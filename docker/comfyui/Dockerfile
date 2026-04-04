FROM python:3.12-slim

RUN pip install --no-cache-dir huggingface_hub

COPY download_models.py /app/download_models.py

WORKDIR /app
ENTRYPOINT ["python3", "download_models.py"]
