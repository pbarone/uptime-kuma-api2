#!/bin/bash
#
# Runs the full inherited integration suite against a throwaway Uptime Kuma
# container.
#
# DISPOSABLE LOCAL DOCKER HOST ONLY. This script runs the inherited integration
# tests, which delete every monitor, notification, proxy, tag, status page,
# docker host, maintenance and API key on the instance they connect to. That is
# safe here only because each instance is a fresh container this script created
# and destroys, and it is why the target must never be a server you care about.
# The container is published on 127.0.0.1 only, on UPTIME_KUMA_TEST_PORT
# (default 3002, never the conventional 3001 a real instance may hold), under a
# name unique to this run.
#
# Usage: ./run_tests.sh [uptime-kuma-version]
#   With no argument, runs 3.0.0-beta.3. Pass 3.0.0-beta.2 to test that beta.
#
# Better Auth allows 10 requests per 60 seconds, so the suite pauses for a
# minute when test_2fa has used up the window. Expect a few minutes per run.
#
# Requires bash: uses arrays, [[ ]] and trap. Do not switch the shebang back to sh.
version="$1"
if [ -n "$version" ]
then
  versions=("$version")
else
  # the Uptime Kuma 3.x line this branch supports
  versions=(3.0.0-beta.3)
fi
port="${UPTIME_KUMA_TEST_PORT:-3002}"
if [ "$port" = "3001" ]
then
  echo "Refusing port 3001: it is where a real Uptime Kuma usually runs."
  exit 1
fi
export UPTIME_KUMA_TEST_URL="http://127.0.0.1:$port"
container="uptimekuma-it-$$"
stop_container() {
  docker rm -f "$container" > /dev/null 2>&1
}
trap stop_container EXIT
status=0
for version in "${versions[@]}"
do
  echo "Starting uptime kuma $version..."
  docker run -d --rm -p "127.0.0.1:$port:3001" --name "$container" \
    -e UPTIME_KUMA_DB_TYPE=sqlite \
    -e UPTIME_KUMA_DISABLE_LEAK_PWD_CHECK=1 \
    "louislam/uptime-kuma:$version" > /dev/null || exit 1
  readiness_timeout="${READINESS_TIMEOUT:-60}"
  readiness_started_at=$SECONDS
  while [[ "$(curl -s -L --max-time 1 -o /dev/null -w ''%{http_code}'' "127.0.0.1:$port")" != "200" ]]
  do
    readiness_elapsed=$((SECONDS - readiness_started_at))
    if (( readiness_elapsed >= readiness_timeout ))
    then
      echo "Timed out waiting for uptime kuma $version after ${readiness_elapsed}s."
      exit 1
    fi
    sleep 0.5
  done
  echo "Running tests..."
  python -m unittest discover -s tests || status=1
  echo "Stopping uptime kuma..."
  stop_container
  sleep 1
  echo ''
done
exit $status
