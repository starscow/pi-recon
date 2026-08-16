#!/usr/bin/env bash
# Build the PI Recon image without embedding runtime credentials.
#   MODE=tsec  → DeepSeek official base (hosted rewrites to *.tsecbench.gw)
#   MODE=baidu → agent-awd.baidu.com (only if you intentionally use that gateway)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODE="${MODE:-tsec}"
PLATFORM="${PLATFORM:-linux/amd64}"
BASE_IMAGE="${BASE_IMAGE:-docker.m.daocloud.io/library/python:3.12-slim-bookworm}"

if [[ "$MODE" == "tsec" ]]; then
  TAG="${TAG:-pi-recon:tsec}"
  OUT="${OUT:-$ROOT/dist/pi-recon-tsec.tar.gz}"
  MODELS_FILE="models.tsec.json"
  DEFAULT_BASE="https://api.deepseek.com/v1"
  EXTRA_BUILD_ARGS=(
    --build-arg "DEEPSEEK_BASE_URL=${DEFAULT_BASE}"
    --build-arg "LLM_ROUTE=gateway"
    --build-arg "TSEC_HOSTED=1"
  )
else
  TAG="${TAG:-pi-recon:baidu}"
  OUT="${OUT:-$ROOT/dist/pi-recon-baidu.tar.gz}"
  MODELS_FILE="models.baidu.json"
  DEFAULT_BASE="${BAIDU_BASE:-https://agent-awd.baidu.com/v1}"
  EXTRA_BUILD_ARGS=(
    --build-arg "DEEPSEEK_BASE_URL=${DEFAULT_BASE}"
    --build-arg "LLM_ROUTE=public"
    --build-arg "TSEC_HOSTED=0"
  )
fi

echo "==> MODE=${MODE} tag=${TAG} models=${MODELS_FILE} platform=${PLATFORM}"
echo "==> runtime credentials are not included; base=${DEFAULT_BASE}"

mkdir -p "$(dirname "$OUT")"
docker build --platform "$PLATFORM" \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --build-arg "MODELS_FILE=${MODELS_FILE}" \
  "${EXTRA_BUILD_ARGS[@]}" \
  -t "$TAG" -f docker/Dockerfile .

docker image inspect "$TAG" --format '{{.Size}}' | awk '{printf "image %.2f GB\n", $1/1024/1024/1024}'
docker save "$TAG" | gzip > "$OUT"
ls -lh "$OUT"
echo "done: $OUT"
