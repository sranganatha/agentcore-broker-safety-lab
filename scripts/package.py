"""Build a deterministic Lambda zip without host dependencies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from agentcore_broker_safety_lab.validate_fixtures import ROOT, validate_runtime_fixtures


ARTIFACTS = ROOT / "artifacts"
PACKAGE = ARTIFACTS / "broker-tool-target.zip"
MANIFEST = ARTIFACTS / "lambda-package.json"


def main() -> None:
    validate_runtime_fixtures()
    files = sorted((ROOT / "agentcore_broker_safety_lab").glob("*.py"))
    files += [
        ROOT / "config" / "cases.json",
        ROOT / "config" / "synthetic_runbooks.json",
        ROOT / "config" / "synthetic_services.json",
        ROOT / "config" / "tools.json",
    ]
    ARTIFACTS.mkdir(exist_ok=True)
    with zipfile.ZipFile(PACKAGE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(PACKAGE.read_bytes()).hexdigest()
    MANIFEST.write_text(
        json.dumps(
            {"file": PACKAGE.name, "sha256": digest, "size_bytes": PACKAGE.stat().st_size},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"packaged {PACKAGE.name} sha256={digest}")


if __name__ == "__main__":
    main()
