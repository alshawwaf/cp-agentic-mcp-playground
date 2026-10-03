#!/usr/bin/env node
import { Command } from 'commander';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import fs from 'fs';
import http from 'http';
import { randomUUID } from 'crypto';

export interface CliOption {
  flag: string;
  description: string;
  env?: string;
  default?: string;
  type?: 'string' | 'boolean';
}

export interface ServerConfig {
  name: string;
  description?: string;
  options: CliOption[];
}

export interface ServerModule {
  server: any; // The MCP server instance (used for stdio and as a fallback)
  // Optional factory that builds a fresh MCP server instance with all tools
  // registered. When provided, Streamable HTTP creates one server per session,
  // which is required for concurrent/multi-client use: the MCP SDK forbids
  // connecting a single server to more than one transport.
  createServer?: () => any;
  Settings: {
    fromArgs(options: any): any;
    fromHeaders(headers: Record<string, string | string[]>): any;
  };
  settingsManager: any; // SettingsManager instance for multi-user support
  apiManagerFactory: any; // APIManagerFactory instance for multi-user support
  sessionManager: any; // SessionManager instance for session lifecycle management
  pkg: { version: string };
}

export type TransportType = 'stdio' | 'http';

/**
 * Launch an MCP server with configuration-driven CLI options
 * @param configPath Path to the server configuration JSON file
 * @param serverModule The server module containing server, Settings, and pkg
 */
export async function launchMCPServer(
  configPath: string,
  serverModule: ServerModule
): Promise<void> {
  // Load configuration
  const config: ServerConfig = JSON.parse(fs.readFileSync(configPath, 'utf8'));

  // Create commander program for CLI options
  const program = new Command();

  if (config.description) {
    program.description(config.description);
  }

  // Dynamically add options from config
  config.options.forEach(option => {
    const envValue = option.env ? process.env[option.env] : undefined;
    // LAB PATCH (D044 follow-up): the environment wins over a config default (as
    // upstream now does). With "default": "443" first, MANAGEMENT_PORT was ignored
    // at start-up; env-mode sessions now reuse these start-up options.
    const defaultValue = envValue || option.default;

    if (option.type === 'boolean') {
      const boolDefault = envValue === 'true' || option.default === 'true';
      program.option(option.flag, option.description, boolDefault);
    } else {
      program.option(option.flag, option.description, defaultValue);
    }
  });

  // Always add transport options regardless of server-config
  const transportTypeDefault = process.env.MCP_TRANSPORT_TYPE || 'stdio';
  program.option('--transport <type>', 'Transport type (stdio or http)', transportTypeDefault);

  // Always add transport-port option regardless of server-config
  const transportPortDefault = process.env.MCP_TRANSPORT_PORT || '3000';
  program.option('--transport-port <number>', 'Port for network transports (e.g., HTTP)', transportPortDefault);

  const debugDefault = process.env.DEBUG === 'true' || false;
  program.option('--debug', 'Enable debug mode', debugDefault);

  // Parse arguments
  program.parse(process.argv);
  const options = program.opts();

  // Initialize settings from CLI args
  if (!serverModule.settingsManager) {
    throw new Error('ServerModule must have a settingsManager. Create it with createServerModule.');
  }

  const settings = serverModule.settingsManager.createFromArgs(options);

  // Determine transport type from options or environment variable
  const transportType = (options.transport || process.env.MCP_TRANSPORT_TYPE || 'stdio').toLowerCase() === 'http' ? 'http' : 'stdio';

  // Always try to read transport-port from CLI args or environment variable
  const transportPort = options.transportPort
    ? parseInt(options.transportPort, 10)
    : process.env.MCP_TRANSPORT_PORT
      ? parseInt(process.env.MCP_TRANSPORT_PORT, 10)
      : 3000;

  if (transportType === 'http') {
    // Launch Streamable server
    await launchHTTPServer(config, serverModule, transportPort);
  } else {
    // Start stdio server
    const transport = new StdioServerTransport();
    const defaultSessionId = 'default';

    // Initialize the default session
    const sessionMetadata = {
      type: 'stdio',
      startedAt: new Date()
    };

    serverModule.sessionManager.createSession(defaultSessionId, sessionMetadata);

    // Add default session context for stdio transport
    (transport as any).extraContext = () => {
      return {
        sessionId: defaultSessionId,
        transport
      };
    };

    await serverModule.server.connect(transport);

    console.error(`${config.name} running on stdio transport. Version: ${serverModule.pkg.version}`);
    console.error(`Transport type: stdio`);
  }
}

