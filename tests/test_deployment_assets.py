from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_compose_exposes_only_nginx_and_protects_repository_mount():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert set(services) == {"volume-init", "redis", "api", "nginx"}
    assert "ports" not in services["api"]
    assert "ports" not in services["redis"]
    assert services["nginx"]["ports"] == [
        "${DEVPILOT_BIND_ADDRESS:-127.0.0.1}:${DEVPILOT_HTTP_PORT:-8080}:8080"
    ]
    assert services["api"]["build"]["target"] == (
        "runtime-${DEVPILOT_TOOLCHAIN_PROFILE:-python}"
    )
    repository_mount = next(
        item
        for item in services["api"]["volumes"]
        if isinstance(item, dict) and item.get("target") == "/repos"
    )
    assert repository_mount["read_only"] is True
    assert services["api"]["environment"]["DEVPILOT_DATA_DIR"] == "/data"
    assert services["api"]["environment"]["DEVPILOT_API_REPOSITORY_ROOTS"] == (
        '["/repos"]'
    )


def test_api_dockerfile_has_non_root_python_and_full_toolchain_targets():
    dockerfile = (ROOT / "docker" / "api" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "FROM runtime-base AS runtime-python" in dockerfile
    assert "FROM runtime-base AS runtime-full" in dockerfile
    assert "USER devpilot" in dockerfile
    for executable in (
        "node-toolchain",
        "go-toolchain",
        "rust-toolchain",
        "gradle-toolchain",
        "maven-toolchain",
    ):
        assert executable in dockerfile
    assert "python:3.13-slim-trixie" in dockerfile
    assert "maven:3.9-eclipse-temurin-17" in dockerfile
    assert "/opt/java/openjdk" in dockerfile
    assert "ca-certificates git procps" in dockerfile


def test_nginx_supports_spa_websocket_and_query_safe_access_logs():
    config = (ROOT / "docker" / "nginx" / "default.conf").read_text(
        encoding="utf-8"
    )

    assert "try_files $uri $uri/ /index.html" in config
    assert "proxy_set_header Upgrade $http_upgrade" in config
    assert "proxy_read_timeout 1800s" in config
    assert "proxy_read_timeout 3600s" in config
    assert '"$request_method $uri $server_protocol"' in config
    assert "$request_uri" not in config
    assert "$args" not in config


def test_docker_environment_example_uses_safe_local_defaults():
    example = (ROOT / ".env.docker.example").read_text(encoding="utf-8")

    assert "DEVPILOT_BIND_ADDRESS=127.0.0.1" in example
    assert "DEVPILOT_TOOLCHAIN_PROFILE=python" in example
    assert "DEVPILOT_REPOSITORY_ROOT_HOST=" in example
    assert ".env\n" in (ROOT / ".gitignore").read_text(encoding="utf-8")
