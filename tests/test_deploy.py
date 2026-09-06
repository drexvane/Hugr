"""The deployment files, checked for the one mistake that cannot be undone.

An image is not a running process you can fix and restart. Whatever is inside it is
inside every registry it was pushed to, every layer cache, and every `docker save`
tarball anyone made — and `order_items` carries customer names, street addresses and
3,340 client IPs. Publishing those once is not reversible by editing a file.

So the tests here are narrow and blunt:

* **no data path is copied into the image**, and `.dockerignore` keeps `data/` and
  `dataset/` out of the build context entirely, so a careless `COPY . .` later would
  still not carry them;
* **the container does not run as root**, and does not bind a port on every interface
  when it is brought up locally;
* **no secret is in any of the three files** — they name environment variables and
  read values from the platform.

Nothing here builds an image. Docker is not available in CI and is not needed: every
claim above is a property of the text, and the text is what gets committed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO / "Dockerfile"
DOCKERIGNORE = REPO / ".dockerignore"
COMPOSE = REPO / "docker-compose.yml"

# The directories that hold the real extract and every snapshot built from it.
DATA_PATHS = ("data/", "dataset/", "data/versions", "data/clean", "data/raw")


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ignored() -> list[str]:
    return [line.strip() for line in
            DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


@pytest.fixture(scope="module")
def compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def copied(dockerfile: str) -> list[str]:
    """Every source argument of every COPY and ADD instruction."""
    out: list[str] = []
    for line in dockerfile.splitlines():
        line = line.strip()
        if not re.match(r"(?i)^(copy|add)\s", line):
            continue
        parts = [p for p in line.split()[1:] if not p.startswith("--")]
        out += parts[:-1]          # the last argument is the destination
    return out


# --------------------------------------------------------------------------- #
# the mistake that cannot be undone
# --------------------------------------------------------------------------- #

def test_the_image_copies_no_data(dockerfile):
    sources = copied(dockerfile)
    assert sources, "the parse found no COPY at all, so it proved nothing"
    for source in sources:
        for path in DATA_PATHS:
            assert not source.startswith(path.rstrip("/")), (source, path)
    # And nothing sweeping, which is the shape that carries data by accident.
    assert "." not in sources and "./" not in sources


def test_the_build_context_excludes_the_data_directories(ignored):
    for path in ("data/", "dataset/"):
        assert path in ignored, path


def test_the_build_context_excludes_the_secrets_it_should(ignored):
    for path in (".env", ".env.*", "*.key", "credentials.json"):
        assert path in ignored, path
    # The example is the one env file that may travel: it holds names, not values.
    assert "!.env.example" in ignored


def test_the_snapshot_arrives_as_a_read_only_mount(compose):
    assert "./data/versions:/app/data/versions:ro" in compose


def test_no_deployment_file_contains_a_secret(dockerfile, compose):
    for text, name in ((dockerfile, "Dockerfile"), (compose, "docker-compose.yml"),
                       (DOCKERIGNORE.read_text(encoding="utf-8"), ".dockerignore")):
        # An assignment with a non-empty literal on the right is a value; a `${VAR}`
        # or an empty default is a name, which is the whole point.
        for match in re.finditer(r"(?m)^\s*-?\s*(?:ENV\s+)?"
                                 r"(ANTHROPIC_API_KEY|DTP_DASHBOARD_PASSWORD"
                                 r"[A-Z_]*)\s*[:=]\s*(.*)$", text):
            value = match.group(2).strip().strip('"\'')
            assert value in ("", "${" + match.group(1) + ":-}"), (name, match.group(0))


# --------------------------------------------------------------------------- #
# how it runs
# --------------------------------------------------------------------------- #

def test_the_container_does_not_run_as_root(dockerfile):
    users = re.findall(r"(?im)^USER\s+(\S+)", dockerfile)
    assert users, "no USER instruction, so it runs as root"
    assert users[-1] not in ("root", "0")


def test_the_local_compose_binds_loopback_only(compose):
    # `- "8501:8501"` publishes on every interface the machine has, which on a laptop
    # on a conference network is the accident this whole file is about.
    published = re.findall(r'(?m)^\s*-\s*"([^"]+:)?\d+:\d+"', compose)
    assert published, "no port mapping found, so nothing was checked"
    assert all(prefix == "127.0.0.1:" for prefix in published), published


def test_streamlit_is_told_to_bind_inside_the_container(dockerfile):
    # 0.0.0.0 *inside* a container is right - the loopback there is not reachable -
    # and is only safe because the mapping above decides what is exposed.
    assert "--server.address=0.0.0.0" in dockerfile
    assert "--server.headless=true" in dockerfile


def test_telemetry_is_off(dockerfile):
    # Sending usage statistics out of a deployment of this data is not a decision a
    # Dockerfile gets to make silently.
    assert "--browser.gatherUsageStats=false" in dockerfile


def test_there_is_a_health_check(dockerfile):
    assert "HEALTHCHECK" in dockerfile
    assert "_stcore/health" in dockerfile


def test_the_dependencies_are_installed_from_the_pinned_file(dockerfile):
    # `pip install .` would resolve fresh versions at build time, so an image built
    # next month would not be the one that was tested. pandas 3.x in particular.
    assert "COPY requirements.txt" in dockerfile
    assert "pip install -r requirements.txt" in dockerfile


def test_the_python_version_matches_what_the_project_requires(dockerfile):
    requires = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12"' in requires
    assert re.search(r"(?im)^FROM python:3\.1[2-9]", dockerfile)


# --------------------------------------------------------------------------- #
# and that the docs say what this is not
# --------------------------------------------------------------------------- #

def test_the_dockerfile_points_at_the_launch_argument(dockerfile):
    assert "docs/04-qa-and-launch.md" in dockerfile
    assert "not identity" in dockerfile


def test_the_launch_doc_carries_the_deployment_section():
    doc = (REPO / "docs" / "04-qa-and-launch.md").read_text(encoding="utf-8")
    assert "## Deploying it" in doc
    for part in ("Dockerfile", "docker compose", "reverse proxy"):
        assert part in doc, part
