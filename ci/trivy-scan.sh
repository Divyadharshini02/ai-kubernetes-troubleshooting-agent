#!/bin/sh
# Scans the images and dependencies, and fails on HIGH or CRITICAL findings that have a fix, and on
# any secret. A scan that cannot run also fails. Full results are always saved in reports/.
# Usage: trivy-scan.sh BACKEND_IMAGE FRONTEND_IMAGE
set -eu

backend_image=$1
frontend_image=$2
cache_volume=ci-trivy-cache
gate="--severity HIGH,CRITICAL --ignore-unfixed --exit-code 1"
skip_dirs="--skip-dirs .ci --skip-dirs reports --skip-dirs backend/tests --skip-dirs frontend/node_modules --skip-dirs frontend/dist"

trivy() {
    docker run --rm --label ci=troubleshooting-agent -u 1000:1000 -e HOME=/tmp \
        -e TRIVY_CACHE_DIR=/cache -e TRIVY_NO_PROGRESS=true ${TRIVY_EXTRA_ENV:-} \
        -v "$cache_volume:/cache" -v "$WORKSPACE:/w" -w /w "$TRIVY_IMAGE" "$@"
}

docker run --rm -v "$cache_volume:/cache" --entrypoint chown "$TRIVY_IMAGE" 1000:1000 /cache

# Without a database a scan would find nothing, so failing to get one must fail the build.
if ! trivy image --download-db-only; then
    echo "Retrying the vulnerability database download once"
    sleep 30
    trivy image --download-db-only
fi
# Every scan below uses the database just downloaded.
TRIVY_EXTRA_ENV="-e TRIVY_SKIP_DB_UPDATE=true ${TRIVY_EXTRA_ENV:-}"

status=0

scan_image() {
    label=$1
    image=$2
    docker save -o ".ci/$label.tar" "$image"
    # The full report, with every severity. It never decides the result.
    trivy image --input "/w/.ci/$label.tar" --scanners vuln,secret --format json --output "/w/reports/trivy-$label.json"
    trivy image --input "/w/.ci/$label.tar" --scanners vuln,secret --format table --output "/w/reports/trivy-$label.txt"
    echo "=== Gate: $image ==="
    if ! trivy image --input "/w/.ci/$label.tar" --scanners vuln,secret $gate --format table --output "/w/reports/trivy-$label-gate.txt"; then
        echo "GATE FAILED: $image"
        status=1
    fi
    cat "reports/trivy-$label-gate.txt"
    rm -f ".ci/$label.tar"
}

scan_image backend "$backend_image"
scan_image frontend "$frontend_image"

echo "=== Gate: dependency files and committed source ==="
trivy fs --scanners vuln,secret $skip_dirs --format json --output /w/reports/trivy-dependencies.json /w
if ! trivy fs --scanners vuln,secret $skip_dirs $gate --format table --output /w/reports/trivy-dependencies-gate.txt /w; then
    echo "GATE FAILED: dependency files or committed source"
    status=1
fi
cat reports/trivy-dependencies-gate.txt

# Report only. This includes advice worth reading, but it is not a pass or fail check.
trivy config $skip_dirs --format table --output /w/reports/trivy-config.txt /w

if [ "$status" -ne 0 ]; then
    echo "Trivy found issues that must be fixed. See the reports above."
fi
exit "$status"
