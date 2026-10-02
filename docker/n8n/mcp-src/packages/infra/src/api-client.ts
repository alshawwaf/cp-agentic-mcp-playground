// API client implementation for Check Point MCP servers
import axios from 'axios';
import fs from 'fs';
import https from 'https';
import tls from 'tls';
import { redactSecrets } from '@chkp/mcp-utils';

// ---------------------------------------------------------------------------
// LAB PATCH (TLS verification, D053/D066/D096): certificate verification is
// always ON. Upstream turned verification off for every on-prem client, so the
// Management API key and the Gaia password went to unverified servers. There
// is deliberately no switch to turn verification off. To trust a self-signed or private-CA certificate:
//   * NODE_EXTRA_CA_CERTS=/path/ca.pem - Node adds the PEM file to its trust
//     store for every HTTPS client in the process (read once at start-up), or
//   * MANAGEMENT_CA_CERT=/path/ca.pem (GAIA_CA_CERT for the Gaia server) - the
//     PEM file is ADDED to Node's default trust store for these clients only.
// If the certificate does not name the address you connect to (for example a
// NAT or IP address), set MANAGEMENT_TLS_SERVERNAME (GAIA_TLS_SERVERNAME) to a
// DNS name the certificate does contain; it applies only to the configured
// MANAGEMENT_HOST (GAIA_GATEWAY_IP), and the certificate is still verified.
// ---------------------------------------------------------------------------
export interface TlsTrustSettings {
  caFile?: string;     // PEM file added to the default trust store
  servername?: string; // name the server certificate must be valid for
}

const verifyingAgents = new Map<string, https.Agent>();

/** Node's default CA list: bundled roots + NODE_EXTRA_CA_CERTS (+ system store if enabled). */
function defaultCaCertificates(): string[] {
  const getCACertificates = (tls as any).getCACertificates;
  if (typeof getCACertificates === 'function') {
    return getCACertificates('default');
  }
  const certificates = [...tls.rootCertificates];
  const extraFile = process.env.NODE_EXTRA_CA_CERTS;
  if (extraFile) {
    try { certificates.push(fs.readFileSync(extraFile, 'utf8')); } catch { /* Node already warned at start-up */ }
  }
  return certificates;
}

/**
 * LAB PATCH (D053): an https.Agent that always verifies the server certificate
 * (rejectUnauthorized: true, which also overrides NODE_TLS_REJECT_UNAUTHORIZED),
 * optionally trusting one extra CA file and checking against a given name.
 */
export function getVerifyingHttpsAgent(trust: TlsTrustSettings = {}): https.Agent {
  const caFile = trust.caFile?.trim() || '';
  const servername = trust.servername?.trim() || '';
  const key = `${caFile}|${servername}`;
  let agent = verifyingAgents.get(key);
  if (!agent) {
    const options: https.AgentOptions = { rejectUnauthorized: true };
    if (caFile) {
      let pem: string;
      try {
        pem = fs.readFileSync(caFile, 'utf8');
      } catch (error) {
        throw new Error(`Cannot read the CA certificate file ${caFile} (${(error as NodeJS.ErrnoException).code || (error as Error).message}).`);
      }
      if (!pem.includes('-----BEGIN CERTIFICATE-----')) {
        throw new Error(`The CA certificate file ${caFile} does not contain a PEM certificate.`);
      }
      options.ca = [...defaultCaCertificates(), pem]; // adds to, never replaces, the default trust store
    }
    if (servername) {
      options.servername = servername;
    }
    agent = new https.Agent(options);
    verifyingAgents.set(key, agent);
  }
  return agent;
}

const TLS_VERIFICATION_ERRORS = new Set([
  'DEPTH_ZERO_SELF_SIGNED_CERT', 'SELF_SIGNED_CERT_IN_CHAIN', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE',
  'UNABLE_TO_GET_ISSUER_CERT', 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY', 'CERT_UNTRUSTED', 'CERT_HAS_EXPIRED',
  'CERT_NOT_YET_VALID', 'CERT_SIGNATURE_FAILURE', 'CERT_REVOKED', 'ERR_TLS_CERT_ALTNAME_INVALID',
]);

/**
 * LAB PATCH (no secrets in logs): turn an axios error without a response into
 * a plain Error. The raw axios error carries the request config (the login
 * body with the API key or password, the X-chkp-sid header) and was logged as
 * a whole by callers.
 */
