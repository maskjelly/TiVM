FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app/orchestrator
ENV PYTHONUNBUFFERED=1
EXPOSE 6090
CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "6090", "--log-level", "warning"]
