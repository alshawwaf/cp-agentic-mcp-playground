import { redactSecrets } from './redact.js';

/**
 * SettingsManager for MCP servers with multi-user support.
 * This class manages settings on a per-session basis for all MCP servers.
 */
export class SettingsManager {
  private settingsMap: Map<string, any> = new Map();
  private defaultSessionId: string = 'default';
  private settingsClass: any;
  
  // Global debug state that persists across all sessions and instances.
  // LAB PATCH (D044): set only from the operator's --debug / DEBUG at start-up,
  // never from a request header (a `debug: true` header used to switch on
  // global debug logging of every incoming header, credentials included).
  private static globalDebugState: string | string[] | boolean | undefined = undefined;

  // LAB PATCH (D044): the start-up CLI/env options, reused for every session
  // that does not supply its own credentials in request headers.
  private bootArgs: Record<string, any> = {};

  /**
   * Creates a new SettingsManager
   * @param settingsClass The Settings class to use for creating settings instances
   */
  constructor(settingsClass: any) {
    this.settingsClass = settingsClass;
    // We don't initialize the default settings here because we need the Settings class
    // which will be specific to each infra package
  }

  /**
   * Get settings for a specific session
   * @param sessionId The session ID (defaults to 'default' for stdio mode)
   * @returns Settings instance for the session
   */
  getSettings(sessionId?: string): any {
    const sid = sessionId || this.defaultSessionId;
    
    if (!this.settingsMap.has(sid)) {
      // Create new settings instance for this session if it doesn't exist
      this.settingsMap.set(sid, new this.settingsClass());
    }
    
    return this.settingsMap.get(sid);
  }

  /**
   * Set settings for a specific session
   * @param settings The settings object to set
   * @param sessionId The session ID (defaults to 'default' for stdio mode)
   */
  setSettings(settings: any, sessionId?: string): void {
    const sid = sessionId || this.defaultSessionId;
    this.settingsMap.set(sid, settings);
  }

  /**
   * Injects debug settings from source into settings object
   * @param settings The settings object to update
   * @param source Source object containing debug information
   * @private
   */
  private injectDebug(settings: any, source: Record<string, any>, updateGlobal = false): void {
    const debug = source?.debug ?? SettingsManager.globalDebugState ?? process.env.DEBUG;
    if (typeof debug !== 'undefined') {
      // Update global debug state when debug is explicitly set (LAB PATCH: start-up options only)
      if (updateGlobal && source?.debug !== undefined) {
        SettingsManager.globalDebugState = source.debug;
      }
      
      // Use the set method if available (for proper settings classes), otherwise set directly
      if (typeof settings.set === 'function') {
        settings.set('debug', debug);
      } else {
        settings.debug = debug;
      }

      // Print all headers/args when debug is enabled (LAB PATCH: secrets redacted)
      if (debug) {
        console.error('Debug enabled. Source object contents (secrets redacted):');
        console.error(JSON.stringify(redactSecrets(source), null, 2));
      }
    }
  }

  /**
   * Print settings object for debugging by dynamically discovering its properties
   * @param settings The settings object to print
   * @private
   */
  private printSettingsDebug(settings: any): void {
    if (typeof settings.get === 'function') {
      // For settings objects with get method, try to discover properties dynamically
      console.error('Settings (using get method):');
      
      // If the settings object has a data property or similar, try to iterate over it
      if (settings.data && typeof settings.data === 'object') {
        Object.entries(redactSecrets(settings.data) as Record<string, unknown>).forEach(([key, value]) => {
          console.error(`  ${key}: ${JSON.stringify(value)}`);
        });
      } else {
        console.error('  Unable to enumerate settings properties - no accessible data structure');
      }
    } else {
      // For plain objects, show all enumerable properties
      console.error('Settings (plain object, secrets redacted):');
      Object.entries(redactSecrets(settings) as Record<string, unknown>).forEach(([key, value]) => {
        console.error(`  ${key}: ${JSON.stringify(value)}`);
      });
    }
  }

  /**
   * Create settings from command-line arguments
   * @param args Command line arguments
   * @param sessionId Optional session ID
   * @returns Settings instance
   */
  createFromArgs(args: Record<string, any>, sessionId?: string): any {
    this.bootArgs = { ...args };
    const settings = this.settingsClass.fromArgs(args);
    this.injectDebug(settings, args, true);
    this.setSettings(settings, sessionId);
    return settings;
  }

  /**
   * LAB PATCH (D044, header auth vs env auth): build the settings for one new
   * HTTP session WITHOUT storing them, so the launcher can reject a bad
   * configuration before any session state exists.
   *
   * The two credential sources are mutually exclusive per session:
   * - useHeaders = false: the operator's start-up settings (CLI/env) only.
   *   Request headers are ignored entirely.
   * - useHeaders = true: the request headers only. Each Settings.fromHeaders()
   *   builds its object without falling back to process.env, so a caller that
   *   sends e.g. only `management-host` never gets the operator's API key.
   *
   * Debug mode always follows the operator's --debug / DEBUG setting.
   */
  buildSessionSettings(headers: Record<string, string | string[]>, useHeaders: boolean): any {
    let settings: any;
    if (useHeaders) {
      const normalizedHeaders: Record<string, string | string[]> = {};
      for (const [key, value] of Object.entries(headers)) {
        normalizedHeaders[key.includes('_') ? key.replace(/_/g, '-') : key] = value;
      }
      if (SettingsManager.globalDebugState) {
        console.error('=== Session settings from request headers (secrets redacted) ===');
        console.error(JSON.stringify(redactSecrets(normalizedHeaders), null, 2));
      }
      settings = this.settingsClass.fromHeaders(normalizedHeaders);
    } else {
      settings = this.settingsClass.fromArgs({ ...this.bootArgs });
    }
    this.injectDebug(settings, { debug: SettingsManager.globalDebugState });
    if (SettingsManager.globalDebugState) {
      this.printSettingsDebug(settings);
    }
    return settings;
  }
  
  /**
   * Create settings from HTTP headers
   * @param headers HTTP headers
   * @param sessionId Optional session ID
   * @returns Settings instance
   */
  createFromHeaders(headers: Record<string, string | string[]>, sessionId?: string): any {
    // LAB PATCH (D044): header-supplied settings never mix with env credentials,
    // and a `debug` header no longer changes the global debug state.
    const settings = this.buildSessionSettings(headers, true);
    this.setSettings(settings, sessionId);
    return settings;
  }

  /**
   * Clear settings for a session
   * @param sessionId The session ID to clear
   */
  clearSession(sessionId: string): void {
    if (sessionId !== this.defaultSessionId) {
      this.settingsMap.delete(sessionId);
    }
  }
}