function requestFailure(error: any, method: string, url: string): Error {
  const code: string | undefined = error?.code;
  let message: string;
  if (code && TLS_VERIFICATION_ERRORS.has(code)) {
    message = `TLS certificate verification failed for ${url} (${code}: ${error?.message}). ` +
      'This server does not trust the certificate. Mount the server CA certificate (PEM) into the ' +
      'container and point NODE_EXTRA_CA_CERTS (or MANAGEMENT_CA_CERT / GAIA_CA_CERT) at it; if the ' +
      'certificate does not name this host, also set MANAGEMENT_TLS_SERVERNAME / GAIA_TLS_SERVERNAME.';
    console.error(message);
  } else {
    message = `API request ${method.toUpperCase()} ${url} failed: ${code ? `${code} ` : ''}${error?.message ?? String(error)}`;
  }
  const failure = new Error(message);
  (failure as any).code = code;
  return failure;
}

/**
 * Enum representing the types of authentication tokens
 */
export enum TokenType {
  API_KEY = "API_KEY",
  CI_TOKEN = "CI_TOKEN"
}

function getMainPackageUserAgent(): string {
  if (process.env.CP_MCP_MAIN_PKG) {
    if (process.env.CP_MCP_MAIN_PKG.includes("quantum-management-mcp")) {
      return "mgmt-mcp";
    }
  }
  return "Check Point MCP API Client";
}

/**
 * Response from an API client call
 */
export class ClientResponse {
  constructor(
    public status: number,
    public response: Record<string, any>
  ) {}
}

/**
 * Base class for API clients
 */
export abstract class APIClientBase {
  protected sid: string | null = null;
  protected sessionTimeout: number | null = null; // in seconds
  protected sessionStart: number | null = null;   // timestamp when session was created
  protected isMDS: boolean = false; // Whether this is an MDS environment
  private _debug?: boolean;

  constructor(
    protected readonly authToken: string = "",
    protected readonly tokenType: TokenType = TokenType.API_KEY // Default
  ) {}

  /**
   * Get the host URL for the API client
   */
  abstract getHost(): string;
    /**
   * Create an API client instance - generic method for creating clients
   */
  static create<T extends APIClientBase>(this: new (...args: any[]) => T, ...args: any[]): T {
    return new this(...args);
  }

