# Maslow-EcoSystem

## Synthetic page-set verifier

`tools/verify_page_set.py` is a small, read-only CLI for checking the structure of a local manifest and verifying local file bytes against SHA-256 values.

### Manifest contract

The manifest must be UTF-8 JSON with a top-level `pages` list. Each ordered page entry must contain only:

- `page_id` in `P001` format
- `relative_path`
- `sha256` as 64 lowercase hexadecimal characters

### Synthetic usage example

```bash
mkdir -p /tmp/page-set-example/pages
printf 'page-one\n' > /tmp/page-set-example/pages/p001.bin
printf 'page-two\n' > /tmp/page-set-example/pages/p002.bin
printf 'page-three\n' > /tmp/page-set-example/pages/p003.bin

python - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path("/tmp/page-set-example")
pages = []
for index in range(1, 4):
    relative_path = f"pages/p{index:03d}.bin"
    content = (root / relative_path).read_bytes()
    pages.append(
        {
            "page_id": f"P{index:03d}",
            "relative_path": relative_path,
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    )

(root / "manifest.json").write_text(json.dumps({"pages": pages}, indent=2), encoding="utf-8")
PY

cd /home/runner/work/Maslow-EcoSystem/Maslow-EcoSystem
python tools/verify_page_set.py \
  --manifest /tmp/page-set-example/manifest.json \
  --root /tmp/page-set-example \
  --expected-count 3
```

This verifier checks only:

- manifest JSON shape and field types
- contiguous ascending page IDs beginning at `P001`
- expected page count
- duplicate IDs and duplicate paths
- path safety, including traversal and symlink escapes outside the chosen root
- local file presence and SHA-256 byte matches

This verifier does **not** establish:

- source selection
- page approval
- authority to copy material into a public repository
- release readiness or publication approval
- any rights, provenance, or public-disclosure decision

Those require separate evidence and are outside this program.
