ARG PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/proxypool
COPY requirements/service.txt /tmp/service.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: --index-url https://pypi.org/simple -r /tmp/service.txt \
    && groupadd --gid 10001 proxypool \
    && useradd --uid 10001 --gid 10001 --no-create-home proxypool
COPY apps ./apps
COPY integrations ./integrations
COPY tools ./tools
COPY requirements/tasks.txt ./requirements/tasks.txt
USER 10001:10001
EXPOSE 13004
CMD ["python", "-B", "-m", "apps.proxypool.service.main", "--config", "/config/config.json"]
