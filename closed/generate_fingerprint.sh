#!/bin/bash
# =============================================================================
# 设备指纹生成脚本
# =============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_PATH="${1:-$SCRIPT_DIR/device.fingerprint}"

# --- 去首尾空白 -------------------------------------------------------------
trim() { sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

# --- 读取文件第一行（去 NUL、去空白；不可读/为空则输出空串） ----------------
first_line() {
  local f="$1"
  if [ -r "$f" ]; then
    tr -d '\000' < "$f" 2>/dev/null | head -n1 | trim
  fi
}

# --- 1. 采集硬件参数（顺序固定，与 C++ 侧 license_check.h 保持一致） --------
# NX 板卡唯一标识，多源冗余：任一来源缺失不影响整体指纹
dt_serial="$(first_line /proc/device-tree/serial-number)"       # Jetson DT 序列号
mmc_cid="$(first_line /sys/block/mmcblk0/device/cid)"           # eMMC CID
mmc_serial="$(first_line /sys/block/mmcblk0/device/serial)"     # eMMC 序列号

# 网卡 MAC：优先 eth0；缺失时取第一个非虚拟网卡（跳过 lo/docker/veth 等）
eth0_mac="$(first_line /sys/class/net/eth0/address | tr 'A-F' 'a-f')"
if [ -z "$eth0_mac" ]; then
  for iface in $(ls /sys/class/net 2>/dev/null | sort); do
    case "$iface" in
      lo|docker*|veth*|virbr*|br-*|tun*|tap*) continue ;;
    esac
    eth0_mac="$(first_line "/sys/class/net/$iface/address" | tr 'A-F' 'a-f')"
    [ -n "$eth0_mac" ] && break
  done
fi

cpu_serial=""
if [ -r /proc/cpuinfo ]; then
  cpu_serial="$(grep -m1 '^Serial' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | trim | tr 'A-F' 'a-f')"
fi

# --- 拼装规范化原材料：key=value 以分号连接，仅含非空来源 --------------------
raw=""
add_src() { # 键 值
  [ -n "${2:-}" ] || return 0
  if [ -n "$raw" ]; then raw="$raw;"; fi
  raw="$raw$1=$2"
}
add_src dt_serial "$dt_serial"
add_src mmc_cid "$mmc_cid"
add_src mmc_serial "$mmc_serial"
add_src eth0_mac "$eth0_mac"
add_src cpu_serial "$cpu_serial"

if [ -z "$raw" ]; then
  echo "ERROR: 未能读取到任何硬件唯一标识，无法生成设备指纹。" >&2
  echo "       请确认在 J30V2 无人机 NX 板卡上运行本脚本。" >&2
  exit 1
fi

# --- 2. 计算指纹哈希（sha256sum 优先，其次 openssl，最后 python3） ----------
sha_hex=""
if command -v sha256sum >/dev/null 2>&1; then
  sha_hex="$(printf '%s' "$raw" | sha256sum | awk '{print $1}')"
elif command -v openssl >/dev/null 2>&1; then
  sha_hex="$(printf '%s' "$raw" | openssl dgst -sha256 | awk '{print $NF}')"
elif command -v python3 >/dev/null 2>&1; then
  sha_hex="$(printf '%s' "$raw" | python3 -c 'import sys,hashlib;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
fi

if [ -z "$sha_hex" ]; then
  echo "ERROR: 需要 sha256sum / openssl / python3 之一来计算指纹。" >&2
  exit 1
fi

# 设备 ID：J30V2- + 指纹前 8 位大写（与 C++ 侧 DeriveIds 一致）
device_id="J30V2-$(printf '%s' "${sha_hex:0:8}" | tr '[:lower:]' '[:upper:]')"

# --- 3. 写出指纹文件 --------------------------------------------------------
cat > "$OUT_PATH" <<EOF
{
  "device_id": "$device_id",
  "fingerprint": "$sha_hex",
  "version": 1
}
EOF

echo "[完成] 设备指纹已生成: $OUT_PATH"
echo ""
echo "请将生成的 device.fingerprint 文件发送给厂商获取授权。"
echo ""
echo "文件内容:"
cat "$OUT_PATH"
