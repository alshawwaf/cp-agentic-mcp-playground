// API client implementation for Check Point MCP servers
import axios from "axios";
import https from "https";

/**
 * Response from an API client call
 */
export class ClientResponse {
  constructor(public status: number, public response: Record<string, any>) {}
}

/**
 * Base class for API clients
 */
export abstract class APIClientBase {
  protected sid: string | null = null;

  constructor(protected readonly apiKey: string) {}

  /**
   * Get the host URL for the API client
   */
  abstract getHost(): string;
  /**
   * Create an API client instance - generic method for creating clients
   */
  static create<T extends APIClientBase>(
    this: new (...args: any[]) => T,
    ...args: any[]
  ): T {
    return new this(...args);
  }

  /**
   * Get headers for the API requests
   */
  protected getHeaders(): Record<string, string> {
    return {
      "Content-Type": "application/json",
      "X-chkp-sid": this.sid || "",
    };
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
    if (!this.sid) {
      this.sid = await this.loginWithApiKey();
    }

    return await APIClientBase.makeRequest(
      this.getHost(),
      method,
      uri,
      data,
      this.getHeaders(),
      params
    );
  }

  /**
   * Login to the API using the API key
   */
  async loginWithApiKey(): Promise<string> {
    console.error("Logging in with API key");

    const loginResp = await APIClientBase.makeRequest(
      this.getHost(),
      "POST",
      "login",
      { "api-key": this.apiKey },
      { "Content-Type": "application/json" }
    );

    return loginResp.response.sid;
  }

  /**
   * Make a request to a Check Point API
   */ static async makeRequest(
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
      params: params || undefined,
    };

    // Add httpsAgent if provided (for handling self-signed certificates)
    if (httpsAgent) {
      config.httpsAgent = httpsAgent;
    }

    // Only set data for non-GET requests
    if (method.toUpperCase() !== "GET" && data !== undefined) {
      config.data = data;
    }

    // Always log the URL for our debug API
    console.error(`🌐 API Request: ${method} ${url}`);

    try {
      const response = await axios(config);

      return new ClientResponse(
        response.status,
        response.data as Record<string, any>
      );
    } catch (error: any) {
      if (error.response) {
        throw new Error(
          `API request failed: ${error.response.status} - ${JSON.stringify(
            error.response.data
          )}`
        );
      }
      throw error;
    }
  }
}

/**
 * API client for Harmony SASE
 */
export class HarmonySaseAPIClient extends APIClientBase {
  constructor(
    apiKey: string,
    private readonly managementHost: string,
    private readonly origin: string
  ) {
    super(apiKey);
  }

  getHost(): string {
    return this.managementHost;
  }

  /**
   * Override the login method for Harmony SASE
   */
  override async loginWithApiKey(): Promise<string> {
    console.error("Logging in to Harmony SASE with API key");
    const loginResp = await APIClientBase.makeRequest(
      this.getHost(),
      "POST",
      "v1/auth/authorize",
      {
        apiKey: this.apiKey,
        grantType: "api_key",
      },
      {
        "Content-Type": "application/json",
        accept: "application/json",
      }
    );
    return loginResp.response.data.accessToken;
  }

  /**
   * Override the headers method for Harmony SASE
   */
  protected override getHeaders(): Record<string, string> {
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${this.sid}`,
      Origin: this.origin,
    };
  }
}
