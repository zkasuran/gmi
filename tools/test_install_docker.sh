#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'docker is required for this acceptance test' >&2
    exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
git clone --bare "$root" "$work/repo.git" >/dev/null
git --git-dir="$work/repo.git" tag -f v1.0.1 >/dev/null

run_image() {
    image=$1
    setup='apt-get update -qq >/dev/null 2>&1 && apt-get install -qq -y --no-install-recommends git ca-certificates python3 python3-pip python3-venv >/dev/null 2>&1'
    if [ "$image" = python:3.12-slim ]; then
        setup='apt-get update -qq >/dev/null 2>&1 && apt-get install -qq -y --no-install-recommends git ca-certificates >/dev/null 2>&1'
    fi
    docker run --rm \
        -v "$root/install.sh:/work/install.sh:ro" \
        -v "$work/repo.git:/source/repo.git:ro" \
        "$image" sh -ec "
            export DEBIAN_FRONTEND=noninteractive
            $setup
            git config --global url.file:///source/repo.git.insteadOf https://github.com/zkasuran/gmi
            git config --global --add safe.directory '*'
            HOME=/root GMI_REF=v1.0.1 sh /work/install.sh --mcp
            test \"\$(HOME=/root /root/.local/bin/gmi --version)\" = 'gmi 1.0.1'
            HOME=/root /root/.local/bin/gmi mcp tools >/dev/null
        "
}

status=0
: >"$work/results"
for image in python:3.12-slim debian:12; do
    if run_image "$image"; then
        printf 'PASS %s\n' "$image" >>"$work/results"
    else
        printf 'FAIL %s\n' "$image" >>"$work/results"
        status=1
    fi
done
cat "$work/results"
exit "$status"
