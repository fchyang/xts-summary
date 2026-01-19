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
# 必须在运行前先 export 以下变量（一次性即可）
#   GITHUB_TOKEN   – PAT，需拥有 repo 权限
#   GITHUB_USERNAME – 你的 GitHub 登录名（用于 git push）
#   GITHUB_REPOSITORY – "owner/repo"（如 YourUser/summary_tool）
#   GITHUB_EMAIL   – （可选）git 提交使用的邮箱
#   PYPI_TOKEN     – PyPI API token
#   PYPI_USERNAME  – 默认 "__token__"（可不改）
# 例：
# export GITHUB_TOKEN=ghp_XXXXXXXXXXXXXXXXXXXX
# export GITHUB_USERNAME=YourUser
# export GITHUB_REPOSITORY=YourUser/summary_tool
# export GITHUB_EMAIL=you@example.com
# export PYPI_TOKEN=xxxxxx

# ---------- 读取版本号 ----------
# 假设 pyproject.toml 中有 `version = "0.1.3"` 这样的行
VERSION=$(grep '^version\s*=' pyproject.toml | head -1 | cut -d'"' -f2)
if [[ -z "$VERSION" ]]; then
    echo "❌ 读取 version 失败！请确认 pyproject.toml 中有正确定义的 version 字段"
    exit 1
fi
TAG="v${VERSION}"
echo "🚀 当前准备发布的版本是: $VERSION (tag: $TAG)"

# -------------------------------------------------
# 防止重复创建 Release（同标签已存在时直接退出）
# -------------------------------------------------
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  # 使用 GitHub API 检查同标签的 Release 是否已存在
  api_url="https://api.github.com/repos/${GITHUB_REPOSITORY}/releases/tags/${TAG}"
  response=$(curl -s -H "Authorization: token $GITHUB_TOKEN" "$api_url")
  if echo "$response" | grep -q '"url"'; then
    echo "⚠️ Release $TAG 已在 GitHub 上存在，已跳过创建以避免重复上传。"
    exit 0
  fi
else
  # 没有 token 时退回 git ls-remote 检查远程标签
  if git ls-remote --tags "$REMOTE" "$TAG" | grep -q "$TAG"; then
    echo "⚠️ 远程已经存在标签 $TAG，已跳过创建以避免重复上传。"
    exit 0
  fi
fi

# ---------- 1️⃣ 提交改动 ----------
echo "🔧 添加并提交本地改动…"
# 确保 Git 使用正确的用户名和邮箱
if [[ -n "${GITHUB_USERNAME:-}" ]]; then
  git config user.name "${GITHUB_USERNAME}"
fi
if [[ -n "${GITHUB_EMAIL:-}" ]]; then
  git config user.email "${GITHUB_EMAIL}"
fi

git add -A
# 若已经没有变化，git commit 会报错，这里捕获并忽略
# 若提供额外信息，则拼接到 commit 信息中
if [[ "$#" -gt 0 ]]; then
  # 第一个参数作为额外描述（可包含空格，记得引用）
  EXTRA_MSG="$*"
  COMMIT_MSG="Release $TAG – $EXTRA_MSG"
else
  COMMIT_MSG="Release $TAG"
fi

git commit -m "$COMMIT_MSG" || echo "✅ 没有需要提交的改动"

echo "📤 推送分支 ${REMOTE}/${BRANCH} …"
# 使用 HTTPS + PAT 推送，避免交互式密码输入
# 自动获取当前 remote URL 并在需要时注入凭证
if [[ -n "${GITHUB_USERNAME:-}" && -n "${GITHUB_TOKEN:-}" ]]; then
  # 取 origin 的 URL（可能是 https://github.com/owner/repo.git）
  ORIGIN_URL=$(git remote get-url origin)
  if [[ "$ORIGIN_URL" =~ ^https:// ]]; then
    # 把 https:// 替换为带凭证的 URL
    AUTH_URL=${ORIGIN_URL/https:\/\//https://${GITHUB_USERNAME}:${GITHUB_TOKEN}@}
    git push "$AUTH_URL" "$BRANCH"
  else
    # 非 https (ssh) 直接使用原 remote
    git push "$REMOTE" "$BRANCH"
  fi
else
  echo "⚠️ 未设置 GITHUB_USERNAME 或 GITHUB_TOKEN，使用默认 remote 推送"
  git push "$REMOTE" "$BRANCH"
fi

# ---------- 2️⃣ 打 tag 并触发 GitHub Actions ----------
# 删除本地已有同名 tag（如果之前手动打过）
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "⚠️ 本地已存在 $TAG，先删除旧 tag 再重新创建"
    git tag -d "$TAG"
    # 删除远程旧 tag（如果需要）
if [[ -n "${GITHUB_USERNAME:-}" && -n "${GITHUB_TOKEN:-}" ]]; then
  ORIGIN_URL=$(git remote get-url origin)
  if [[ "$ORIGIN_URL" =~ ^https:// ]]; then
    AUTH_URL=${ORIGIN_URL/https:\/\//https://${GITHUB_USERNAME}:${GITHUB_TOKEN}@}
    git push "$AUTH_URL" ":refs/tags/$TAG" || true
  else
    git push "$REMOTE" ":refs/tags/$TAG" || true
  fi
else
  git push "$REMOTE" ":refs/tags/$TAG" || true
fi
fi

echo "🏷️ 创建并推送 tag $TAG …"
git tag -a "$TAG" -m "Release $TAG"
# 推送新 tag 到远程
if [[ -n "${GITHUB_USERNAME:-}" && -n "${GITHUB_TOKEN:-}" ]]; then
  ORIGIN_URL=$(git remote get-url origin)
  if [[ "$ORIGIN_URL" =~ ^https:// ]]; then
    AUTH_URL=${ORIGIN_URL/https:\/\//https://${GITHUB_USERNAME}:${GITHUB_TOKEN}@}
    git push "$AUTH_URL" "$TAG"
  else
    git push "$REMOTE" "$TAG"
  fi
else
  git push "$REMOTE" "$TAG"
fi

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
 echo "  • PyPI 包已发布： https://pypi.org/project/${GITHUB_REPOSITORY##*/}/${VERSION}"
