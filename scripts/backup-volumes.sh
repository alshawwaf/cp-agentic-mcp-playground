#!/bin/sh
# scripts/backup-volumes.sh: back up the Check Point AI agent lab into one encrypted archive.
#
#   ./scripts/backup-volumes.sh [--output-dir DIR] [--volumes NAME,...] [--include-models]
#                               [--passphrase-file FILE] [--no-encrypt] [--retention-days N]
#
# What it saves (for the Compose project of this directory, whatever its name):
#   - every Docker volume of the project (found by its Compose label), except the Ollama model
#     volume, which can be downloaded again (--include-models adds it)
#   - the ./flowise_data and ./n8n/shared folders (Flowise keeps its database and keys there)
#   - .env (n8n can read its stored credentials only with the N8N_ENCRYPTION_KEY in it)
#   - a consistent SQL dump of Postgres (pg_dumpall) when the postgres service is running
# The archive is encrypted with AES-256 (openssl, PBKDF2) because it holds secrets and lab data.
# Passphrase: BACKUP_PASSPHRASE, --passphrase-file, or typed twice (hidden). Keep it: without it the
# backup cannot be restored. Stop the lab first (docker compose stop) for a fully consistent copy.
# Restore with: ./scripts/restore-volumes.sh <archive>
# Needs docker, sh and openssl (macOS and Linux include it). No secret is printed.

set -u
umask 077

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P) || exit 2
LAB_ROOT=$(cd "$SCRIPT_DIR/.." && pwd -P) || exit 2
# shellcheck source=SCRIPTDIR/lib/labenv.sh
. "$SCRIPT_DIR/lib/labenv.sh"

HELPER_IMAGE="${LAB_BACKUP_IMAGE:-busybox:1.37.0}"
out_dir=
only=
include_models=0
encrypt=1
pass_file=
retention=30

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case $1 in
    --output-dir) [ $# -ge 2 ] || lab_die "--output-dir needs a directory" 2; out_dir=$2; shift ;;
    --volumes) [ $# -ge 2 ] || lab_die "--volumes needs names" 2; only=$2; shift ;;
    --include-models) include_models=1 ;;
    --no-encrypt) encrypt=0 ;;
    --passphrase-file) [ $# -ge 2 ] || lab_die "--passphrase-file needs a file" 2; pass_file=$2; shift ;;
    --retention-days) [ $# -ge 2 ] || lab_die "--retention-days needs a number" 2; retention=$2; shift ;;
    --project-dir) [ $# -ge 2 ] || lab_die "--project-dir needs a directory" 2
      LAB_ROOT=$(cd "$2" && pwd -P) || lab_die "no such directory: $2" 2; shift ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
case $retention in '' | *[!0-9]*) lab_die "--retention-days needs a whole number" 2 ;; esac
[ -n "$out_dir" ] || out_dir="$LAB_ROOT/backups"

lab_have_docker || lab_die "Docker is not installed or not on PATH."
lab_docker_up || lab_die "Docker is not running."
mkdir -p "$out_dir" || lab_die "cannot create $out_dir"
out_dir=$(cd "$out_dir" && pwd -P)
chmod 700 "$out_dir" 2>/dev/null

