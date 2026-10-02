// LAB PATCH (no secrets in logs): keys whose values are masked by redactSecrets().
const SECRET_KEY_PATTERN =
  /(pass(word)?|secret|token|api[-_]?key|apikey|access[-_]?key|private[-_]?key|authorization|cookie|credential|(^|[-_])sid$)/i;

/**
 * LAB PATCH (no secrets in logs): returns a copy of `value` that is safe to log.
 * Values under keys that look like credentials (API keys, passwords, tokens,
 * Authorization/Cookie headers, X-chkp-sid, ...) are replaced with "***".
 * Use it for every debug dump of headers, settings, CLI options or request data.
 */
export function redactSecrets(value: unknown, depth = 0): unknown {
  if (depth > 8 || value === null || typeof value !== 'object') {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(item => redactSecrets(item, depth + 1));
  }
  const result: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    result[key] = SECRET_KEY_PATTERN.test(key) && item !== undefined && item !== ''
      ? '***'
      : redactSecrets(item, depth + 1);
  }
  return result;
}