// ---------------------------------------------------------------------------
// LAB PATCH (session lifecycle, D127/D128): Streamable HTTP sessions are closed
// after MCP_SESSION_IDLE_TIMEOUT_SECONDS without a request (default 1800) and
// at most MCP_MAX_SESSIONS stay open (default 32; when full, the least recently
// used idle session is closed). 0 disables either limit. Sessions with a request
// or an SSE stream in progress are never closed by these limits. Many clients
// (n8n's MCP Client Tool, Flowise, the gateway) never send DELETE, so without
// this every abandoned session kept a full server instance (about 2 MB) until
// the sidecar ran out of memory.
// ---------------------------------------------------------------------------
const DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS = 1800;
const DEFAULT_MAX_SESSIONS = 32;

function readNonNegativeInt(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === '') {
    return fallback;
  }
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0) {
    console.error(`Ignoring ${name}=${JSON.stringify(raw)}: expected a whole number >= 0. Using ${fallback}.`);
    return fallback;
  }
  return value;
}

// LAB PATCH (D044): server options that are not credentials or connection
// targets. ORIGIN (Harmony SASE) collides with the standard HTTP Origin header.
const NON_CREDENTIAL_OPTIONS = new Set(['ORIGIN']);

/**
 * LAB PATCH (D044): the server's credential/target options that this request
 * supplies as (non-empty) headers. Any match means the session uses
 * header-supplied settings only; no match means the operator's env settings
 * only. The two are never mixed.
 */
function headerSuppliedOptions(headers: http.IncomingHttpHeaders, config: ServerConfig): string[] {
  const optionNames = new Set<string>();
  for (const option of config.options || []) {
    if (!option.env || option.type === 'boolean' || NON_CREDENTIAL_OPTIONS.has(option.env)) {
      continue;
    }
    optionNames.add(option.env.toUpperCase().replace(/-/g, '_'));
    optionNames.add(option.flag.split(' ')[0].replace(/^--?/, '').toUpperCase().replace(/-/g, '_'));
  }
  const supplied: string[] = [];
  for (const [name, value] of Object.entries(headers)) {
    const text = Array.isArray(value) ? value.join('') : value;
    if (text === undefined || String(text).trim() === '') {
      continue;
    }
    const normalized = name.toUpperCase().replace(/-/g, '_');
    if (optionNames.has(normalized)) {
      supplied.push(normalized);
    }
  }
  return supplied;
}

function sendJsonRpcError(res: http.ServerResponse, status: number, code: number, message: string, id: unknown = null): void {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ jsonrpc: '2.0', error: { code, message }, id: id ?? null }));
}

/**
 * Launch an MCP server with Streamable HTTP transport
 * @param config Server configuration
 * @param serverModule The server module containing server, Settings, and pkg
 * @param port Port to listen on
 */
