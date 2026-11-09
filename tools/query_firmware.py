#!/usr/bin/env python3
"""查询 QCY H3S 当前发布的 OTA，可选下载；不连接或刷写耳机。"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", default="0.0.1", help="上报的当前版本，默认旧版 0.0.1")
    parser.add_argument("--download-dir", type=Path, help="下载返回的固件到此目录")
    args = parser.parse_args()
    base = "https://appcontent.qcy.com"
    headers = {"sys_": "android", "app_version": "4.2.1_701", "lang": "zh", "country": "CN"}
    with urlopen(Request(base + "/user/touristMode", headers=headers), timeout=30) as response:
        token = json.load(response)["data"]["token"]
    headers.update({"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8"})
    body = json.dumps({"modelId": 16764, "firmwareVersion": args.current}).encode()
    request = Request(base + "/earphoneoOta/findEarphoneoOta", data=body, headers=headers)
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("code") != 200:
        raise SystemExit("查询失败，未下载")
    firmware = result.get("data")
    if not args.download_dir or not firmware:
        return
    if firmware.get("modelId") != 16764:
        raise SystemExit("响应型号不匹配，未下载")
    url = firmware["firmwareUrl"]
    name = Path(urlsplit(url).path).name
    if not name:
        raise SystemExit("下载 URL 缺少文件名")
    with urlopen(url, timeout=60) as response:
        content = response.read()
        expected = response.headers.get("Content-Length")
    if expected is not None and len(content) != int(expected):
        raise SystemExit("下载长度不匹配，未保存")
    args.download_dir.mkdir(parents=True, exist_ok=True)
    dest = args.download_dir / name
    if dest.exists() and dest.read_bytes() != content:
        raise SystemExit("已有同名但内容不同的文件，未覆盖")
    dest.write_bytes(content)
    print(f"保存：{dest}\n大小：{len(content)}\nSHA-256：{hashlib.sha256(content).hexdigest()}")


if __name__ == "__main__":
    main()
