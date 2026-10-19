#!/bin/bash
# 把 scrcpy 官方发行包（自带 adb）装进 wrob 环境。
# conda 里没有这两个工具，本机也没有 Homebrew；官方包是免安装的，解压即可用，不需要管理员权限。
#
#   tools/install_scrcpy.sh            装下面钉住的版本
#   tools/install_scrcpy.sh v5.0.1     装指定版本（https://github.com/Genymobile/scrcpy/releases）
#
# 装完后 conda activate wrob 就能直接用 scrcpy 和 adb。
set -euo pipefail

VERSION="${1:-v4.1}"
ENV_DIR="$(conda run -n wrob python -c 'import sys; print(sys.prefix)')"

case "$(uname -m)" in
    arm64) ARCH=aarch64 ;;
    x86_64) ARCH=x86_64 ;;
    *) echo "不支持的架构：$(uname -m)" >&2; exit 1 ;;
esac

NAME="scrcpy-macos-${ARCH}-${VERSION}"
BASE="https://github.com/Genymobile/scrcpy/releases/download/${VERSION}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "下载 ${NAME}.tar.gz …"
curl -fsSL -o "$WORK/${NAME}.tar.gz" "${BASE}/${NAME}.tar.gz"
curl -fsSL -o "$WORK/SHA256SUMS.txt" "${BASE}/SHA256SUMS.txt"

# 和发行页公布的校验和对一遍，对不上就不装。
(cd "$WORK" && grep " ${NAME}.tar.gz\$" SHA256SUMS.txt | shasum -a 256 -c -)

TARGET="$ENV_DIR/opt/scrcpy"
rm -rf "$TARGET"
mkdir -p "$TARGET"
tar -xzf "$WORK/${NAME}.tar.gz" -C "$TARGET" --strip-components=1

# scrcpy 要在自己的目录里找 scrcpy-server 和 adb，所以用转发脚本而不是软链接。
for tool in scrcpy adb; do
    printf '#!/bin/sh\nexec "%s/%s" "$@"\n' "$TARGET" "$tool" > "$ENV_DIR/bin/$tool"
    chmod +x "$ENV_DIR/bin/$tool"
done

"$ENV_DIR/bin/scrcpy" --version | head -1
"$ENV_DIR/bin/adb" version | head -1
echo "已装到 $TARGET"
