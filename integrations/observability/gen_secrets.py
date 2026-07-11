#!/usr/bin/env python3
"""Generate the Langfuse self-hosting secrets as ready-to-paste .env lines.

Stdlib only (no pip, no openssl needed). Mirrors the values Langfuse v2 expects:

  NEXTAUTH_SECRET          openssl rand -base64 32   (session validation)
  SALT                     openssl rand -base64 32   (API-key hashing)
  LANGFUSE_ENCRYPTION_KEY  openssl rand -hex 32      (256-bit, stored-secret crypto)

Usage:
  python3 gen_secrets.py            # print the three secret lines
  python3 gen_secrets.py --with-keys  # also print a pk-lf-/sk-lf- project key pair
                                      # (only useful for Langfuse headless init)

Nothing is written to disk and nothing is sent anywhere — copy the lines you want
into your .env. Re-run to get fresh values.
"""
import argparse
import base64
import secrets


def rand_base64_32() -> str:
    """32 random bytes, base64 — matches `openssl rand -base64 32`."""
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def rand_hex_32() -> str:
    """32 random bytes, hex (64 chars / 256-bit) — matches `openssl rand -hex 32`."""
    return secrets.token_bytes(32).hex()


def lf_key(prefix: str) -> str:
    """A Langfuse-style project key, e.g. pk-lf-<40 hex> / sk-lf-<40 hex>."""
    return "{0}-lf-{1}".format(prefix, secrets.token_hex(20))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-keys", action="store_true",
                        help="also emit a LANGFUSE_PUBLIC_KEY/SECRET_KEY pair")
    args = parser.parse_args()

    print("# --- paste into .env (fresh random values) ---")
    print("NEXTAUTH_SECRET={0}".format(rand_base64_32()))
    print("SALT={0}".format(rand_base64_32()))
    print("LANGFUSE_ENCRYPTION_KEY={0}".format(rand_hex_32()))

    if args.with_keys:
        print("LANGFUSE_PUBLIC_KEY={0}".format(lf_key("pk")))
        print("LANGFUSE_SECRET_KEY={0}".format(lf_key("sk")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
