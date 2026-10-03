// Gaia API client that extends OnPremAPIClient with custom URL path
import { OnPremAPIClient } from '@chkp/quantum-infra';
import type { TlsTrustSettings } from '@chkp/quantum-infra';
import { configuredGatewayHost, normalizeGatewayHost } from './gaia-auth.js';
import type { GaiaConnection } from './gaia-auth.js';

/**
 * API client for Gaia systems
 * Extends OnPremAPIClient but uses /gaia_api/ instead of /web_api/
 * Note: Gaia API only supports username/password authentication, not API keys
 */
export class GaiaApiClient extends OnPremAPIClient {
  private readonly connection: GaiaConnection;

  constructor(connection: GaiaConnection) {
    // Call parent constructor - Gaia doesn't use API keys, so pass undefined
    super(
      undefined, 
      connection.gatewayIp.trim(), 
      connection.port.toString(), 
      connection.user, 
      connection.password
    );
    
    // Store connection details
    this.connection = connection;
  }

  /**
   * Override getHost() to use Gaia API path with configurable port
   */
  getHost(): string {
    return `https://${this.connection.gatewayIp}:${this.connection.port}/gaia_api`;
  }

  /**
   * LAB PATCH (D053/D096): TLS trust for Gaia gateways (certificate always
   * verified). GAIA_CA_CERT adds a CA file to Node's trust store;
   * GAIA_TLS_SERVERNAME applies only to the configured GAIA_GATEWAY_IP.
   */
  protected tlsTrust(): TlsTrustSettings {
    let isConfiguredGateway = false;
    try {
      isConfiguredGateway = configuredGatewayHost() === normalizeGatewayHost(this.connection.gatewayIp);
    } catch {
      isConfiguredGateway = false;
    }
    return {
      caFile: process.env.GAIA_CA_CERT,
      servername: isConfiguredGateway ? process.env.GAIA_TLS_SERVERNAME : undefined,
    };
  }

  /**
   * Get connection info for logging
   */
  getConnectionInfo(): string {
    return `${this.connection.gatewayIp}:${this.connection.port}`;
  }

  // Override inherited methods to ensure TypeScript recognizes them
  async login(): Promise<string> {
    return super.login();
  }

  async callApi(method: string, uri: string, data: Record<string, any> = {}, params?: Record<string, any>): Promise<any> {
    return super.callApi(method, uri, data, params);
  }
}