async function launchHTTPServer(
  config: ServerConfig,
  serverModule: ServerModule,
  port: number
): Promise<void> {
  // Map to store transports by session ID
  const transports: Record<string, StreamableHTTPServerTransport> = {};

  // LAB PATCH (D127): last activity and requests in progress, per session.
  const activity = new Map<string, { lastActive: number; inFlight: number }>();
  const idleTimeoutMs = readNonNegativeInt('MCP_SESSION_IDLE_TIMEOUT_SECONDS', DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS) * 1000;
  const maxSessions = readNonNegativeInt('MCP_MAX_SESSIONS', DEFAULT_MAX_SESSIONS);

  // Release everything a session holds. Runs once per transport: closing the
  // per-session server closes the transport again, which re-enters onclose.
  const cleanupSession = (sid: string, transport: StreamableHTTPServerTransport): void => {
    if ((transport as any)._labCleanedUp) {
      return;
    }
    (transport as any)._labCleanedUp = true;
    if (transports[sid] === transport) {
      delete transports[sid];
    }
    activity.delete(sid);

    // Close the per-session server instance (if one was created)
    try { (transport as any)._sessionServer?.close?.(); } catch { /* best effort */ }

    // Clean up session-specific resources
    serverModule.settingsManager.clearSession(sid);
    serverModule.apiManagerFactory.clearSession(sid);

    // Remove the session from session manager (this will also clean up SessionContext data)
    serverModule.sessionManager.removeSession(sid);
  };

  const closeSession = async (sid: string): Promise<void> => {
    const transport = transports[sid];
    if (!transport) {
      return;
    }
    try {
      await transport.close();
    } catch (error) {
      console.error(`Error closing MCP session: ${(error as Error).message}`);
    }
    cleanupSession(sid, transport);
  };

  // Count a request (or SSE stream) against its session until the response ends.
  const trackRequest = (sid: string, res: http.ServerResponse): void => {
    const entry = activity.get(sid);
    if (!entry) {
      return;
    }
    entry.inFlight += 1;
    entry.lastActive = Date.now();
    serverModule.sessionManager.touchSession(sid);
    res.once('close', () => {
      entry.inFlight = Math.max(0, entry.inFlight - 1);
      entry.lastActive = Date.now();
    });
  };

  const leastRecentlyUsedIdleSession = (): string | undefined => {
    let candidate: string | undefined;
    let oldest = Infinity;
    for (const [sid, entry] of activity) {
      if (entry.inFlight === 0 && entry.lastActive < oldest) {
        oldest = entry.lastActive;
        candidate = sid;
      }
    }
    return candidate;
  };

  if (idleTimeoutMs > 0) {
    const sweepIntervalMs = Math.min(60_000, Math.max(1_000, Math.floor(idleTimeoutMs / 2)));
    const sweeper = setInterval(() => {
      const now = Date.now();
      const idle = [...activity]
        .filter(([, entry]) => entry.inFlight === 0 && now - entry.lastActive >= idleTimeoutMs)
        .map(([sid]) => sid);
      if (idle.length === 0) {
        return;
      }
      Promise.all(idle.map(sid => closeSession(sid))).then(() => {
        console.error(`Closed ${idle.length} idle MCP session(s) (no request for ${idleTimeoutMs / 1000}s); ${Object.keys(transports).length} open.`);
      });
    }, sweepIntervalMs);
    sweeper.unref();
  }

  // Create HTTP server
  const server = http.createServer(async (req, res) => {
    // Handle requests to the root URL
    // Handle requests to the root URL or /mcp or /sse
    if (req.url === '/' || req.url === '/mcp' || req.url === '/sse') {
      // Get the session ID from headers
      const sessionId = req.headers['mcp-session-id'] as string | undefined;

      // Handle different request methods
      if (req.method === 'POST') {
        // For POST requests, need to parse the body
        const chunks: Buffer[] = [];

        try {
          // Read the request body
          for await (const chunk of req) {
            chunks.push(Buffer.from(chunk));
          }
          const bodyBuffer = Buffer.concat(chunks);
          let body;

          // Try to parse JSON body
          try {
            const bodyText = bodyBuffer.toString('utf8');
            if (bodyText) {
              body = JSON.parse(bodyText);
            }
          } catch (err) {
            console.error('Error parsing request body:', (err as Error).message);
          }

          let transport: StreamableHTTPServerTransport;

          if (sessionId && transports[sessionId]) {
            // Reuse existing transport for the session.
            // LAB PATCH (D128): the session's settings were fixed at initialize;
            // a later request (possibly from another caller that learned the
            // session ID) can no longer replace them with its own headers.
            transport = transports[sessionId];
            trackRequest(sessionId, res);
          } else if (sessionId) {
            // LAB PATCH (D127): unknown or expired session. 404 tells MCP clients
            // to start a new session (Streamable HTTP spec).
            sendJsonRpcError(res, 404, -32001, 'Session not found', body?.id);
            return;
          } else if (body && body.method === 'initialize') {
            // LAB PATCH (D044): decide this session's credential source once,
            // before any session state exists. A bad configuration is rejected
            // here with a clear error and leaves nothing behind (previously the
            // SDK reported it as a JSON-RPC "Parse error" and a zombie session stayed).
            const useHeaders = headerSuppliedOptions(req.headers, config).length > 0;
            let sessionSettings: any;
            try {
              sessionSettings = serverModule.settingsManager.buildSessionSettings(
                headersToEnvVars(req.headers, config),
                useHeaders
              );
            } catch (error) {
              sendJsonRpcError(res, 400, -32000, `Invalid session configuration: ${(error as Error).message}`, body.id);
              return;
            }

            // LAB PATCH (D127): enforce MCP_MAX_SESSIONS.
            if (maxSessions > 0 && Object.keys(transports).length >= maxSessions) {
              const victim = leastRecentlyUsedIdleSession();
              if (!victim) {
                sendJsonRpcError(res, 503, -32000, 'Server busy: too many open MCP sessions. Retry later.', body.id);
                return;
              }
              await closeSession(victim);
              console.error(`MCP session limit (${maxSessions}) reached: closed the least recently used idle session.`);
            }

            // New initialization request
            transport = new StreamableHTTPServerTransport({
              sessionIdGenerator: () => randomUUID(),
              onsessioninitialized: (sid: string) => {
                // Store the transport by session ID
                transports[sid] = transport;
                activity.set(sid, { lastActive: Date.now(), inFlight: 0 });

                // Create session in the session manager
                const metadata = {
                  userAgent: req.headers['user-agent'],
                  origin: req.headers.origin || req.headers.referer,
                  remoteAddress: req.socket.remoteAddress,
                  initialPath: req.url,
                  credentialSource: useHeaders ? 'headers' : 'environment'
                };

                serverModule.sessionManager.createSession(sid, metadata);

                // Set up the session-specific settings (built and validated above)
                serverModule.settingsManager.setSettings(sessionSettings, sid);
              }
            });

            // Clean up transport when closed
            (transport as any).onclose = () => {
              const sid = (transport as any).sessionId;
              if (sid) {
                cleanupSession(sid, transport);
              }
            };

            // Connect to the MCP server - only needed for new transports
            // Configure the transport to include session information in the extra context
            // Cast to any as the type definition may not include this property
            (transport as any).extraContext = (msg: any) => {
              const sessionId = (transport as any).sessionId;
              return {
                sessionId,
                transport
              };
            };

            // Use a fresh server instance per session when a factory is provided.
            // This is required for multi-session Streamable HTTP: the MCP SDK
            // forbids connecting one server to multiple transports. Servers that
            // do not provide a factory fall back to the shared singleton.
            const sessionServer = typeof serverModule.createServer === 'function'
              ? serverModule.createServer()
              : serverModule.server;
            (transport as any)._sessionServer = sessionServer;

            await sessionServer.connect(transport)
              .catch((error: Error) => {
                console.error('Error connecting to HTTP transport:', error.message);
              });
          } else {
            // Invalid request
            sendJsonRpcError(res, 400, -32000, 'Bad Request: No valid session ID provided', body?.id);
            return;
          }

          // Handle the request with parsed body
          await transport.handleRequest(req, res, body);
        } catch (error) {
          console.error('Error handling POST request:', (error as Error).message);
          if (!res.headersSent) {
            sendJsonRpcError(res, 500, -32000, 'Internal server error: ' + (error as Error).message);
          }
        }
      } else if (req.method === 'GET') {
        // GET requests for HTTP streaming (server-to-client SSE) of an existing session.
        // LAB PATCH (D127): a GET without a known session used to build a whole
        // server instance that could never be initialized; reject it instead.
        if (!sessionId || !transports[sessionId]) {
          if (sessionId) {
            sendJsonRpcError(res, 404, -32001, 'Session not found');
          } else {
            sendJsonRpcError(res, 400, -32000, 'Bad Request: No valid session ID provided');
          }
          return;
        }
        const transport = transports[sessionId];
        trackRequest(sessionId, res);
        await transport.handleRequest(req, res);
      } else if (req.method === 'DELETE') {
        // DELETE requests for session termination
        if (!sessionId || !transports[sessionId]) {
          res.writeHead(sessionId ? 404 : 400, { 'Content-Type': 'text/plain' });
          res.end('Invalid or missing session ID');
          return;
        }

        const transport = transports[sessionId];
        await transport.handleRequest(req, res);
      } else {
        // Unsupported methods
        res.writeHead(405, { 'Content-Type': 'text/plain', 'Allow': 'GET, POST, DELETE' });
        res.end('Method not allowed');
      }
    } else if (req.url === '/health' || req.url === '/status') {
      // Handle health checks
      res.writeHead(200, { 'Content-Type': 'application/json' });

      // LAB PATCH (D128): report only counts. Listing session IDs let anyone on
      // the network close or reuse other clients' sessions.
      res.end(JSON.stringify({
        status: 'ok',
        server: config.name,
        version: serverModule.pkg.version,
        activeSessions: serverModule.sessionManager.getSessionCount(),
        sessionIdleTimeoutSeconds: idleTimeoutMs / 1000,
        maxSessions
      }));
    } else {
      // 404 for unknown routes
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Not found');
    }
  });

  // Start HTTP server
  server.listen(port, () => {
    console.error(`${config.name} running on HTTP transport at http://localhost:${port}. Version: ${serverModule.pkg.version}`);
    console.error(`Transport type: HTTP, Transport-port: ${port}`);
  });

  // Handle server errors
  server.on('error', (err) => {
    console.error(`Server error: ${err.message}`);
  });
}

