#!/bin/bash
#
# Runs the full inherited integration suite against a throwaway Uptime Kuma
# container.
#
# DISPOSABLE LOCAL DOCKER HOST ONLY. This script binds host port 3001 and
# claims the container name "uptimekuma", so it will collide with any Uptime
# Kuma already running there. It also runs the inherited integration tests,
# which delete every monitor, notification, proxy, tag, status page, docker
# host, maintenance and API key on the instance they connect to. That is safe
# here only because each instance is a fresh container this script created and
# destroys, and it is why the target must never be a server you care about.
#
# Usage: ./run_tests.sh [uptime-kuma-version]
#   With no argument, runs the single version in the matrix below.
#
# Known limitation on the v3 branch: this suite cannot pass until the
# authentication change lands. Uptime Kuma 3.x moved login from the socket.io
# login event to HTTP, and the library does not speak that yet, so the suite
# fails at login. The 3.x image may also need database environment variables
# that this script does not set. Both are left for that change.
#
# Requires bash: uses arrays and [[ ]]. Do not switch the shebang back to sh.

version="$1"
if [ -n "$version" ]
then
  versions=("$version")
else
  # 3.0.0-beta.2 - the Uptime Kuma 3.x line this branch supports
  versions=(3.0.0-beta.2)
fi

for version in "${versions[@]}"
do
  echo "Starting uptime kuma $version..."
  docker run -d -it --rm -p 3001:3001 --name uptimekuma "louislam/uptime-kuma:$version" > /dev/null

  readiness_timeout="${READINESS_TIMEOUT:-60}"
  readiness_started_at=$SECONDS

  while [[ "$(curl -s -L --max-time 1 -o /dev/null -w ''%{http_code}'' 127.0.0.1:3001)" != "200" ]]
  do
    readiness_elapsed=$((SECONDS - readiness_started_at))
    if (( readiness_elapsed >= readiness_timeout ))
    then
      echo "Timed out waiting for uptime kuma $version after ${readiness_elapsed}s."
      echo "Stopping uptime kuma..."
      docker stop uptimekuma > /dev/null
      exit 1
    fi
    sleep 0.5
  done

  echo "Running tests..."
  python -m unittest discover -s tests

  echo "Stopping uptime kuma..."
  docker stop uptimekuma > /dev/null
  sleep 1

  echo ''
done
