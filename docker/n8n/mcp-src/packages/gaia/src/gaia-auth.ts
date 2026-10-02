import { SessionContext } from '@chkp/mcp-utils';
import { isIP } from 'net';

export interface GaiaConnection {
  gatewayIp: string;
  port: number;
  user: string;
  password: string;
}

// ---------------------------------------------------------------------------
// LAB PATCH (D043/D096): headless credentials are bound to configured gateways.
//
// The lab's earlier headless fallback (commit ee1af1b) paired GAIA_USERNAME /
// GAIA_PASSWORD with ANY gateway_ip the model put in a tool call, so one
// injected sentence ("check the routes on 203.0.113.5") sent the gateway admin
// password to that host. Now:
//   * gateway_ip and port are validated (IP address or DNS name; 1-65535).
//   * Headless mode = any of GAIA_GATEWAY_IP, GAIA_USERNAME, GAIA_PASSWORD is
//     defined (the container deployment always defines them, maybe empty).
//     The browser dialog is then never used (nobody can answer it).
//   * The env credentials are used ONLY for GAIA_GATEWAY_IP and the hosts in
//     the optional GAIA_ALLOWED_GATEWAYS list (comma or space separated, same
//     credentials). Any other gateway_ip is refused - the env credentials are
//     never sent to it.
//   * Without any GAIA_* variable (desktop use) the upstream flow is unchanged:
//     the user types the credentials for that specific gateway in the dialog.
// ---------------------------------------------------------------------------
const GAIA_ENV_VARS = ['GAIA_GATEWAY_IP', 'GAIA_USERNAME', 'GAIA_PASSWORD'];
const HOST_LABEL = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/;

/** Validate and normalize a gateway address (IPv4, IPv6 or DNS name; no scheme, port or path). */
export function normalizeGatewayHost(value: unknown, source = 'gateway_ip'): string {
  if (typeof value !== 'string') {
    throw new Error(`${source} must be a string`);
  }
  let host = value.trim().toLowerCase();
  if (host.startsWith('[') && host.endsWith(']')) {
    host = host.slice(1, -1);
  }
  if (host.endsWith('.')) {
    host = host.slice(0, -1);
  }
  if (isIP(host)) {
    return host;
  }
  const labels = host.split('.');
  const isHostName = host.length > 0 && host.length <= 253 &&
    labels.every(label => HOST_LABEL.test(label)) &&
    /^[a-z]/.test(labels[labels.length - 1]); // rejects numeric look-alikes such as 010.0.0.1
  if (!isHostName) {
    throw new Error(`${source} ${JSON.stringify(value.slice(0, 100))} is not a valid IP address or host name`);
  }
  return host;
}

/** Host as used in an https:// URL (IPv6 literals need brackets). */
function urlHost(host: string): string {
  return isIP(host) === 6 ? `[${host}]` : host;
}

/** Validate a TCP port given as a number or numeric string. */
export function validateGatewayPort(value: unknown, source = 'port'): number {
  const port = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  if (typeof port !== 'number' || !Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`${source} must be a whole number from 1 to 65535`);
  }
  return port;
}

/** True when the operator configured Gaia through the environment (headless deployment). */
export function isHeadlessGaiaConfig(): boolean {
  return GAIA_ENV_VARS.some(name => process.env[name] !== undefined);
}

/** The configured gateway (GAIA_GATEWAY_IP), normalized; undefined when unset. */
export function configuredGatewayHost(): string | undefined {
  const value = process.env.GAIA_GATEWAY_IP?.trim();
  return value ? normalizeGatewayHost(value, 'GAIA_GATEWAY_IP') : undefined;
}

/** Hosts the env credentials may be sent to: GAIA_GATEWAY_IP plus GAIA_ALLOWED_GATEWAYS. */
function allowedGatewayHosts(): Set<string> {
  const hosts = new Set<string>();
  const primary = configuredGatewayHost();
  if (primary) {
    hosts.add(primary);
  }
  for (const entry of (process.env.GAIA_ALLOWED_GATEWAYS || '').split(/[\s,]+/)) {
    if (entry) {
      hosts.add(normalizeGatewayHost(entry, 'GAIA_ALLOWED_GATEWAYS entry'));
    }
  }
  return hosts;
}

const NOT_CONFIGURED =
  'Gaia is not configured on this MCP server. The administrator must set GAIA_GATEWAY_IP, ' +
  'GAIA_USERNAME and GAIA_PASSWORD for it and restart the server.';

