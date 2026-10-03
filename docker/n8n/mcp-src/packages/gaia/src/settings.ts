// settings.ts - Minimal settings since we use dialog authentication
import { getHeaderValue } from '@chkp/mcp-utils';

export class Settings {
  // Keep minimal settings - dialog authentication handles the rest
  public verbose: boolean = false;

  constructor({
    verbose = process.env.VERBOSE === 'true'
  }: {
    verbose?: boolean;
  } = {}) {
    this.verbose = verbose || false;
  }

  validate(): boolean {
    // No validation needed since we prompt for everything via dialogs
    return true;
  }

  static fromArgs(options: any): Settings {
    // LAB PATCH (log noise): no longer prints every CLI option; fromArgs now
    // runs for every HTTP session (see mcp-utils SettingsManager).
    return new Settings({
      verbose: options.verbose
    });
  }

  static fromHeaders(headers: Record<string, string | string[]>): Settings {
    const verbose = getHeaderValue(headers, 'VERBOSE') === 'true';
    
    return new Settings({
      verbose
    });
  }
}