#!/bin/sh
# Starts the built images together and checks that they run, as non-root, and that with no
# credentials the backend refuses cluster requests. Usage: smoke-test.sh BACKEND_IMAGE FRONTEND_IMAGE
set -eu

backend_image=$1
frontend_image=$2
name="ci-smoke-${BUILD_NUMBER:-local}"
backend="$name-backend"
frontend="$name-frontend"

cleanup() {
    docker rm -f "$backend" "$frontend" >/dev/null 2>&1 || true
    docker network rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_until_healthy() {
    container=$1
    attempts=0
    while [ "$(docker inspect --format '{{.State.Health.Status}}' "$container")" != "healthy" ]; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 60 ] || [ "$(docker inspect --format '{{.State.Running}}' "$container")" != "true" ]; then
            echo "FAILED: $container did not become healthy"
            docker logs "$container" 2>&1 | tail -20
            exit 1
        fi
        sleep 2
    done
    echo "$container is healthy"
}

# Prints the HTTP status and body of a backend request, so that error statuses can be checked too.
backend_request() {
    docker exec "$backend" python -c "
import sys, urllib.error, urllib.request
try:
    response = urllib.request.urlopen('http://127.0.0.1:8000$1', timeout=10)
except urllib.error.HTTPError as error:
    response = error
print(response.status, response.read().decode())
"
}

expect() {
    label=$1
    actual=$2
    wanted=$3
    case "$actual" in
        *"$wanted"*) echo "OK: $label" ;;
        *)
            echo "FAILED: $label. Wanted [$wanted] in: $actual"
            exit 1
            ;;
    esac
}

docker network create --label ci=troubleshooting-agent "$name" >/dev/null
docker run -d --name "$backend" --label ci=troubleshooting-agent --network "$name" --network-alias backend \
    --memory 384m --cpus 1 "$backend_image" >/dev/null
wait_until_healthy "$backend"
docker run -d --name "$frontend" --label ci=troubleshooting-agent --network "$name" \
    --memory 64m --cpus 0.25 "$frontend_image" >/dev/null
wait_until_healthy "$frontend"

expect "backend runs as non-root" "$(docker exec "$backend" id -u)" "10001"
expect "frontend runs as non-root" "$(docker exec "$frontend" id -u)" "101"
expect "backend health" "$(backend_request /api/health)" '200 {"status":"ok"'
expect "no credentials means a clear refusal" "$(backend_request /api/cluster/health)" '503 {"detail":{"code":"kubeconfig_unavailable"'
expect "frontend serves the application" "$(docker exec "$frontend" wget -q -O - http://127.0.0.1:8080/)" '<div id="root"'
expect "frontend proxies to the backend" "$(docker exec "$frontend" wget -q -O - http://127.0.0.1:8080/api/health)" '"status":"ok"'
expect "backend exposes metrics" "$(backend_request /metrics)" 'troubleshooting_investigations_total'
expect "metrics count real requests" "$(backend_request /metrics)" 'troubleshooting_http_requests_total{method="GET",route="/api/cluster/health",status="503"} 1.0'
frontend_metrics="$(docker exec "$frontend" wget -q -O - http://127.0.0.1:8080/metrics)"
case "$frontend_metrics" in
    *troubleshooting_*)
        echo "FAILED: the frontend serves backend metrics"
        exit 1
        ;;
    *) echo "OK: the frontend does not serve metrics" ;;
esac
echo "Smoke test passed"
