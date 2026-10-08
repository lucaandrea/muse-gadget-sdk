FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home muse && mkdir /data && chown muse /data
USER muse
ENV MUSE_DATA_DIR=/data
EXPOSE 8765
CMD ["muse-companion", "serve", "--host", "0.0.0.0", "--port", "8765"]
