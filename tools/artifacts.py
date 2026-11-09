"""Bounded, SHA256-checked artifact retrieval; no device access."""
import hashlib
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen


def retrieve(url, target, expected_sha256, expected_bytes=None, limit=32 * 1024 * 1024):
    target = Path(target)
    if target.exists():
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_sha256 or (expected_bytes is not None and len(data) != expected_bytes):
            raise ValueError(f'Existing file has unexpected contents: {target.name}')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + '.', suffix='.part', delete=False) as out:
            temp = Path(out.name)
            digest = hashlib.sha256()
            count = 0
            with urlopen(Request(url, headers={'User-Agent': 'h3s-research'}), timeout=60) as response:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > limit:
                        raise ValueError('Artifact exceeds size bound')
                    out.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != expected_sha256 or (expected_bytes is not None and count != expected_bytes):
                raise ValueError(f'Artifact digest/length mismatch: {target.name}')
        temp.replace(target)
        return target
    finally:
        if temp is not None and temp.exists():
            temp.unlink()