/**
 * Convert HTTP headers to environment variable format
 * Map headers to the environment variables defined in the server config
 * @param headers HTTP headers object
 * @param config Optional server config to use for mapping
 * @returns Headers converted to environment variable format
 */
function headersToEnvVars(
  headers: http.IncomingHttpHeaders,
  config?: ServerConfig
): Record<string, string | string[]> {
  const result: Record<string, string | string[]> = {};

  // First, convert all headers to uppercase with underscores
  for (const [name, value] of Object.entries(headers)) {
    if (value !== undefined) {
      // Convert header names to environment variable format (UPPER_CASE)
      const envName = name.toUpperCase().replace(/-/g, '_');
      result[envName] = value;
    }
  }

  // If we have a config, try to map header values to the environment variables defined in the config
  if (config && config.options) {
    // Create a map of lowercase header name -> env var name
    const headerToEnvMap: Record<string, string> = {};

    // Build a mapping from header keys to environment variable names based on config
    for (const option of config.options) {
      if (option.env) {
        // Create mappings for different formats of the same option
        const flagName = option.flag
          .split(' ')[0]                   // Extract just the flag part (e.g., --api-key from --api-key <key>)
          .replace(/^--?/, '')             // Remove leading -- or -
          .replace(/-/g, '_');             // Convert dashes to underscores

        headerToEnvMap[flagName.toLowerCase()] = option.env;

        // Also map the env name to itself (in case header is already in env var format)
        headerToEnvMap[option.env.toLowerCase()] = option.env;
      }
    }

    // Look for headers that match our config options
    for (const [headerName, headerValue] of Object.entries(headers)) {
      if (headerValue !== undefined) {
        const normalizedHeaderName = headerName.toLowerCase().replace(/-/g, '_');
        const envVarName = headerToEnvMap[normalizedHeaderName];

        if (envVarName) {
          // We found a matching environment variable in the config
          result[envVarName] = headerValue;
        }
      }
    }
  }

  return result;
}
