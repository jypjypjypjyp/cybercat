#!/bin/zsh
# deploy.sh — 一次性部署/更新 Cyber Cat (SFTP Manager)
# 依赖声明仅在 pyproject.toml（dependencies + dependency-groups.dev），
# 本脚本用 `uv sync` 读取它同步 .venv，避免版本漂移。
# 1. uv sync 建 .venv + 同步依赖（运行时 + dev group）
# 2. 注册 cybercat 命令到 ~/.local/bin
# 3. 验证可用；`--test` 可选跑测试
set -e

# 项目根 = 脚本所在目录（支持项目移动/改名）
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${HOME}/.local/bin"
WRAPPER="${PREFIX}/cybercat"
PY="${PROJECT_DIR}/.venv/bin/python"

echo "== Cyber Cat deploy =="
echo "项目目录: ${PROJECT_DIR}"

# 1. 用 uv sync 按 pyproject.toml 建 venv + 同步依赖（运行时 + dev group，单一来源）
echo "[1/2] uv sync（按 pyproject.toml 同步 .venv，含 dev group）..."
(cd "$PROJECT_DIR" && uv sync)

# 2. 注册 cybercat 命令
echo "[2/2] 注册 cybercat 命令 -> ${WRAPPER}"
mkdir -p "$PREFIX"
cat > "$WRAPPER" <<EOF
#!/bin/zsh
# cybercat —— Cyber Cat 启动命令 (由 deploy.sh 生成)
PROJECT_DIR="${PROJECT_DIR}"
PY="\$PROJECT_DIR/.venv/bin/python"
if [ ! -x "\$PY" ]; then
  echo "cybercat: 未找到项目 Python (\$PY)。请运行 $PROJECT_DIR/deploy.sh" >&2
  exit 1
fi
exec "\$PY" "\$PROJECT_DIR/scripts/cybercat.py" "\$@"
EOF
chmod +x "$WRAPPER"

# 3. 验证
echo ">> 验证 cybercat ..."
if command -v cybercat >/dev/null 2>&1; then
  echo "✅ cybercat 位于: $(command -v cybercat)"
  echo "   运行: cybercat   → 启动 http://127.0.0.1:9000"
else
  echo "⚠️  cybercat 不在 PATH。请确认 ${PREFIX} 在你的 PATH 中，或重新打开终端。"
fi

# 可选测试
if [ "${1:-}" = "--test" ]; then
  echo ">> 运行测试..."
  (cd "$PROJECT_DIR" && "$PY" -m pytest -q)
fi

echo "== 部署完成 =="
