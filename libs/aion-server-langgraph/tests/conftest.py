import sys
from pathlib import Path

# Add package sources to sys.path for tests
PROJECT_ROOT = Path(__file__).resolve().parents[1]
src_path = PROJECT_ROOT / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Ensure local a2a SDK is discoverable if present
A2A_ROOT = PROJECT_ROOT.parent / "_a2a-python" / "src"
if A2A_ROOT.exists() and str(A2A_ROOT) not in sys.path:
    sys.path.insert(0, str(A2A_ROOT))

