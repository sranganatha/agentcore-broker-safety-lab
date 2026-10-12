"""Build deterministic Lambda and AgentCore Runtime zips."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from agentcore_broker_safety_lab.validate_fixtures import ROOT, validate_runtime_fixtures


ARTIFACTS = ROOT / "artifacts"
PACKAGE = ARTIFACTS / "broker-tool-target.zip"
RUNTIME_PACKAGE = ARTIFACTS / "broker-runtime.zip"
MANIFEST = ARTIFACTS / "aws-packages.json"
RUNTIME_DEPS = Path("/runtime-deps")


def _write_zip(target: Path, files: list[tuple[Path, str]]) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def _artifact(path: Path) -> dict[str, int | str]:
    return {
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def main() -> None:
    validate_runtime_fixtures()
    files = sorted(
        path
        for path in (ROOT / "agentcore_broker_safety_lab").glob("*.py")
        if path.name not in {"broker_agent.py", "runtime_app.py"}
    )
    files += [
        ROOT / "config" / "cases.json",
        ROOT / "config" / "synthetic_runbooks.json",
        ROOT / "config" / "synthetic_services.json",
        ROOT / "config" / "tools.json",
    ]
    ARTIFACTS.mkdir(exist_ok=True)
    _write_zip(PACKAGE, [(path, path.relative_to(ROOT).as_posix()) for path in files])
    if not RUNTIME_DEPS.is_dir():
        raise SystemExit("runtime dependencies are unavailable; use the runtime packaging image")
    runtime_files = [
        (path, path.relative_to(RUNTIME_DEPS).as_posix())
        for path in RUNTIME_DEPS.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
    ]
    runtime_files += [
        (path, path.relative_to(ROOT).as_posix())
        for path in (
            ROOT / "agentcore_broker_safety_lab" / "__init__.py",
            ROOT / "agentcore_broker_safety_lab" / "broker_agent.py",
            ROOT / "agentcore_broker_safety_lab" / "runtime_app.py",
        )
    ]
    _write_zip(RUNTIME_PACKAGE, runtime_files)
    manifest = {"lambda": _artifact(PACKAGE), "runtime": _artifact(RUNTIME_PACKAGE)}
    MANIFEST.write_text(
        json.dumps(manifest, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    for artifact in manifest.values():
        print(f"packaged {artifact['file']} sha256={artifact['sha256']}")


if __name__ == "__main__":
    main()
