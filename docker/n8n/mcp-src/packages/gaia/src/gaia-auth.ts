import { SessionContext } from '@chkp/mcp-utils';

export interface GaiaConnection {
  gatewayIp: string;
  port: number;
  user: string;
  password: string;
}

/**
 * Get gateway connection details (IP, port, credentials) with automatic prompting
 */
export async function getGaiaConnection(
  gatewayIp?: string,
  port?: number,
  extra?: any
): Promise<GaiaConnection> {

  // Headless env-credential fallback (added for the containerized HTTP-transport deployment).
  // Upstream, Gaia authenticates through an interactive localhost browser dialog, which cannot
  // run in a headless sidecar reached over the remote MCP gateway. When the operator configures
  // a default gateway via environment variables, use them directly and skip both dialogs. A
  // tool-supplied gateway_ip still takes precedence for the host; GAIA_USERNAME/GAIA_PASSWORD
  // apply to whichever host is used. Leave the env unset to keep the original dialog behaviour.
  const envIp = process.env.GAIA_GATEWAY_IP;
  const envUser = process.env.GAIA_USERNAME;
  const envPass = process.env.GAIA_PASSWORD;
  const envPort = process.env.GAIA_GATEWAY_PORT
    ? parseInt(process.env.GAIA_GATEWAY_PORT, 10)
    : undefined;
  const effectiveIp = gatewayIp || envIp;
  if (effectiveIp && envUser && envPass) {
    return {
      gatewayIp: effectiveIp,
      port: port || envPort || 443,
      user: envUser,
      password: envPass,
    };
  }

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