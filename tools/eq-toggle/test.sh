#!/usr/bin/env bash
set -euo pipefail
source_dir=$(cd "$(dirname "$0")" && pwd)
test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT
"${CC:-cc}" -std=c99 -Wall -Wextra -Werror -DH3S_EQ_HOST_TEST \
    "$source_dir/patch.c" "$source_dir/test.c" -o "$test_dir/eq-test"
"$test_dir/eq-test"
