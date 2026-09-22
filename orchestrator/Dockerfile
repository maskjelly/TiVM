FROM python:3.14-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn requests

COPY . /app/orchestrator
ENV PYTHONUNBUFFERED=1
EXPOSE 6090
CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "6090", "--log-level", "warning"]
