import importlib.util
import sys
from pathlib import Path

# Compute repo paths
repo_root = Path(__file__).resolve().parents[1]
src_path = repo_root / "src"
cleanup_path = repo_root / "scripts" / "cleanup_redis_slips.py"

if __name__ == "__main__":
    # Ensure `src` is on sys.path so project modules (slip_manager, logging_utils) import properly
    sys.path.insert(0, str(src_path))

    spec = importlib.util.spec_from_file_location(
        "cleanup_redis_slips", str(cleanup_path)
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    raise SystemExit(module.main())