work=
lab_tmp_init
cleanup() {
  [ -n "$work" ] && [ -d "$work" ] && rm -rf "$work"
  work=
  lab_tmp_cleanup
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM HUP

# ---------------------------------------------------------------------------- passphrase
if [ "$encrypt" = 1 ]; then
  command -v openssl >/dev/null 2>&1 || lab_die "openssl is required to encrypt the backup (or pass --no-encrypt to write an unencrypted archive)."
  if [ -n "$pass_file" ]; then
    [ -r "$pass_file" ] || lab_die "cannot read $pass_file"
    LAB_BACKUP_PASSPHRASE=$(head -n 1 "$pass_file")
  elif [ -n "${BACKUP_PASSPHRASE:-}" ]; then
    LAB_BACKUP_PASSPHRASE=$BACKUP_PASSPHRASE
  elif [ -t 0 ]; then
    _saved=$(stty -g 2>/dev/null)
    trap 'stty "$_saved" 2>/dev/null; cleanup; exit 130' INT TERM HUP
    stty -echo 2>/dev/null
    printf 'Backup passphrase (at least 12 characters, hidden): '
    IFS= read -r LAB_BACKUP_PASSPHRASE || LAB_BACKUP_PASSPHRASE=
    printf '\nType it again: '
    IFS= read -r _again || _again=
    stty "$_saved" 2>/dev/null || stty echo
    printf '\n'
    [ "$LAB_BACKUP_PASSPHRASE" = "$_again" ] || lab_die "the two entries differ."
    unset _again
  else
    lab_die "set BACKUP_PASSPHRASE, use --passphrase-file FILE, or run in a terminal (or pass --no-encrypt)." 2
  fi
  [ ${#LAB_BACKUP_PASSPHRASE} -ge 12 ] || lab_die "the backup passphrase must be at least 12 characters."
  export LAB_BACKUP_PASSPHRASE
else
  lab_warn "--no-encrypt: the archive will hold secrets and lab data in plain form. Store it encrypted."
fi

# ---------------------------------------------------------------------------- what to save
project=$(lab_project_name)
[ -n "$project" ] || lab_die "cannot tell the Compose project name of $LAB_ROOT"
lab_say "Backing up Compose project '$project' ($LAB_ROOT)"

work=$(mktemp -d "$out_dir/.lab-backup-work.XXXXXX") || lab_die "cannot create a work directory in $out_dir"
chmod 700 "$work"
mkdir -p "$work/volumes" "$work/bind" "$work/env" "$work/postgres"

running=$(docker ps -q --filter "label=com.docker.compose.project=$project" 2>/dev/null </dev/null | grep -c .)
if [ "$running" -gt 0 ]; then
  lab_warn "$running containers of this lab are running. Files that change during the copy (databases)"
  lab_say "  may be inconsistent. For a clean copy stop the lab first: docker compose stop"
fi

failed=0
saved=

# Postgres SQL dump (consistent even while running).
want_dump=1
if [ -n "$only" ]; then case ",$only," in *",postgres_storage,"* | *",postgres-dump,"*) ;; *) want_dump=0 ;; esac; fi
if [ "$running" -gt 0 ] && [ "$want_dump" = 1 ]; then
  _pg_user=
  if lab_env_load "$LAB_ROOT/.env" E_; then _pg_user=$(lab_get E_ POSTGRES_USER); fi
  if lab_compose ps --status running --services 2>/dev/null </dev/null | grep -qx postgres; then
    if lab_compose exec -T postgres pg_dumpall -U "${_pg_user:-admin}" 2>/dev/null </dev/null | gzip > "$work/postgres/dumpall.sql.gz" \
       && [ "$(gzip -dc "$work/postgres/dumpall.sql.gz" | head -c 100 | wc -c | tr -d ' ')" -gt 0 ]; then
      lab_say "  ok    Postgres SQL dump (pg_dumpall)"
      saved="$saved postgres-dump"
    else
      rm -f "$work/postgres/dumpall.sql.gz"
      lab_warn "the Postgres SQL dump failed; the postgres volume copy is still included."
    fi
  fi
fi

# Docker volumes of the project, by Compose label (works for any project name).
vols=$(docker volume ls -q --filter "label=com.docker.compose.project=$project" 2>/dev/null </dev/null)
[ -n "$vols" ] || lab_warn "no Docker volumes found for project '$project' (has the lab been started here?)"
for vol in $vols; do
  key=$(docker volume inspect -f '{{index .Labels "com.docker.compose.volume"}}' "$vol" 2>/dev/null </dev/null)
  [ -n "$key" ] || key=${vol#"${project}_"}
  if [ -n "$only" ]; then
    case ",$only," in *",$key,"* | *",$vol,"*) ;; *) continue ;; esac
  fi
  if [ "$key" = ollama_storage ] && [ "$include_models" = 0 ]; then
    lab_say "  skip  $vol (Ollama models: downloaded again on start; --include-models saves them)"
    continue
  fi
  if docker run --rm --cpus 1 --memory 256m --network none -v "$vol":/source:ro -v "$work/volumes":/backup \
       "$HELPER_IMAGE" tar czf "/backup/$key.tar.gz" -C /source . >/dev/null 2>&1 </dev/null; then
    lab_say "  ok    volume $vol"
    saved="$saved $key"
  else
    lab_say "  FAIL  volume $vol"
    failed=$((failed + 1))
  fi
