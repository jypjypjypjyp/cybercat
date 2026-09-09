#!/bin/zsh
# deploy.sh — 一次性部署/更新 Cyber Cat (SFTP Manager)
# 1. uv 建 .venv + 安装依赖
# 2. (可选) 跑测试
# 3. 注册 cybercat 命令到 ~/.local/bin
# 4. 验证可用
set -e

# 项目根 = 脚本所在目录（支持项目移动/改名）
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${HOME}/.local/bin"
WRAPPER="${PREFIX}/cybercat"
PY="${PROJECT_DIR}/.venv/bin/python"

echo "== Cyber Cat deploy =="
echo "项目目录: ${PROJECT_DIR}"

# 1. 建 venv + 装依赖
if [ ! -x "$PY" ]; then
  echo "[1/4] 创建 uv 虚拟环境 (.venv)..."
  (cd "$PROJECT_DIR" && uv venv --python 3.12)
else
  echo "[1/4] .venv 已存在，跳过"
fi

echo "[2/4] 安装依赖 (paramiko / fastapi / uvicorn / python-multipart / pytest)..."
(cd "$PROJECT_DIR" && uv pip install 'paramiko>=5.0.0' 'fastapi>=0.140.0' 'uvicorn>=0.50.0' 'python-multipart>=0.0.9' 'pytest>=8.0.0' 'httpx>=0.27.0')

# 3. 注册 cybercat 命令
echo "[3/4] 注册 cybercat 命令 -> ${WRAPPER}"
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

# 4. 验证
echo "[4/4] 验证 cybercat ..."
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
