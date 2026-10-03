// LAB PATCH (D097, PATCHES.md section 8): file_path comes from the model, so only regular
// files under CPINFO_ALLOWED_DIRS (separated by ':' or ',', default /data/cpinfo, where the
// lab mounts ./n8n/shared) are opened (upstream opened any path, e.g. /proc/self/environ).
// Copied from packages/threat-emulation/src/lib/file-access.ts (PATCHES.md section 12) so
// both servers use the same checks without a cross-package dependency; keep them in sync.
import { promises as fs } from "fs";
import path from "path";
import { CpInfoIOError } from "./cpinfo-exceptions.js";

const DEFAULT_ALLOWED_DIRS = "/data/cpinfo";

export function allowedCpinfoDirs(): string[] {
  return (process.env.CPINFO_ALLOWED_DIRS || DEFAULT_ALLOWED_DIRS)
    .split(/[:,]/)
    .map(dir => dir.trim())
    .filter(dir => dir !== "")
    .map(dir => path.resolve(dir));
}

const isUnder = (candidate: string, root: string): boolean =>
  candidate === root || candidate.startsWith(root.endsWith(path.sep) ? root : root + path.sep);

// Real path of the closest parent that exists. path.dirname() does not normalize, so a
// '..' after a symlink is resolved by the file system, as it would be when opening.
async function nearestExistingParent(requested: string): Promise<string | undefined> {
  let current = path.dirname(requested);
  for (let i = 0; i < 4096; i++) {
    try {
      return await fs.realpath(current);
    } catch {
      const next = path.dirname(current);
      if (next === current) {
        return undefined;
      }
      current = next;
    }
  }
  return undefined;
}

/**
 * Resolve a model-supplied cpinfo path to the real path of a regular file under the
 * allowed directories, or throw CpInfoIOError. Symlinks and '..' are resolved by the file
 * system before the check. A relative path is resolved against the first allowed directory.
 * Paths outside get the same "Access denied" answer whether or not they exist; "File not
 * found" only when the closest existing parent is inside an allowed directory (no
 * file-existence oracle, also not through a symlinked directory planted inside).
 */
export async function resolveAllowedCpinfoPath(filePath: unknown): Promise<string> {
  if (typeof filePath !== "string" || filePath.trim() === "" || filePath.includes("\0")) {
    throw new CpInfoIOError("A cpinfo file path is required");
  }
  const roots = allowedCpinfoDirs();
  const realRoots = (await Promise.all(roots.map(root => fs.realpath(root).catch(() => undefined))))
    .filter((root): root is string => root !== undefined);
  const denied = new CpInfoIOError(
    `Access denied: ${filePath} is not a file under the allowed cpinfo directories (${roots.join(", ")}). ` +
    "Put the file there (in the lab: the n8n/shared folder) or ask the administrator to set CPINFO_ALLOWED_DIRS."
  );
  const inside = (candidate: string): boolean => realRoots.some(root => isUnder(candidate, root));

  // String concatenation, not path.join(): '..' must not be collapsed before symlinks are resolved.
  const requested = path.isAbsolute(filePath) ? filePath : `${roots[0] ?? DEFAULT_ALLOWED_DIRS}${path.sep}${filePath}`;
  let resolved: string;
  try {
    resolved = await fs.realpath(requested);
  } catch {
    const parent = await nearestExistingParent(requested);
    if (parent !== undefined && inside(parent)) {
      throw new CpInfoIOError(`File not found: ${filePath}`);
    }
    throw denied;
  }
  if (!inside(resolved)) {
    throw denied;
  }
  const stats = await fs.stat(resolved).catch(() => undefined);
  if (!stats || !stats.isFile()) {
    // Directories, FIFOs, sockets and devices are refused (a FIFO would block the read).
    throw denied;
  }
  return resolved;
}
