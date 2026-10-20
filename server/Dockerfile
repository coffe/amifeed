FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY amifeed ./amifeed
RUN sed -i 's|^readme = .*||' pyproject.toml \
 && pip install --no-cache-dir . \
 && useradd --uid 1000 --home /data amifeed \
 && mkdir -p /data && chown amifeed /data
USER amifeed
WORKDIR /data
ENV AMIFEED_CONFIG=/config/amifeed.toml
ENTRYPOINT ["amifeed"]