  /**
   * Get headers for the API requests
   */
  protected getHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "User-Agent": getMainPackageUserAgent(),
    };
    
    // Only add the X-chkp-sid header if we have a valid session ID
    if (this.sid) {
      headers["X-chkp-sid"] = this.sid;
    }
    
    return headers;
  }

  /**
   * Get debug mode
   */
  get debug(): boolean {
    return !!this._debug;
  }

  /**
   * Set debug mode
   */
  set debug(value: boolean) {
    this._debug = value;
  }

 /**
   * Check if this client needs to perform login before making API calls
   */
  protected needsLogin(): boolean {
    return true;
  }

  /**
   * LAB PATCH (D053): the agent for this client's HTTPS requests. undefined
   * means axios' default agent, which verifies certificates against Node's
   * trust store (including NODE_EXTRA_CA_CERTS).
   */
  protected getHttpsAgent(): https.Agent | undefined {
    return undefined;
  }
  
  /**
   * Check if this client is in an MDS environment
   */
  async isMDSEnvironment(): Promise<boolean> {
    if (!this.hasValidSession()) {
      await this.login();
    }
    return this.isMDS;
  }

  /**
   * Check if the client has a valid session
   */
  hasValidSession(): boolean {
    return !!this.sid && !this.isSessionExpired();
  }

  /**
   * Get the current session ID (for debugging/logging purposes)
   */
  getSessionId(): string | null {
    return this.sid;
  }

  /**
   * Create a new API client instance with a specific session ID
   * Used for domain-specific clients in MDS environments
   */
  static createWithSid<T extends APIClientBase>(
    this: new (...args: any[]) => T,
    originalClient: T,
    sid: string
  ): T {
    // Create a new instance with the same configuration as the original
    const newClient = Object.create(Object.getPrototypeOf(originalClient));
    Object.assign(newClient, originalClient);
    
    // Set the new session ID
    newClient.sid = sid;
    newClient.sessionStart = Date.now();
    
    return newClient;
  }

  /**
   * Call an API endpoint
   */
  async callApi(
    method: string,
    uri: string,
    data: Record<string, any>,
    params?: Record<string, any>
  ): Promise<ClientResponse> {

    if (this.needsLogin() && (!this.sid || this.isSessionExpired())) {
      try {
        await this.login();
      }
      catch (error: any) {
        // If the error is already a ClientResponse, just return it directly
        if (error instanceof ClientResponse) {
          console.error(`Login failed with status ${error.status}:`, error.response);
          return error;
        }
        // For other types of errors, create a generic response
        console.error("Login failed with unexpected error:", error);
        return new ClientResponse(500, { error: "Authentication failed", message: error.message });
      }
    }

    // LAB PATCH (D053): certificate verification stays on (upstream turned it off for on-prem).
    const httpsAgent = this.getHttpsAgent();

    try {
      return await this.makeRequest(
          this.getHost(),
          method,
          uri,
          data,
          this.getHeaders(),
          params,
          httpsAgent
      );
    } catch (error: any) {
      // If we get a 401 error with "session" in the message, the session has expired on the server side
      // Reset the session and retry once
      if ((error.message?.includes('401') || error.response?.status === 401) && 
          error.message?.toLowerCase().includes('session')) {
        console.error("Session expired (401), resetting session and retrying...");
        this.sid = null;
        this.sessionStart = null;
        
        // Re-login and retry the request
        try {
          await this.login();
        } catch (loginError: any) {
          if (loginError instanceof ClientResponse) {
            console.error(`Login retry failed with status ${loginError.status}:`, loginError.response);
            return loginError;
          }
          console.error("Login retry failed with unexpected error:", loginError);
          throw loginError;
        }
        
        // Retry the original request with the new session
        return await this.makeRequest(
            this.getHost(),
            method,
            uri,
            data,
            this.getHeaders(),
            params,
            httpsAgent
        );
      }
      
      // For other errors, just re-throw the original error
      throw error;
    }
  }

  /**
   * Check if the session is expired based on sessionTimeout and sessionStart
   */
  protected isSessionExpired(): boolean {
    if (!this.sid || !this.sessionTimeout || !this.sessionStart) return true;
    const now = Date.now();
    // Add a small buffer (5 seconds) to avoid edge cases
    return now > (this.sessionStart + (this.sessionTimeout - 5) * 1000);
  }

  /**
   * Login to the API using the API key
   */
  async login(): Promise<string> {
    const apiTokenHeader = this.tokenType === TokenType.API_KEY ? "api-key" : "ci-token";
    const loginResp = await this.makeRequest(
      this.getHost(),
      "POST",
      "login",
      { [apiTokenHeader] : this.authToken },
      { "Content-Type": "application/json" }
    );
    if (loginResp.status !== 200 || !loginResp.response || !loginResp.response.sid) {
      throw loginResp;
    }
    this.sid = loginResp.response.sid;

    this.sessionTimeout = loginResp.response["session-timeout"] || null;
    this.sessionStart = Date.now();
    
    // Check if this is an MDS environment by calling get-session with the session UID
    if (loginResp.response.uid) {
      await this.detectMDS(loginResp.response.uid); // handleSelfSigned = false (default) for cloud
    }
    
    return loginResp.response.sid;
  }

  /**
   * Detect if this is an MDS environment by checking the session details
   */
  protected async detectMDS(sessionUid: string, handleSelfSigned: boolean = false): Promise<void> {
    try {
      // LAB PATCH (D053): handleSelfSigned no longer disables verification; the
      // client's verifying agent (and its trusted CA, if configured) is used.
      const httpsAgent = this.getHttpsAgent();
      
      const sessionResp = await this.makeRequest(
        this.getHost(),
        "POST", 
        "show-session",
        { uid: sessionUid },
        this.getHeaders(),
        null,
        httpsAgent
      );
      
      if (sessionResp.status === 200 && 
          sessionResp.response?.domain?.["domain-type"] === "mds") {
        this.isMDS = true;
      }
    } catch (error) {
      // If we can't determine MDS status, assume it's not MDS
      console.warn("Could not determine MDS status:", (error as Error)?.message ?? error);
      this.isMDS = false;
    }
  }

  /**
   * Make a request to a Check Point API
   */
  async makeRequest(
    host: string,
    method: string,
    uri: string,
    data: Record<string, any>,
    headers: Record<string, string> = {},
    params: Record<string, any> | null = null,
    httpsAgent?: https.Agent
  ): Promise<ClientResponse> {
    // Ensure uri doesn't start with a slash
    uri = uri.replace(/^\//, "");
    const url = `${host}/${uri}`;    
    const config: any = {
      method: method.toUpperCase(),
      url,
      headers,
      params: params || undefined
    };
    
    // Add httpsAgent if provided (for handling self-signed certificates)
    if (httpsAgent) {
      config.httpsAgent = httpsAgent;
    }

    // Only set data for non-GET requests
    if (method.toUpperCase() !== 'GET' && data !== undefined) {
      config.data = data;
    }

    console.error(`API Request: ${method} ${url}`);

    try {
      const response = await axios(config);
      return new ClientResponse(response.status, response.data as Record<string, any>);
    } catch (error: any) {
      if (error.response) {
        console.error(`❌ API Error (${error.response.status}):`);
        console.error('Headers:', redactSecrets(error.response.headers));
        console.error('Data:', redactSecrets(error.response.data));

        // Print the request details when debug is enabled
        // (LAB PATCH: secrets redacted - the login body carries the API key or password)
        if (this.debug) {
          console.error('Debug mode: Printing request details (secrets redacted):');
          console.error('Request Method:', method);
          console.error('Request URL:', url);
          console.error('Request Headers:', redactSecrets(config.headers));
          console.error('Request Data:', redactSecrets(config.data));
          console.error('Request Params:', redactSecrets(config.params));
        }
      }
      
      if (error.response) {
        throw new Error(`API request failed: ${error.response.status} - ${JSON.stringify(redactSecrets(error.response.data))}`);
      }
      // LAB PATCH (no secrets in logs): never rethrow the raw axios error.
      throw requestFailure(error, method, url);
    }
  }
}

