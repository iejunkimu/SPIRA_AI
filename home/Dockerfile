FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
ENV HOME_DB=/state/home.db WORLD_DB=/state/world.db
CMD ["uvicorn", "spira_home.app:app", "--host", "0.0.0.0", "--port", "8710"]
