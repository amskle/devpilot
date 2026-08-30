# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.13-slim-trixie
ARG NODE_IMAGE=node:22-bookworm-slim
ARG GO_IMAGE=golang:1.24-bookworm
ARG RUST_IMAGE=rust:1.85-bookworm
ARG GRADLE_IMAGE=gradle:8.14.3-jdk17
ARG MAVEN_IMAGE=maven:3.9-eclipse-temurin-17

FROM ${NODE_IMAGE} AS node-toolchain
FROM ${GO_IMAGE} AS go-toolchain
FROM ${RUST_IMAGE} AS rust-toolchain
FROM ${GRADLE_IMAGE} AS gradle-toolchain
FROM ${MAVEN_IMAGE} AS maven-toolchain

FROM ${PYTHON_IMAGE} AS runtime-base

ARG DEVPILOT_UID=10001
ARG DEVPILOT_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/devpilot

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git procps \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${DEVPILOT_GID}" devpilot \
    && useradd --uid "${DEVPILOT_UID}" --gid "${DEVPILOT_GID}" \
       --create-home --shell /usr/sbin/nologin devpilot \
    && mkdir -p /data /repos \
    && chown -R devpilot:devpilot /data /home/devpilot \
    && git config --system --add safe.directory '/repos/*'

WORKDIR /app

COPY requirements.lock pyproject.toml ./
RUN python -m pip install --no-cache-dir -r requirements.lock

COPY devpilot ./devpilot
COPY runtime ./runtime
COPY shared_state ./shared_state
COPY skills ./skills
RUN python -m pip install --no-cache-dir --no-deps -e .

USER devpilot
EXPOSE 8000

CMD ["python", "-m", "devpilot", "api", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM runtime-base AS runtime-python

FROM runtime-base AS runtime-full

USER root

COPY --from=node-toolchain /usr/local/ /usr/local/
COPY --from=go-toolchain /usr/local/go/ /usr/local/go/
COPY --from=rust-toolchain /usr/local/cargo/ /usr/local/cargo/
COPY --from=rust-toolchain /usr/local/rustup/ /usr/local/rustup/
COPY --from=gradle-toolchain /opt/gradle/ /opt/gradle/
COPY --from=maven-toolchain /opt/java/openjdk/ /opt/java/openjdk/
COPY --from=maven-toolchain /usr/share/maven/ /usr/share/maven/

ENV JAVA_HOME=/opt/java/openjdk \
    CARGO_HOME=/home/devpilot/.cargo \
    RUSTUP_HOME=/usr/local/rustup \
    GOPATH=/home/devpilot/go \
    GRADLE_USER_HOME=/home/devpilot/.gradle \
    MAVEN_CONFIG=/home/devpilot/.m2 \
    npm_config_cache=/home/devpilot/.npm \
    PATH=/opt/java/openjdk/bin:/usr/share/maven/bin:/opt/gradle/bin:/usr/local/go/bin:/usr/local/cargo/bin:${PATH}

RUN mkdir -p \
      /home/devpilot/.cargo \
      /home/devpilot/.gradle \
      /home/devpilot/.m2 \
      /home/devpilot/.npm \
      /home/devpilot/go \
    && chown -R devpilot:devpilot /home/devpilot

USER devpilot
