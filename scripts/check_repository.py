"""Fail CI if runtime source is flattened or private artifacts are committed."""

import subprocess
from pathlib import Path


def main():
    paths = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    problems = []
    blocked_suffixes = {".sqlite3", ".dump", ".backup", ".xlsx", ".xls", ".csv", ".pdf"}
    for name in filter(None, paths):
        path = Path(name)
        if not path.exists():
            continue  # A staged deletion is valid during local preparation.
        if path.suffix.lower() in blocked_suffixes or path.name.startswith(".env"):
            problems.append(f"Private data artifact: {name}")
        if (
            len(path.parts) == 1
            and path.suffix in {".py", ".html", ".css", ".js"}
            and name != "manage.py"
        ):
            problems.append(f"Misplaced runtime source: {name}")
        if "__pycache__" in path.parts or "staticfiles" in path.parts:
            problems.append(f"Generated artifact: {name}")
    shim = Path("build (3).sh")
    if shim.exists() and shim.stat().st_size > 1024:
        problems.append("Legacy build wrapper must remain a small forwarding script.")
    if problems:
        raise SystemExit("\n".join(problems))
    print("Repository layout and private-artifact checks passed.")


if __name__ == "__main__":
    main()
