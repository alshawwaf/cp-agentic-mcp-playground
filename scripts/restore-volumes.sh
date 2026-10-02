#!/bin/sh
# scripts/restore-volumes.sh: restore a backup made by scripts/backup-volumes.sh.
#
#   ./scripts/restore-volumes.sh ARCHIVE [--volumes NAME,...] [--with-env] [--passphrase-file FILE] [--yes]
#
# Safe by design:
#   1. The whole archive is decrypted and checked BEFORE anything is changed.
#   2. It refuses to run while containers of the lab are running (stop them: docker compose stop).
#   3. It shows exactly what will be replaced and asks you to type the project name (--yes skips this).
#   4. Volumes are restored for THIS directory's Compose project, whatever its name (a backup taken
#      from another project name restores fine). Each volume is emptied completely (hidden files
#      too) and then filled from the backup; every volume is attempted and reported.
#   5. Folders (./flowise_data, ./n8n/shared) are not deleted: the current ones are renamed to
#      <name>.before-restore-<time> first.
#   6. .env is never replaced silently: the backup's copy is saved as .env.from-backup (mode 600).
#      --with-env makes it the active .env (the current one is kept as .env.before-restore).
#      n8n needs the N8N_ENCRYPTION_KEY from the backup's .env to read its restored credentials.
# Passphrase: BACKUP_PASSPHRASE, --passphrase-file, or typed (hidden).

set -u
umask 077

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P) || exit 2
LAB_ROOT=$(cd "$SCRIPT_DIR/.." && pwd -P) || exit 2
# shellcheck source=SCRIPTDIR/lib/labenv.sh
. "$SCRIPT_DIR/lib/labenv.sh"

HELPER_IMAGE="${LAB_BACKUP_IMAGE:-busybox:1.37.0}"
archive=
only=
with_env=0
assume_yes=0
pass_file=

usage() { sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case $1 in
    --volumes) [ $# -ge 2 ] || lab_die "--volumes needs names" 2; only=$2; shift ;;
    --with-env) with_env=1 ;;
    --yes | -y) assume_yes=1 ;;
    --passphrase-file) [ $# -ge 2 ] || lab_die "--passphrase-file needs a file" 2; pass_file=$2; shift ;;
    --project-dir) [ $# -ge 2 ] || lab_die "--project-dir needs a directory" 2
      LAB_ROOT=$(cd "$2" && pwd -P) || lab_die "no such directory: $2" 2; shift ;;
    -h | --help) usage; exit 0 ;;
    -*) printf 'Unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    *) [ -z "$archive" ] || lab_die "only one archive, please" 2; archive=$1 ;;
  esac
  shift
done
[ -n "$archive" ] || { usage >&2; exit 2; }
[ -f "$archive" ] || lab_die "backup file not found: $archive"
archive_dir=$(cd "$(dirname "$archive")" && pwd -P)
archive="$archive_dir/$(basename "$archive")"

lab_have_docker || lab_die "Docker is not installed or not on PATH."
lab_docker_up || lab_die "Docker is not running."

work=
saved_tty=
# shellcheck disable=SC2329  # called from the traps below
cleanup() {
  [ -n "$saved_tty" ] && stty "$saved_tty" 2>/dev/null
  [ -n "$work" ] && [ -d "$work" ] && rm -rf "$work"
  work=
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM HUP

project=$(lab_project_name)
[ -n "$project" ] || lab_die "cannot tell the Compose project name of $LAB_ROOT"

# ---------------------------------------------------------------------------- 1. decrypt and check
work_parent="$LAB_ROOT/backups"
mkdir -p "$work_parent" && chmod 700 "$work_parent" 2>/dev/null
work=$(mktemp -d "$work_parent/.lab-restore-work.XXXXXX") || lab_die "cannot create a work directory in $work_parent"
chmod 700 "$work"

case $archive in
  *.enc)
    command -v openssl >/dev/null 2>&1 || lab_die "openssl is required to decrypt this backup."
    if [ -n "$pass_file" ]; then
      [ -r "$pass_file" ] || lab_die "cannot read $pass_file"
      LAB_BACKUP_PASSPHRASE=$(head -n 1 "$pass_file")
    elif [ -n "${BACKUP_PASSPHRASE:-}" ]; then
      LAB_BACKUP_PASSPHRASE=$BACKUP_PASSPHRASE
    elif [ -t 0 ]; then
      saved_tty=$(stty -g 2>/dev/null)
      stty -echo 2>/dev/null
      printf 'Backup passphrase (hidden): '
      IFS= read -r LAB_BACKUP_PASSPHRASE || LAB_BACKUP_PASSPHRASE=
      stty "$saved_tty" 2>/dev/null || stty echo
      saved_tty=
      printf '\n'
    else
      lab_die "set BACKUP_PASSPHRASE or use --passphrase-file FILE." 2
    fi
    export LAB_BACKUP_PASSPHRASE
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:LAB_BACKUP_PASSPHRASE -in "$archive" 2>/dev/null \
      | (cd "$work" && tar xzf - 2>/dev/null) \
      || lab_die "cannot decrypt or read $archive (wrong passphrase or damaged file). Nothing was changed." ;;
  *)
    (cd "$work" && tar xzf "$archive" 2>/dev/null) || lab_die "cannot read $archive. Nothing was changed." ;;
