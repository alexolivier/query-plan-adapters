#!/usr/bin/env bash
# Runs the linter in the same container as the suites.
set -euo pipefail

cd "$(dirname "$0")/.."

CERBOS_VERSION="$(tr -d '[:space:]' < ../conformance/CERBOS_VERSION)"
CERBOS_IMAGE_DIGEST="$(tr -d '[:space:]' < ../conformance/CERBOS_IMAGE_DIGEST)"
export CERBOS_VERSION CERBOS_IMAGE_DIGEST
# Compose interpolates every service, including the store services this script never starts.
POSTGRES_IMAGE="$(tr -d '[:space:]' < POSTGRES_IMAGE)"
MYSQL_IMAGE="$(tr -d '[:space:]' < MYSQL_IMAGE)"
export POSTGRES_IMAGE MYSQL_IMAGE
export RUBY_VERSION="${RUBY_VERSION:-3.4}"
export ACTIVERECORD_VERSION="${ACTIVERECORD_VERSION:-8.0}"

docker compose build tests
# No PDP is necessary, because standardrb reads only the source.
docker compose run --rm --no-deps tests bundle exec standardrb "$@"
