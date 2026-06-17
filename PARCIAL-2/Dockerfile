FROM python:3.11-slim

WORKDIR /app

COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

ENV GITHUB_BASE_URL=https://models.inference.ai.azure.com
ENV LANGCHAIN_TRACING_V2=false
ENV LANGCHAIN_DISABLED=true

EXPOSE 8000

CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"]