/**
 * API client for Smart One Cloud
 */
export class SmartOneCloudAPIClient extends APIClientBase {
  constructor(
    authToken: string,
    tokenType: TokenType,
    private readonly s1cUrl: string
  ) {
    super(authToken, tokenType);
  }

  getHost(): string {
    return this.s1cUrl;
  }
}

/**
 * API client for Bearer token authentication
 * Does not use session management - uses Bearer token directly
 */
export class BearerTokenAPIClient extends APIClientBase {
  constructor(
    private readonly bearerToken: string,
    private readonly baseUrl: string
  ) {
    super(""); // No auth token needed for base class
  }

  getHost(): string {
    return this.baseUrl;
  }

  /**
   * Override getHeaders to use Authorization Bearer instead of X-chkp-sid
   */
  protected getHeaders(): Record<string, string> {
    return {
      "Content-Type": "application/json",
      "User-Agent": getMainPackageUserAgent(),
      "Authorization": `Bearer ${this.bearerToken}`
    };
  }

  /**
   * Override needsLogin - Bearer token doesn't need session management
   */
  protected needsLogin(): boolean {
    return false;
  }
}

/**
 * API client for on-premises management server
 * Supports username/password authentication. LAB PATCH (D053): the server
 * certificate is verified; see getVerifyingHttpsAgent() for trusting a
 * self-signed or private-CA certificate.
 */
export class OnPremAPIClient extends APIClientBase {
  private readonly username?: string;
  private readonly password?: string;

  constructor(
    apiKey: string | undefined,
    private readonly managementHost: string,
    private readonly managementPort: string = "443",
    username?: string,
    password?: string
  ) {
    super(apiKey || ""); // APIClientBase requires apiKey, but we'll handle empty case
    this.username = username;
    this.password = password;
  }

  getHost(): string {
    const managementHost =  this.managementHost;
    const port = this.managementPort;
    return `https://${managementHost}:${port}/web_api`;
  }

  /**
   * LAB PATCH (D053): operator TLS trust settings (environment only, never
   * request headers). The server-name override applies only to the
   * configured MANAGEMENT_HOST, never to a host a session supplied itself.
   */
  protected tlsTrust(): TlsTrustSettings {
    const configuredHost = process.env.MANAGEMENT_HOST?.trim().toLowerCase();
    const isConfiguredHost = !!configuredHost && configuredHost === this.managementHost.trim().toLowerCase();
    return {
      caFile: process.env.MANAGEMENT_CA_CERT,
      servername: isConfiguredHost ? process.env.MANAGEMENT_TLS_SERVERNAME : undefined,
    };
  }

  protected getHttpsAgent(): https.Agent {
    return getVerifyingHttpsAgent(this.tlsTrust());
  }

    /**
   * Override login() to support both api-key and username/password authentication
   * (LAB PATCH D053: with certificate verification)
   */
  async login(): Promise<string> {
    // Determine if we're using API key or username/password
    const isUsingApiKey = !!this.authToken;
    const isUsingCredentials = !!(this.username && this.password);
    
    if (!isUsingApiKey && !isUsingCredentials) {
      // Create and throw a ClientResponse directly for credential errors
      throw new ClientResponse(
        401, 
        { message: "Authentication failed: No API key or username/password provided" }
      );
    }

    // LAB PATCH (D053): verify the certificate (upstream turned verification off here)
    const httpsAgent = this.getHttpsAgent();
    
    // Prepare login payload based on authentication method
    const loginPayload = isUsingApiKey 
      ? { "api-key": this.authToken } 
      : { "user": this.username, "password": this.password };
    
    const loginResp = await this.makeRequest(
      this.getHost(),
      "POST",
      "login",
      loginPayload,
      { "Content-Type": "application/json" },
      null,
      httpsAgent
    );

    if (loginResp.status !== 200 || !loginResp.response || !loginResp.response.sid) {
      throw loginResp;
    }

    this.sid = loginResp.response.sid;
    this.sessionTimeout = loginResp.response["session-timeout"] || null;
    this.sessionStart = Date.now();

    // Check if this is an MDS environment by calling get-session with the session UID
    if (loginResp.response.uid) {
      await this.detectMDS(loginResp.response.uid, true); // handleSelfSigned = true for OnPrem
    }

    return loginResp.response.sid;
  }
}
