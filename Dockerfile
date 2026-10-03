FROM python:3.12.10-slim
WORKDIR /app
COPY requirements.txt requirements.lock.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY src ./src
COPY scripts ./scripts
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
CMD ["uvicorn", "da_agent.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