esac
[ -f "$work/manifest.txt" ] || lab_die "$archive is not a lab backup (no manifest.txt). Nothing was changed."
src_project=$(sed -n 's/^project=//p' "$work/manifest.txt")
created=$(sed -n 's/^created=//p' "$work/manifest.txt")

vol_keys=
for f in "$work"/volumes/*.tar.gz; do
  [ -f "$f" ] || continue
  key=$(basename "$f" .tar.gz)
  if [ -n "$only" ]; then case ",$only," in *",$key,"* | *",${project}_$key,"*) ;; *) continue ;; esac; fi
  gzip -t "$f" 2>/dev/null || lab_die "volume $key in the backup is damaged. Nothing was changed."
  vol_keys="$vol_keys $key"
done
folders=
for f in "$work"/bind/*.tar.gz; do
  [ -f "$f" ] || continue
  name=$(basename "$f" .tar.gz)
  dir=$(printf '%s' "$name" | sed 's/^n8n_shared$/n8n\/shared/')
  if [ -n "$only" ]; then case ",$only," in *",$dir,"* | *",$name,"*) ;; *) continue ;; esac; fi
  gzip -t "$f" 2>/dev/null || lab_die "folder $dir in the backup is damaged. Nothing was changed."
  folders="$folders $dir"
done
[ -n "$vol_keys$folders" ] || lab_die "the backup holds nothing to restore${only:+ for --volumes $only}. Nothing was changed."

# ---------------------------------------------------------------------------- 2. lab must be stopped
running=$(docker ps --format '{{.Names}}' --filter "label=com.docker.compose.project=$project" 2>/dev/null </dev/null | tr '\n' ' ')
if [ -n "$(printf '%s' "$running" | tr -d ' ')" ]; then
  lab_say "These containers of project '$project' are running: $running"
  lab_die "stop the lab first (docker compose stop), then run the restore again. Nothing was changed."
fi

# ---------------------------------------------------------------------------- 3. plan + confirmation
lab_say "Restore plan"
lab_say "  Backup:  $archive"
lab_say "           from project '$src_project', taken $created"
lab_say "  Target:  Compose project '$project' in $LAB_ROOT"
for key in $vol_keys; do
  if docker volume inspect "${project}_$key" >/dev/null 2>&1 </dev/null; then
    lab_say "  REPLACE volume ${project}_$key (its current content is deleted)"
  else
    lab_say "  create  volume ${project}_$key"
  fi
done
for dir in $folders; do
  if [ -e "$LAB_ROOT/$dir" ]; then lab_say "  replace folder ./$dir (current one renamed, not deleted)"
  else lab_say "  create  folder ./$dir"; fi
done
if [ -f "$work/env/.env" ]; then
  if [ -f "$LAB_ROOT/.env" ] && cmp -s "$work/env/.env" "$LAB_ROOT/.env"; then lab_say "  keep    .env (the backup holds the same file)"
  elif [ "$with_env" = 1 ]; then lab_say "  replace .env (current one kept as .env.before-restore)"
  else lab_say "  save    the backup's .env as .env.from-backup (your .env stays active)"; fi
fi
[ -f "$work/postgres/dumpall.sql.gz" ] && lab_say "  note    the backup also holds a Postgres SQL dump (kept as backups/postgres-dumpall-<time>.sql.gz)"
if [ "$assume_yes" = 0 ]; then
  [ -t 0 ] || lab_die "run in a terminal to confirm, or pass --yes. Nothing was changed." 2
  printf 'Type the project name (%s) to continue: ' "$project"
  IFS= read -r answer || answer=
  [ "$answer" = "$project" ] || lab_die "not confirmed. Nothing was changed." 1
fi

# ---------------------------------------------------------------------------- 4. restore
stamp=$(date +%Y%m%d-%H%M%S)
failed=0
for key in $vol_keys; do
  vol="${project}_$key"
  if ! docker volume inspect "$vol" >/dev/null 2>&1 </dev/null; then
    docker volume create --label "com.docker.compose.project=$project" --label "com.docker.compose.volume=$key" \
      "$vol" >/dev/null 2>&1 </dev/null || { lab_say "  FAIL  cannot create volume $vol"; failed=$((failed + 1)); continue; }
  fi
  if docker run --rm --cpus 1 --memory 256m --network none -v "$vol":/target -v "$work/volumes":/backup:ro "$HELPER_IMAGE" \
       sh -c 'rm -rf /target/..?* /target/.[!.]* /target/* && tar xzf "/backup/$1.tar.gz" -C /target' sh "$key" \
       >/dev/null 2>&1 </dev/null; then
    lab_say "  ok    volume $vol"
  else
    lab_say "  FAIL  volume $vol"
    failed=$((failed + 1))
  fi
done
for dir in $folders; do
  name=$(printf '%s' "$dir" | tr '/' '_')
  if [ -e "$LAB_ROOT/$dir" ]; then
    mv "$LAB_ROOT/$dir" "$LAB_ROOT/$dir.before-restore-$stamp" || { lab_say "  FAIL  cannot move ./$dir aside"; failed=$((failed + 1)); continue; }
  fi
  mkdir -p "$LAB_ROOT/$dir"
  if docker run --rm --cpus 1 --memory 256m --network none -v "$LAB_ROOT/$dir":/target -v "$work/bind":/backup:ro "$HELPER_IMAGE" \
       tar xzf "/backup/$name.tar.gz" -C /target >/dev/null 2>&1 </dev/null; then
    lab_say "  ok    folder ./$dir"
    [ -e "$LAB_ROOT/$dir.before-restore-$stamp" ] && lab_say "        previous content: ./$dir.before-restore-$stamp"
  else
    lab_say "  FAIL  folder ./$dir"
    failed=$((failed + 1))
  fi
done
if [ -f "$work/env/.env" ]; then
  if [ "$with_env" = 1 ]; then
    [ -f "$LAB_ROOT/.env" ] && cp "$LAB_ROOT/.env" "$LAB_ROOT/.env.before-restore"
    cp "$work/env/.env" "$LAB_ROOT/.env" && chmod 600 "$LAB_ROOT/.env" && lab_say "  ok    .env restored (previous one: .env.before-restore)"
  elif [ -f "$LAB_ROOT/.env" ] && cmp -s "$work/env/.env" "$LAB_ROOT/.env"; then
    lab_say "  ok    .env in the backup is the same as the current one"
  else
    cp "$work/env/.env" "$LAB_ROOT/.env.from-backup" && chmod 600 "$LAB_ROOT/.env.from-backup"
    lab_say "  ok    the backup's .env saved as .env.from-backup. If the restored n8n cannot read its"
    lab_say "        credentials, copy N8N_ENCRYPTION_KEY (and POSTGRES_PASSWORD) from it into .env."
  fi
fi
if [ -f "$work/postgres/dumpall.sql.gz" ]; then
  cp "$work/postgres/dumpall.sql.gz" "$LAB_ROOT/backups/postgres-dumpall-$stamp.sql.gz"
fi

lab_say ""
if [ "$failed" -gt 0 ]; then
  lab_say "Restore finished with $failed failure(s). Fix them before you start the lab."
  exit 1
fi
lab_say "Restore complete. Start the lab: docker compose up -d   (with 1Password: op run --env-file=.env -- docker compose up -d)"
exit 0
