FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements-runtime.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install -r requirements-runtime.txt
COPY . .

CMD ["uvicorn", "forge.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
