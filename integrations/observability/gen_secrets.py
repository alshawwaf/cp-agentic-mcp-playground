#!/usr/bin/env python3
"""Fill in the Langfuse and LiteLLM secrets that are still blank in .env.

./setup.sh generates every lab secret. Use this helper when you made .env by hand
(for example `cp .env-example .env`) and Langfuse will not start or tracing is off.

It sets, only where the value is blank or a placeholder such as change_me:
  NEXTAUTH_SECRET          32 random bytes, base64   (Langfuse session signing)
  SALT                     32 random bytes, base64   (Langfuse API-key hashing)
  LANGFUSE_ENCRYPTION_KEY  32 random bytes, hex      (Langfuse encryption at rest)
  LANGFUSE_PUBLIC_KEY      pk-lf-<random>            (project key pair that Langfuse
  LANGFUSE_SECRET_KEY      sk-lf-<random>             creates on its first start)
  LITELLM_MASTER_KEY       sk-<random>               (the key every agent uses for lab-chat;
                                                      the old public training value is replaced)

A value that is already set is never changed: changing SALT, LANGFUSE_ENCRYPTION_KEY or
the project keys after Langfuse's first start breaks sign-in, stored settings or tracing.
1Password references (op://...) are left alone. The file keeps its comments and order,
is written with mode 600, and no value is ever printed: the output lists names only.

Usage (from the repository root):
  python3 integrations/observability/gen_secrets.py              # fill blanks in ./.env
  python3 integrations/observability/gen_secrets.py --dry-run    # only list what would be set
  python3 integrations/observability/gen_secrets.py --env-file path/to/.env
Then run: docker compose up -d langfuse litellm
Stdlib only. Nothing is sent anywhere.
"""
import argparse
import base64
import os
import secrets
import sys
import tempfile

PUBLIC_VALUES = {"LITELLM_MASTER_KEY": {"sk-cp-litellm-training-key"}}
PLACEHOLDERS = {"none", "null", "unset", "changeme", "change-me"}


def _b64(n=32):
    return base64.b64encode(secrets.token_bytes(n)).decode("ascii")


GENERATORS = (
    ("NEXTAUTH_SECRET", _b64),
    ("SALT", _b64),
    ("LANGFUSE_ENCRYPTION_KEY", lambda: secrets.token_hex(32)),
    ("LANGFUSE_PUBLIC_KEY", lambda: "pk-lf-" + secrets.token_hex(20)),
    ("LANGFUSE_SECRET_KEY", lambda: "sk-lf-" + secrets.token_hex(20)),
    ("LITELLM_MASTER_KEY", lambda: "sk-" + secrets.token_hex(24)),
)


def _parse(line):
    """(name, value) for a NAME=value line, else (None, None)."""
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        return None, None
    if text.startswith("export "):
        text = text[len("export "):].lstrip()
    name, value = text.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return name.strip(), value


def _needs_value(name, value):
    if value is None:
        return True
    low = value.strip().lower()
    if low.startswith("op://"):
        return False
    return (not low or low in PLACEHOLDERS or low.startswith("change_me") or low.startswith("<")
            or value in PUBLIC_VALUES.get(name, ()))


def fill(path, dry_run=False):
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    last = {}  # name -> index of its last definition (the one Docker Compose uses)
    for i, line in enumerate(lines):
        name, _ = _parse(line)
        if name:
            last[name] = i

    set_names, kept, external = [], [], []
    appended = []
    for name, make in GENERATORS:
        value = _parse(lines[last[name]])[1] if name in last else None
        if value is not None and value.strip().lower().startswith("op://"):
            external.append(name)
            continue
        if not _needs_value(name, value):
            kept.append(name)
            continue
        set_names.append(name)
        if name in last:
            lines[last[name]] = "{0}={1}".format(name, make())
        else:
            appended.append("{0}={1}".format(name, make()))

    if set_names and not dry_run:
        if appended:
            lines += ["", "# Added by integrations/observability/gen_secrets.py"] + appended
        real = os.path.realpath(path)
        fd, tmp = tempfile.mkstemp(prefix=".env.", dir=os.path.dirname(real))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, real)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    return set_names, kept, external


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=".env", help="the .env file to update (default: ./.env)")
    parser.add_argument("--dry-run", action="store_true", help="list what would be set; change nothing")
    parser.add_argument("--with-keys", action="store_true", help=argparse.SUPPRESS)  # old flag; keys are always included
    args = parser.parse_args()

    if not os.path.isfile(args.env_file):
        print("ERROR: {0} not found. Run ./setup.sh (it creates .env with every secret), or copy "
              ".env-example to .env first.".format(args.env_file), file=sys.stderr)
        return 2
    set_names, kept, external = fill(args.env_file, dry_run=args.dry_run)
    verb = "would set" if args.dry_run else "set"
    print("{0}: {1}".format(verb, ", ".join(set_names) or "nothing (every value is already present)"))
    if kept:
        print("kept (already set): {0}".format(", ".join(kept)))
    if external:
        print("left to 1Password (op:// references): {0}".format(", ".join(external)))
    if set_names and not args.dry_run:
        print("{0} updated (mode 600). Next: docker compose up -d langfuse litellm".format(args.env_file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
