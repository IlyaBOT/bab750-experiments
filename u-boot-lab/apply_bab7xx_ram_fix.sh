#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"

echo "[1/3] Ищу BAB7xx.h..."
hdr="$(find "$ROOT" -type f \( -path "*/include/configs/BAB7xx.h" -o -name "BAB7xx.h" \) | head -n1 || true)"
if [[ -z "${hdr}" ]]; then
  echo "Не найден BAB7xx.h"
  exit 1
fi
echo "Нашёл: $hdr"

if ! grep -q 'CONFIG_SYS_TEXT_BASE' "$hdr"; then
  python3 - "$hdr" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
needle = '#define CONFIG_SYS_LOAD_ADDR           0x1000000   /* default load address    */\n'
ins = needle + '#define CONFIG_SYS_TEXT_BASE           0x01000000  /* run U-Boot from RAM for chainload */\n'
if needle in s:
    s = s.replace(needle, ins, 1)
else:
    raise SystemExit("Не нашёл место для вставки CONFIG_SYS_TEXT_BASE")
p.write_text(s)
PY
else
  sed -i 's/^\([[:space:]]*#define[[:space:]]\+CONFIG_SYS_TEXT_BASE[[:space:]]\+\).*/\10x01000000  \/* run U-Boot from RAM for chainload *\//' "$hdr"
fi

echo "[2/3] Ищу u-boot.lds..."
mapfile -t lds_files < <(find "$ROOT" -type f -name 'u-boot.lds')
if [[ ${#lds_files[@]} -eq 0 ]]; then
  echo "Не найден ни один u-boot.lds"
  exit 1
fi

patched_any=0
for lds in "${lds_files[@]}"; do
  if grep -q 'SIZEOF_HEADERS' "$lds"; then
    echo "Патчу: $lds"
    perl -0pi -e 's/\.\s*=\s*SIZEOF_HEADERS\s*;/\. = 0x01000000 + SIZEOF_HEADERS;/g' "$lds"
    patched_any=1
  fi
done

if [[ $patched_any -eq 0 ]]; then
  echo "Нашёл u-boot.lds, но не увидел строку . = SIZEOF_HEADERS;"
  exit 1
fi

echo "[3/3] Готово. Теперь пересобери:"
echo "make CROSS_COMPILE=/opt/devkitpro/devkitPPC/bin/powerpc-eabi- distclean"
echo "make CROSS_COMPILE=/opt/devkitpro/devkitPPC/bin/powerpc-eabi- HOSTCC=cc HOSTSTRIP=strip SFX=BAB7xx"

echo
echo "После сборки проверь:"
echo "grep -n \"Linker script and memory map\" -A25 u-boot.map | head -25"
echo "grep -n \"_start\" u-boot.map | head"
echo
echo "Ожидаемо должно быть что-то вроде:"
echo ".text  0x01000060"
echo "_start 0x01000160"
