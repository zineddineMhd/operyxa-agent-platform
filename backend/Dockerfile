FROM python:3.13-slim
RUN pip install --no-cache-dir uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev && useradd --system --uid 10001 app
USER app
EXPOSE 8000
CMD ["uvicorn", "operyxa.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
