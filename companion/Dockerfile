FROM python:3.12-slim
WORKDIR /app
COPY requirements-cloud.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-cloud.txt
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home muse && mkdir /data && chown muse /data
USER muse
ENV MUSE_DATA_DIR=/data MUSE_HOSTED=1
EXPOSE 8765
CMD ["muse-companion", "serve", "--host", "0.0.0.0"]
