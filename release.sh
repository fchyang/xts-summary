#!/usr/bin/env bash
# --------------------------------------------------------------
# 一键发布脚本
#  1️⃣ 提交本地改动并推送到远程分支
#  2️⃣ 打 tag (vX.Y.Z) → 触发 GitHub Actions Release workflow
#  3️⃣ 本地构建 wheel / sdist 并上传到 PyPI
# --------------------------------------------------------------

set -euo pipefail                     # 严格模式
IFS=$'\n\t'

# ---------- 配置 ----------
REMOTE="${REMOTE:-origin}"             # 远程仓库名
BRANCH="${BRANCH:-main}"               # 推送的分支
PYPI_USERNAME="__token__"              # PyPI 官方要求的用户名
# 必须在运行前先 export 下面两个变量
# export GITHUB_TOKEN=ghp_XXXXXXXXXXXXXXXXXXXX
# export PYPI_TOKEN=YOUR_PYPI_TOKEN

# ---------- 读取版本号 ----------
# 假设 pyproject.toml 中有 `version = "0.1.3"` 这样的行
VERSION=$(grep '^version\s*=' pyproject.toml | head -1 | cut -d'"' -f2)
if [[ -z "$VERSION" ]]; then
    echo "❌ 读取 version 失败！请确认 pyproject.toml 中有正确定义的 version 字段"
    exit 1
fi
TAG="v${VERSION}"
echo "🚀 当前准备发布的版本是: $VERSION (tag: $TAG)"

# ---------- 1️⃣ 提交改动 ----------
echo "🔧 添加并提交本地改动…"
git add -A
# 若已经没有变化，git commit 会报错，这里捕获并忽略
git commit -m "Release $TAG" || echo "✅ 没有需要提交的改动"

echo "📤 推送分支 ${REMOTE}/${BRANCH} …"
git push "${REMOTE}" "${BRANCH}"

# ---------- 2️⃣ 打 tag 并触发 GitHub Actions ----------
# 删除本地已有同名 tag（如果之前手动打过）
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "⚠️ 本地已存在 $TAG，先删除旧 tag 再重新创建"
    git tag -d "$TAG"
    git push "${REMOTE}" ":refs/tags/$TAG" || true
fi

echo "🏷️ 创建并推送 tag $TAG …"
git tag -a "$TAG" -m "Release $TAG"
git push "${REMOTE}" "$TAG"

# --------------------------------------------------------------
# 等待 GitHub Actions 完成（可选）
# 这里使用 gh CLI 轮询最近一次在该 tag 上跑的 workflow
# 如果你的仓库没有安装 gh，可直接去 GitHub 页面手动观察
# --------------------------------------------------------------
if command -v gh >/dev/null; then
    echo "⏳ 正在等待 GitHub Actions workflow 完成…"
    # 获取最近一次针对当前 tag 的 workflow run id
    RUN_ID=$(gh run list --branch "$TAG" --limit 1 --json databaseId -q '.[0].databaseId')
    if [[ -z "$RUN_ID" ]]; then
        echo "⚠️ 未能立刻获取 workflow run，稍后将继续轮询…"
        # 直接使用 tag 名字进行 watch，gh 会自动跟踪最近一次相同 tag 的 run
        gh run watch --branch "$TAG"
    else
        # 追踪具体的 run，直到成功或失败
        gh run watch "$RUN_ID"
    fi
    echo "✅ GitHub Actions 已完成"
else
    echo "⚠️ 未安装 gh CLI，不能自动轮询 workflow 状态。请自行登录 GitHub 检查 Release 是否已创建。"
fi
# ---------- 3️⃣ 本地构建并上传至 PyPI ----------
echo "🔧 安装构建工具（build、twine）…"
python -m pip install --upgrade pip
pip install --quiet build twine

echo "📦 本地构建 wheel 与 sdist …"
# 确保 dist 目录干净
rm -rf dist && mkdir -p dist
python -m build

echo "🚀 将构建产物上传至 PyPI …"
# 通过环境变量传入的 PYPI_TOKEN 进行身份验证
python -m twine upload dist/* \
    -u "$PYPI_USERNAME" -p "$PYPI_TOKEN" \
    --non-interactive

echo "🎉 完成！"
echo "  • GitHub Release 已创建： https://github.com/${GITHUB_REPOSITORY}/releases/tag/${TAG}"
echo "  • PyPI 包已发布： https://pypi.org/project/${PYPI_USERNAME:-summary-tool}/${VERSION}"
