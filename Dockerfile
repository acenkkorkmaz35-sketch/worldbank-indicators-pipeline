FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
RUN pip install -e ".[dev]" -c constraints.txt

COPY config ./config
COPY dbt ./dbt
COPY tests ./tests
COPY powerbi ./powerbi
COPY output/powerbi ./output/powerbi
ENV WB_PIPELINE_HOME=/app

# The warehouse lives in /app/data and reports in /app/output; mount them to keep results.
ENTRYPOINT ["wb-pipeline"]
CMD ["run"]
