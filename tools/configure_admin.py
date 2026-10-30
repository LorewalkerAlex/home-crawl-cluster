"""Set the one dashboard administrator in a private env file; never print credentials."""

import argparse
import getpass
import os
from pathlib import Path
import re
import secrets
import sys

from pwdlib import PasswordHash


def configure(path, username, password):
    path = Path(path)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
        raise ValueError(
            "Use 1-64 letters, digits, underscore, dot or hyphen for the username"
        )
    if not 8 <= len(password) <= 512:
        raise ValueError("Password must contain 8-512 characters")
    if path.is_symlink() or not path.is_file():
        raise ValueError("Use the existing private app.env file")
    lines = path.read_text(encoding="utf-8").splitlines()
    if sum(line.startswith("PROXYPOOL_API_KEY=") for line in lines) != 1:
        raise ValueError("Expected one existing business API key; not changed")
    values = {
        "PROXYPOOL_ADMIN_USERNAME": username,
        # Compose env_file single quotes preserve the dollar signs in Argon2 hashes.
        "PROXYPOOL_ADMIN_PASSWORD_HASH": "'"
        + PasswordHash.recommended().hash(password)
        + "'",
        "PROXYPOOL_SESSION_SECRET": secrets.token_urlsafe(32),
    }
    output = [line for line in lines if line.split("=", 1)[0] not in values]
    output.extend(name + "=" + value for name, value in values.items())
    with path.open("r+", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(output) + "\n")
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)
    return {"configured": True, "username": username, "credentials_printed": False}


def main():
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("Run interactively in the SSH terminal; password input requires a terminal")
    username = input("Administrator username [admin]: ").strip() or "admin"
    password = getpass.getpass(
        "Administrator password (hidden, at least 8 characters): "
    )
    confirmation = getpass.getpass("Repeat password (hidden): ")
    if password != confirmation:
        parser.error("Passwords do not match; configuration not changed")
    print(json.dumps(configure(args.env_file, username, password)))


if __name__ == "__main__":
    main()
