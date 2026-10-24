"""Fill missing dotenv secrets without rotating existing credentials."""

import argparse
import re
import secrets
import sys
import tempfile
from pathlib import Path

SECRET_KEYS = ("APP_SECRET_KEY", "SENS_DATA_ENCRYPT_KEY", "DB_PASSWORD", "ADMIN_PASSWORD")
ASSIGNMENT = re.compile(
    r"^(?P<prefix>[ \t]*(?:export[ \t]+)?(?P<key>[A-Z_]+)[ \t]*=)(?P<value>.*)$"
)
EMPTY_VALUE = re.compile(r"""[ \t]*(?:''|"")[ \t]*(?:\#.*)?|[ \t]+\#.*""")


def generate_secrets(path: Path) -> list[str]:
    """Preserve configured values and unrelated lines; write new values privately."""
    if path.is_symlink():
        raise ValueError("Use the real dotenv file path instead of a symlink")
    with path.open(encoding="utf-8", newline="") as source:
        lines = source.readlines()

    assignments: dict[str, tuple[int, str, str]] = {}
    for index, line in enumerate(lines):
        match = ASSIGNMENT.fullmatch(line.rstrip("\r\n"))
        if match is None or match["key"] not in SECRET_KEYS:
            continue
        key = match["key"]
        if key in assignments:
            raise ValueError(f"Duplicate {key}; keep one assignment before generating secrets")
        assignments[key] = (index, match["prefix"], match["value"])

    generated: list[str] = []
    for key in SECRET_KEYS:
        assignment = assignments.get(key)
        if assignment is not None:
            index, prefix, value = assignment
            if value.strip() and EMPTY_VALUE.fullmatch(value) is None:
                continue
            newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
            lines[index] = f"{prefix}{secrets.token_urlsafe(48)}{newline}"
        else:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += "\n"
            lines.append(f"{key}={secrets.token_urlsafe(48)}\n")
        generated.append(key)

    if not generated:
        path.chmod(0o600)
        return generated

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.writelines(lines)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return generated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    try:
        generated = generate_secrets(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"Cannot prepare secrets: {exc}", file=sys.stderr)
        return 1
    if generated:
        print(f"Generated: {', '.join(generated)}. File permissions: 600.")
    else:
        print("All secrets are already configured. File permissions: 600.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
