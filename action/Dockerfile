# The image the GitHub Action runs in.
#
# Every version below is pinned to an exact release. That is the whole point of shipping
# a container for this: a scanner whose results change because a dependency published a
# new default is a scanner nobody can bisect against. When one of these moves, it moves
# in a commit, with a CHANGELOG line, and the integration workflow runs against it first.
#
# The versions here and in `.github/workflows/integration.yml` are meant to match. They
# are two files because one is an image and the other is a runner, and nothing enforces
# it -- so if you change one, change the other.
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

ARG TERRAFORM_VERSION=1.15.8
ARG OPA_VERSION=v1.20.1
ARG CHECKOV_VERSION=3.3.15

# `ca-certificates` because terraform talks to the registry over TLS; `git` because
# terraform modules are frequently git sources; `curl` and `unzip` to fetch the two
# binaries below. Nothing else -- an image with a compiler in it is a larger thing to
# trust than this job needs.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl git unzip \
    && rm -rf /var/lib/apt/lists/*

# Terraform. Checksum-verified against the published SHA256SUMS rather than trusted on
# the strength of TLS alone, because this binary reads the configuration under review.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    archive="terraform_${TERRAFORM_VERSION}_linux_${arch}.zip"; \
    base="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}"; \
    cd /tmp; \
    curl -fsSL -O "${base}/${archive}"; \
    curl -fsSL -O "${base}/terraform_${TERRAFORM_VERSION}_SHA256SUMS"; \
    # Downloaded under its published name, so the checksum line matches as it stands.
    grep " ${archive}\$" "terraform_${TERRAFORM_VERSION}_SHA256SUMS" | sha256sum -c -; \
    unzip -q "${archive}" -d /usr/local/bin; \
    rm -f "${archive}" "terraform_${TERRAFORM_VERSION}_SHA256SUMS"; \
    terraform version

# Open Policy Agent, for the bundled Rego pack.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) opa_arch=amd64_static ;; \
        arm64) opa_arch=arm64_static ;; \
        *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/opa \
        "https://openpolicyagent.org/downloads/${OPA_VERSION}/opa_linux_${opa_arch}"; \
    chmod 0755 /usr/local/bin/opa; \
    opa version

# Checkov and Plimsoll itself, each in its own virtualenv. Checkov pins a wide dependency
# surface and Plimsoll pins a narrow one; installing both into the same environment means
# a checkov release can silently change what this tool depends on.
RUN python -m venv /opt/checkov \
    && /opt/checkov/bin/pip install --no-cache-dir "checkov==${CHECKOV_VERSION}" \
    && ln -s /opt/checkov/bin/checkov /usr/local/bin/checkov \
    && checkov --version

COPY . /src
RUN python -m venv /opt/plimsoll \
    && /opt/plimsoll/bin/pip install --no-cache-dir /src \
    && ln -s /opt/plimsoll/bin/plimsoll /usr/local/bin/plimsoll \
    && rm -rf /src \
    && plimsoll --help > /dev/null

# Providers downloaded during one step are reused by the next. Without this, a `verify`
# run re-downloads the AWS provider once per patch attempt.
ENV TF_PLUGIN_CACHE_DIR=/tmp/plimsoll-plugins \
    PYTHONUNBUFFERED=1

COPY action/entrypoint.sh action/post_comment.py /usr/local/bin/
RUN chmod 0755 /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
