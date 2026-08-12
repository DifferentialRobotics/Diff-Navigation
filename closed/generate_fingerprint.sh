#!/bin/bash
# =============================================================================
# 设备指纹生成脚本
# =============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_PATH="${1:-$SCRIPT_DIR/device.fingerprint}"

# --- 1. 定位 ECID 文件（候选顺序与 C++ 侧 license_check.h 完全一致） --------
ecid_path=""
for f in "${J30V2_ECID_PATH:-}" \
         /proc/device-tree/chosen/ecid \
         /sys/devices/platform/efuse-burn/ecid \
         /sys/devices/platform/tegra-fuse/ecid \
         /sys/module/tegra_fuse/parameters/tegra_chip_uid \
         /sys/module/fuse_burn/parameters/tegra_chip_uid; do
  [ -n "$f" ] || continue
  if [ -r "$f" ] && [ -s "$f" ]; then
    ecid_path="$f"
    break
  fi
done

if [ -z "$ecid_path" ]; then
  echo "ERROR: 未能读取到 ECID（Embedded Chip ID），无法生成设备指纹。" >&2
  echo "       请确认在 J30V2 无人机 NX 板卡（Jetson）上运行本脚本。" >&2
  exit 1
fi

# --- 2. 计算指纹哈希（sha256sum 优先，其次 openssl，最后 python3） ----------
# 直接哈希 ECID 文件原始字节，与 C++ 侧 Sha256Hex(ecid_bytes) 一致
sha_hex=""
if command -v sha256sum >/dev/null 2>&1; then
  sha_hex="$(sha256sum < "$ecid_path" | awk '{print $1}')"
elif command -v openssl >/dev/null 2>&1; then
  sha_hex="$(openssl dgst -sha256 < "$ecid_path" | awk '{print $NF}')"
elif command -v python3 >/dev/null 2>&1; then
  sha_hex="$(python3 -c 'import sys,hashlib;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())' < "$ecid_path")"
fi

if [ -z "$sha_hex" ]; then
  echo "ERROR: 需要 sha256sum / openssl / python3 之一来计算指纹。" >&2
  exit 1
fi

# 设备 ID：J30V2- + 指纹前 8 位大写（与 C++ 侧 DeriveIds 一致）
device_id="J30V2-$(printf '%s' "${sha_hex:0:8}" | tr '[:lower:]' '[:upper:]')"

# --- 3. 写出指纹文件 --------------------------------------------------------
# version 为信息性字段，恒为 1（校验端与签发端均不解析）
cat > "$OUT_PATH" <<EOF
{
  "device_id": "$device_id",
  "fingerprint": "$sha_hex",
  "version": 1
}
EOF

echo "[完成] 设备指纹已生成: $OUT_PATH"
echo "       ECID 来源: $ecid_path"
echo ""
echo "请将生成的 device.fingerprint 文件发送给厂商获取授权。"
echo ""
echo "文件内容:"
cat "$OUT_PATH"
