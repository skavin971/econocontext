FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
ENV ECONOCONTEXT_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "econocontext.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
