import sys
from pathlib import Path

# The launcher runs this file as a script, so the package root must be put on the path explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddforge.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
