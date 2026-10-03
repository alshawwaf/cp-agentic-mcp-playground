// LAB PATCH (Threat Emulation file_path, PATCHES.md section 12): file_path comes from
// the model, and upload_file / scan_file send the file to the Check Point Threat
// Emulation cloud. Without a check, any file the server user can read (for example
// /proc/self/environ with the TE API key) could be uploaded. Only regular files under
// TE_ALLOWED_DIRS (separated by ':' or ',', default /data/shared, where the lab mounts
// ./n8n/shared) are opened. Same approach as the CPInfo patch (PATCHES.md section 8).
import { promises as fs } from 'fs';
import path from 'path';

const DEFAULT_ALLOWED_DIRS = '/data/shared';

export class FileAccessError extends Error {
  constructor(message: string, readonly notFound = false) {
    super(message);
    this.name = 'FileAccessError';
  }
}

export function allowedDirs(): string[] {
  return (process.env.TE_ALLOWED_DIRS || DEFAULT_ALLOWED_DIRS)
    .split(/[:,]/)
    .map(dir => dir.trim())
    .filter(dir => dir !== '')
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
 * Resolve a model-supplied file path to the real path of a regular file under the
 * allowed directories, or throw FileAccessError. Symlinks are resolved before the check.
 * A relative path is resolved against the first allowed directory. Paths outside get the
 * same "Access denied" answer whether or not they exist (no file-existence oracle).
 */
export async function resolveAllowedFile(filePath: unknown): Promise<string> {
  if (typeof filePath !== 'string' || filePath.trim() === '' || filePath.includes('\0')) {
    throw new FileAccessError('file_path is required');
  }
  const roots = allowedDirs();
  const realRoots = (await Promise.all(roots.map(root => fs.realpath(root).catch(() => undefined))))
    .filter((root): root is string => root !== undefined);
  const denied = new FileAccessError(
    `Access denied: ${filePath} is not a file under the allowed directories (${roots.join(', ')}). ` +
    'Put the file there (in the lab: the n8n/shared folder) or ask the administrator to set TE_ALLOWED_DIRS.'
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
      throw new FileAccessError(`File not found: ${filePath}`, true);
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
