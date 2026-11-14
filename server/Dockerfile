FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
COPY spira_server ./spira_server
RUN pip install --no-cache-dir -e .
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
EXPOSE 8000
CMD ["uvicorn", "spira_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
