#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="langchain"
ENV_FILE="$PROJECT_DIR/environment.yml"

usage() {
  cat <<'EOF'
Kanana Schedule Agent runner

Usage:
  ./run.sh             Create env if missing, then run the Gradio app
  ./run.sh --install   Create/update conda env "langchain" from environment.yml, then run
  ./run.sh --golden    Activate env and run golden scenario tests
  ./run.sh --test      Activate env and run pytest + golden scenario tests
  ./run.sh --help      Show this help

First-time setup:
  ./run.sh
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if ! command -v conda >/dev/null 2>&1; then
  echo "conda를 찾을 수 없습니다. Miniconda 또는 Anaconda를 먼저 설치해주세요." >&2
  exit 1
fi

CONDA_BASE="$(conda info --base)"
# shellcheck source=/dev/null
source "$CONDA_BASE/etc/profile.d/conda.sh"

env_exists() {
  conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"
}

cd "$PROJECT_DIR"

if [[ "${1:-}" == "--install" ]]; then
  if env_exists; then
    echo "Updating conda env: $ENV_NAME"
    conda env update -n "$ENV_NAME" -f "$ENV_FILE" --prune
  else
    echo "Creating conda env: $ENV_NAME"
    conda env create -f "$ENV_FILE"
  fi
elif ! env_exists; then
  echo "conda env '$ENV_NAME'가 없어 environment.yml로 새로 만듭니다."
  conda env create -f "$ENV_FILE"
fi

conda activate "$ENV_NAME"
export PYTHONNOUSERSITE=1

if [[ "${1:-}" == "--golden" ]]; then
  python -m run_golden
elif [[ "${1:-}" == "--test" ]]; then
  pytest -q
  python -m run_golden
else
  python app.py
fi
