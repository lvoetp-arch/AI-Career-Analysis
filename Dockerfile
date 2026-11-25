FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY backend/app/ backend/app/
COPY frontend/ frontend/
COPY scripts/start_railway.py scripts/start_railway.py

RUN useradd --uid 10001 --no-create-home career \
    && chown -R career:career /app/backend /app/frontend /app/scripts
USER career
CMD ["python", "scripts/start_railway.py"]
