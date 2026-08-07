#!/bin/zsh
# =============================================================================
# 闭源工作空间安装产物补全脚本
# -----------------------------------------------------------------------------
# 背景：算法部闭源 CMakeLists.txt 大多未编写 install() 规则，直接
#       `catkin_make install` 只生成环境文件、cmake 配置与 package.xml，
#       可执行文件、动态库、launch 文件、config 文件均不会进入 install/。
#       因此闭源启动脚本无法仅靠 install/ 运行。
#
# 本脚本在 catkin_make install 之后执行，将闭源运行所需的资产从
# devel/（编译产物）与 src/（launch/config 资源）补齐到 install/ 中，
# 之后 closed 启动脚本完全依赖 install/，与 src/ 无关——
# 未来算法部删除 src/ 后无需重新编译、无需再次执行本脚本。
#
# 用法（在 catkin_make install 之后运行一次即可，可重复执行）：
#   ./sh_files/install_closed_assets.sh
# =============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CLOSED_DIR="$PROJECT_DIR/closed"
SRC_ROOT="$CLOSED_DIR/src/diff_planner"
DEVEL_LIB="$CLOSED_DIR/devel/lib"
INSTALL_DIR="$CLOSED_DIR/install"
INSTALL_LIB="$INSTALL_DIR/lib"
INSTALL_SHARE="$INSTALL_DIR/share"

# --- 1. 前提检查 -----------------------------------------------------------
if [ ! -f "$CLOSED_DIR/devel/setup.zsh" ]; then
    echo "[错误] 未找到 closed 工作空间 devel 环境文件。"
    echo "       请先完成: cd $CLOSED_DIR && catkin_make install ... "
    exit 1
fi
if [ ! -d "$SRC_ROOT" ]; then
    echo "[错误] 未找到闭源源码目录: $SRC_ROOT"
    echo "       本脚本需要从 src/ 复制 launch/config 资源。"
    exit 1
fi

# --- 2. 收集闭源包清单（按包名，兼容 user_command/multipoint 嵌套结构） ------
# zsh 中变量不做单词拆分，用 ${(f)} 按换行拆分
pkg_list=$(find "$SRC_ROOT" -name package.xml -exec grep -m1 '<name>' {} \; \
           | sed 's/.*<name>\([^<]*\)<\/name>.*/\1/')
if [ -z "$pkg_list" ]; then
    echo "[错误] 未在 $SRC_ROOT 下发现任何包。"
    exit 1
fi

# --- 3. 复制 launch / config 资源 -------------------------------------------
for pkg in ${(f)pkg_list}; do
    pkg_dir="$(dirname "$(grep -rl "<name>$pkg</name>" "$SRC_ROOT" --include=package.xml | head -1)")"
    [ -z "$pkg_dir" ] && continue
    mkdir -p "$INSTALL_SHARE/$pkg"
    for res in launch config rviz param resource; do
        if [ -d "$pkg_dir/$res" ]; then
            cp -r "$pkg_dir/$res" "$INSTALL_SHARE/$pkg/"
            echo "[复制] $pkg/$res -> install/share/$pkg/$res"
        fi
    done
done

# --- 4. 复制可执行文件（devel/lib/<pkg>/） -----------------------------------
for pkg in ${(f)pkg_list}; do
    if [ -d "$DEVEL_LIB/$pkg" ]; then
        mkdir -p "$INSTALL_LIB/$pkg"
        cp -r "$DEVEL_LIB/$pkg"/* "$INSTALL_LIB/$pkg/" 2>/dev/null
        echo "[复制] $pkg 可执行文件 -> install/lib/$pkg/"
    fi
done

# --- 5. 复制动态库（devel/lib/*.so） -----------------------------------------
if ls "$DEVEL_LIB"/*.so >/dev/null 2>&1; then
    cp "$DEVEL_LIB"/*.so "$INSTALL_LIB/"
    echo "[复制] 动态库 -> install/lib/"
fi

echo "=============================================="
echo "[完成] 闭源 install 空间补全完毕。"
echo "       启动脚本: ./sh_files/run_single_lio_closed.sh"
echo "=============================================="