function headlessConnection(requestedHost?: string, requestedPort?: number): GaiaConnection {
  const user = process.env.GAIA_USERNAME;
  const password = process.env.GAIA_PASSWORD;
  const allowed = allowedGatewayHosts();
  if (!user || !password || allowed.size === 0) {
    throw new Error(NOT_CONFIGURED);
  }
  const host = requestedHost ?? configuredGatewayHost();
  if (!host) {
    throw new Error('No gateway given: pass gateway_ip of an allowed gateway (GAIA_GATEWAY_IP is not set).');
  }
  if (!allowed.has(host)) {
    throw new Error(
      `Refusing to connect to gateway "${host}": this server's Gaia credentials are only used for the ` +
      'configured gateway (GAIA_GATEWAY_IP) and the hosts in GAIA_ALLOWED_GATEWAYS. Leave gateway_ip ' +
      'empty to use the configured gateway, or ask the administrator to allow this host.'
    );
  }
  const envPort = process.env.GAIA_GATEWAY_PORT?.trim();
  return {
    gatewayIp: urlHost(host),
    port: requestedPort ?? (envPort ? validateGatewayPort(envPort, 'GAIA_GATEWAY_PORT') : 443),
    user,
    password,
  };
}

/**
 * Get gateway connection details (IP, port, credentials) with automatic prompting
 */
export async function getGaiaConnection(
  gatewayIp?: string,
  port?: number,
  extra?: any
): Promise<GaiaConnection> {

  // LAB PATCH (D043/D096): validate model-supplied input before any use.
  const requestedHost = gatewayIp !== undefined && gatewayIp !== null && String(gatewayIp).trim() !== ''
    ? normalizeGatewayHost(gatewayIp)
    : undefined;
  const requestedPort = port !== undefined && port !== null ? validateGatewayPort(port) : undefined;

  if (isHeadlessGaiaConfig()) {
    return headlessConnection(requestedHost, requestedPort);
  }
  gatewayIp = requestedHost ? urlHost(requestedHost) : undefined;
  port = requestedPort;

  // Step 1: Get gateway IP and port if not provided
  let connectionDetails: { gatewayIp: string; port: number };
  
  if (!gatewayIp) {
    // Prompt for gateway connection details
    const gatewayResult = await SessionContext.getOrPromptUserData({
      cacheKey: 'default_gateway_connection',
      dialogTitle: "GAIA Gateway Connection",
      dialogMessage: "Please provide the gateway connection details:",
      customFields: [
        {
          name: "gateway_ip",
          label: "Gateway IP Address",
          type: "text",
          placeholder: "e.g., 192.168.1.1",
          required: true
        },
        {
          name: "port", 
          label: "Port",
          type: "number",
          placeholder: "443",
          defaultValue: "443",
          required: true
        }
      ],
      expirationMinutes: 60 // Cache gateway selection for 1 hour
    }, extra);
    
    if (gatewayResult.cancelled) {
      throw new Error('Gateway connection details cancelled by user');
    }
    
    connectionDetails = {
      gatewayIp: gatewayResult.data.gateway_ip,
      port: parseInt(gatewayResult.data.port) || 443
    };
  } else {
    connectionDetails = {
      gatewayIp,
      port: port || 443
    };
  }

  // Step 2: Get credentials for this specific gateway+port combination
  const connectionKey = `${connectionDetails.gatewayIp}:${connectionDetails.port}`;
  const cacheKey = `gaia_creds_${connectionKey.replace(/[:.]/g, '_')}`;
  
  const credentialsResult = await SessionContext.getOrPromptUserData({
    cacheKey,
    dialogTitle: `GAIA Authentication`,
    dialogMessage: `Please provide credentials for gateway: ${connectionKey}`,
    expirationMinutes: 15, // 15 minutes as suggested
    customFields: [
      {
        name: "address",
        label: "Address", 
        type: "text",
        defaultValue: connectionKey, // Pre-fill with gateway:port
        required: true,
        placeholder: connectionKey
      },
      {
        name: "user",
        label: "Username",
        type: "text",
        required: true,
        placeholder: "User"
      },
      {
        name: "password",
        label: "Password",
        type: "password",
        required: true,
        placeholder: "Password"
      }
    ]
  }, extra);
  
  if (credentialsResult.cancelled) {
    throw new Error('Authentication cancelled by user');
  }
  
  return {
    gatewayIp: connectionDetails.gatewayIp,
    port: connectionDetails.port,
    user: credentialsResult.data.user,
    password: credentialsResult.data.password
  };
}

/**
 * Clear cached credentials for a specific gateway+port
 */
export function clearGaiaCredentials(gatewayIp: string, port: number, extra: any) {
  const connectionKey = `${gatewayIp}:${port}`;
  const cacheKey = `gaia_creds_${connectionKey.replace(/[:.]/g, '_')}`;
  SessionContext.clearUserData(cacheKey, extra);
}

/**
 * Clear default gateway connection cache
 */
export function clearDefaultGateway(extra: any) {
  SessionContext.clearUserData('default_gateway_connection', extra);
}