done

# Bind-mounted lab data (tar runs in a container: the files may belong to root).
for dir in flowise_data n8n/shared; do
  [ -d "$LAB_ROOT/$dir" ] || continue
  if [ -n "$only" ]; then case ",$only," in *",$dir,"*) ;; *) continue ;; esac; fi
  name=$(printf '%s' "$dir" | tr '/' '_')
  if docker run --rm --cpus 1 --memory 256m --network none -v "$LAB_ROOT/$dir":/source:ro -v "$work/bind":/backup \
       "$HELPER_IMAGE" tar czf "/backup/$name.tar.gz" -C /source . >/dev/null 2>&1 </dev/null; then
    lab_say "  ok    folder ./$dir"
    saved="$saved $dir"
  else
    lab_say "  FAIL  folder ./$dir"
    failed=$((failed + 1))
  fi
done

if [ -f "$LAB_ROOT/.env" ]; then
  cp "$LAB_ROOT/.env" "$work/env/.env" && lab_say "  ok    .env"
fi

[ "$failed" -eq 0 ] || lab_die "$failed item(s) could not be saved; no archive was written."
[ -n "$saved" ] || lab_die "nothing to back up; no archive was written."

names_in() {
  for _f in "$1"/*.tar.gz; do [ -f "$_f" ] && printf '%s ' "$(basename "$_f" .tar.gz)"; done
}
{
  printf 'format=lab-backup-1\n'
  printf 'project=%s\n' "$project"
  printf 'created=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'volumes=%s\n' "$(names_in "$work/volumes")"
  printf 'folders=%s\n' "$(names_in "$work/bind")"
  printf 'encrypted=%s\n' "$encrypt"
} > "$work/manifest.txt"

stamp=$(date +%Y%m%d-%H%M%S)
if [ "$encrypt" = 1 ]; then
  archive="$out_dir/lab-backup-$project-$stamp.tar.gz.enc"
  (cd "$work" && tar czf - manifest.txt volumes bind env postgres) \
    | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -pass env:LAB_BACKUP_PASSPHRASE -out "$archive" \
    || { rm -f "$archive"; lab_die "could not write the encrypted archive."; }
  # Prove the archive decrypts and reads back before calling it a backup.
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:LAB_BACKUP_PASSPHRASE -in "$archive" \
    | tar tzf - >/dev/null 2>&1 || { rm -f "$archive"; lab_die "the archive did not read back; it was removed."; }
else
  archive="$out_dir/lab-backup-$project-$stamp.tar.gz"
  (cd "$work" && tar czf "$archive" manifest.txt volumes bind env postgres) || { rm -f "$archive"; lab_die "could not write the archive."; }
  tar tzf "$archive" >/dev/null 2>&1 || { rm -f "$archive"; lab_die "the archive did not read back; it was removed."; }
fi
chmod 600 "$archive"
cleanup

size=$(du -h "$archive" | cut -f1 | tr -d ' ')
lab_say ""
lab_say "Backup written: $archive ($size, mode 600)"
if [ "$retention" -gt 0 ]; then
  old=$(find "$out_dir" -maxdepth 1 -type f -name "lab-backup-$project-*" -mtime +"$retention" 2>/dev/null)
  if [ -n "$old" ]; then
    printf '%s\n' "$old" | while IFS= read -r f; do rm -f "$f"; done
    lab_say "Removed backups of '$project' older than $retention days from $out_dir."
  fi
fi
lab_say "Restore with: ./scripts/restore-volumes.sh \"$archive\""
[ "$encrypt" = 1 ] && lab_say "Keep the passphrase: the backup cannot be restored without it."
exit 0
