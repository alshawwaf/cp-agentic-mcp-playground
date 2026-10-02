import { getHeaderValue } from '@chkp/mcp-utils';
import { Settings as BaseSettings } from '@chkp/quantum-infra';

export class Settings extends BaseSettings {
  public infinityPortalUrl: string = '';

  constructor({
    clientId = process.env.CLIENT_ID,
    secretKey = process.env.SECRET_KEY,
    infinityPortalUrl = process.env.INFINITY_PORTAL_URL,
    region = process.env.REGION || 'EU',
    ...baseArgs
  }: {
    clientId?: string;
    secretKey?: string;
    infinityPortalUrl?: string;
    region?: string;
    [key: string]: any;
  } = {}) {
    // Don't set s1cUrl to avoid base class validation requiring API key
    super({
      clientId,
      secretKey,
      region: region as any,
      ...baseArgs
    });
    
    this.infinityPortalUrl = infinityPortalUrl || '';
    
    // LAB PATCH (D031): no validation here. Validating in the constructor made
    // the server exit at start-up (and crash-loop) whenever SPARK_MGMT_* was
    // blank, unlike every other server. SMPAPIManager.create() now validates,
    // so a missing setting is reported by the tool call instead.
  }

  /**
   * Spark Management-specific validation
   */
  validateSMPSettings(): void {
    if (!this.clientId) {
      throw new Error('Client ID is required (via --client-id or CLIENT_ID env var)');
    }
    if (!this.secretKey) {
      throw new Error('Secret key is required (via --secret-key or SECRET_KEY env var)');
    }
    if (!this.infinityPortalUrl) {
      throw new Error('Infinity Portal URL is required (via --infinity-portal-url or INFINITY_PORTAL_URL env var)');
    }
  }

  static override fromArgs(options: any): Settings {
    return new Settings({
      clientId: options.clientId,
      secretKey: options.secretKey,
      infinityPortalUrl: options.infinityPortalUrl,
      region: options.region
    });
  }

  static override fromHeaders(headers: Record<string, string | string[]>): Settings {
    // LAB PATCH (D044): headers ONLY - '' instead of undefined so neither this
    // constructor nor the base Settings fills a missing value from process.env.
    const header = (key: string): string => getHeaderValue(headers, key) ?? '';
    
    return new Settings({
      clientId: header('CLIENT-ID'),
      secretKey: header('SECRET-KEY'),
      infinityPortalUrl: header('INFINITY-PORTAL-URL'),
      region: header('REGION').toUpperCase() || 'EU',
      apiKey: '',
      username: '',
      password: '',
      s1cUrl: '',
      managementHost: '',
      cloudInfraToken: ''
    });
  }
};
