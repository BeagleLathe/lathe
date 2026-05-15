FROM python:3.12-slim
RUN test -z "$(command -v rg)" || (echo "system rg present, aborting" && exit 1)
WORKDIR /work
COPY . /work
RUN pip install --no-cache-dir .[dev]
RUN pytest tests/test_resolver.py -v
