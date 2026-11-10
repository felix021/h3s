#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 /path/to/pi32v2/bin /fresh/output/directory" >&2
    exit 2
fi
toolchain=$(realpath "$1")
source_dir=$(cd "$(dirname "$0")" && pwd)
mkdir "$2"
output_dir=$(cd "$2" && pwd)
"$toolchain/clang" -target pi32v2 -mcpu=r3 -Oz -ffreestanding -fno-builtin \
    -fno-common -fno-stack-protector -mllvm -pi32v2-large-program=true \
    -integrated-as -c "$source_dir/patch.c" -o "$output_dir/patch.o"
"$toolchain/clang" -target pi32v2 -mcpu=r3 -mllvm -pi32v2-large-program=true \
    -integrated-as -c "$source_dir/hooks.S" -o "$output_dir/hooks.o"
"$toolchain/ld" -T "$source_dir/patch.ld" -Map "$output_dir/patch.map" \
    "$output_dir/hooks.o" "$output_dir/patch.o" -o "$output_dir/patch.elf"
"$toolchain/objdump" -d -arch-name=pi32v2 -mcpu=r3 "$output_dir/patch.elf" > "$output_dir/patch.asm"
"$toolchain/nm" -n "$output_dir/patch.elf" > "$output_dir/patch.nm"
"$toolchain/clang" --version > "$output_dir/compiler-version.txt"
(cd "$source_dir" && sha256sum patch.c hooks.S patch.ld build.sh) > "$output_dir/source.SHA256SUMS"
echo "$output_dir/patch.elf"
