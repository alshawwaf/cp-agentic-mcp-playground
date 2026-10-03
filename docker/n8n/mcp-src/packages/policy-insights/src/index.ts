#!/usr/bin/env node

// LAB PATCH (vendored Policy Insights, PATCHES.md section 11): adapted from upstream
// packages/policy-insights/src/index.ts at CheckPointSW/mcp-servers@ef34749 (the source
// of npm @chkp/policy-insights-mcp@0.3.5) to the lab's vendored @chkp/mcp-utils:
// - one MCP server instance per HTTP session (createServer factory, PATCHES.md 1);
// - no createMcpServer() telemetry wrapper (the vendored mcp-utils has none);
// - no dumps of the tool context, arguments, responses or raw errors: the context
//   carries the request headers (a session's API key in header mode) and the
//   responses are customer policy data. Debug mode logs the tool, path and
//   redacted arguments only.
// Tool names, descriptions and input schemas are unchanged.

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { McpToolDefinition, toolDefinitionMap } from './toolDefinitionMap.js';
import { Settings, APIManagerForAPIKey } from '@chkp/quantum-infra';
import {
  launchMCPServer,
  createServerModule,
  SessionContext,
  createApiRunner,
  redactSecrets,
} from '@chkp/mcp-utils';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { zodSchemas } from './zodSchemas.js';
import { getServerInfo } from './serverInfo.js';

const pkg = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../package.json'), 'utf-8')
);

process.env.CP_MCP_MAIN_PKG = `${pkg.name} v${pkg.version}`;

/**
 * Server configuration
 */
const SERVER_PACKAGE_NAME: string = pkg['name'];
const INIT_TOOL_NAME = `${(SERVER_PACKAGE_NAME.split('/').pop() as string).replace(/-mcp$/, '')}__init`;

// Management API version: v2.1 (R82.10+)

// Build a fresh MCP server instance with all tools registered (one per HTTP session).
function createPolicyInsightsServer(): McpServer {
  const server = new McpServer({
    ...getServerInfo(),
    name: SERVER_PACKAGE_NAME,
    version: pkg['version'],
  });

  // Add the init tool
  server.tool(
    INIT_TOOL_NAME,
    'Verify, login and initialize management connection. Use this tool on your first interaction with the server.',
    {},
    async (args: Record<string, unknown>, extra: any) => {
      try {
        // Get API manager for this session
        const apiManager = SessionContext.getAPIManager(serverModule, extra);

        // Check if environment is MDS
        const isMds = await apiManager.isMds();

        if (!isMds) {
          return {
            content: [
              {
                type: 'text',
                text: `${SERVER_PACKAGE_NAME} server is up and running. The environment is NOT part of Multi Domain system, there is no need to use domain parameters in tool calls.`,
              },
            ],
          };
        } else {
          // Get domains for MDS environment
          const domains = await apiManager.getDomains();

          // Format domain information
          const domainList = domains
            .map((domain: { name: string; type: string }) => `${domain.name} (${domain.type})`)
            .join(', ');

          return {
            content: [
              {
                type: 'text',
                text: `${SERVER_PACKAGE_NAME} server is up and running. The environment is part of Multi Domain system. You need to use the domain parameter for calling APIs, if you are not sure which to use, ask the user. The domains in the system are: ${domainList}`,
              },
            ],
          };
        }
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : String(error);
        return {
          content: [
            {
              type: 'text',
              text: `Error initializing ${SERVER_PACKAGE_NAME} connection: ${errorMessage}`,
            },
          ],
        };
      }
    }
  );

  // Register all tools from the tool definition map dynamically
  for (const toolDef of toolDefinitionMap) {
    const toolName = toolDef.name;
    if (!toolName) {
      console.error('Tool definition missing name; skipped.');
      continue;
    }
    server.tool(
      toolName,
      toolDef.description as string,
      zodSchemas[toolName as keyof typeof zodSchemas],
      async (args: Record<string, unknown>, extra: any) => {
        // args are already validated by the MCP framework using the Zod schema
        return await executeApiTool(toolName, toolDef, args, extra);
      }
    );
  }

  return server;
}

function isDebug(extra: any): boolean {
  try {
    const debug = SessionContext.getSettings(serverModule, extra)?.debug;
    return debug === true || debug === 'true';
  } catch {
    return false;
  }
}

// LAB PATCH (input validation): `domain` comes from the model and selects the domain
// the session logs in to on the configured management server. Accept a plain name only.
function validDomain(domain: unknown): domain is string {
  return typeof domain === 'string' && domain.length <= 255 && !/[\u0000-\u001f\u007f]/.test(domain);
}

/**
 * Executes an API tool with the provided arguments
 *
 * @param toolName Name of the tool to execute
 * @param definition Tool definition
 * @param toolArgs Arguments provided by the user
 * @param extra The session context from MCP server
 * @returns Call tool result
 */
async function executeApiTool(
  toolName: string,
  definition: McpToolDefinition,
  validatedArgs: Record<string, any>,
  extra?: any
): Promise<{ content: Array<{ type: 'text'; text: string }> }> {
  try {
    // Prepare URL, query parameters, headers, and request body
    const urlPath = definition.pathTemplate as string;

    if (isDebug(extra)) {
      console.error(`=== Executing tool "${toolName}" (${definition.method} ${urlPath}) ===`);
      console.error('Arguments (secrets redacted):', JSON.stringify(redactSecrets(validatedArgs), null, 2));
    }

    let domain: string | undefined = undefined;
    if (validatedArgs.domain !== undefined && validatedArgs.domain !== '') {
      if (!validDomain(validatedArgs.domain)) {
        return {
          content: [{ type: 'text', text: `Error executing tool '${toolName}': invalid domain name` }],
        };
      }
      domain = validatedArgs.domain.trim() !== '' ? validatedArgs.domain : undefined;
    }
    // Execute the request
    // Ensure requestBody is never undefined to avoid sanitizeData issues
    const requestBody = validatedArgs?.['requestBody'] || {};

    if (validatedArgs.domains_to_process) {
      requestBody['domains-to-process'] = [validatedArgs.domains_to_process];
      requestBody['ignore-warnings'] = true;
      // LAB PATCH (compatibility): domains-to-process must run from the System Domain
      // (root MDS session). Upstream's newer @chkp/quantum-infra skips domain routing
      // in that case; the vendored one does not, so the domain is dropped here.
      domain = undefined;
    }

    const resp = await runApi('POST', urlPath, requestBody, extra, domain);

    return { content: [{ type: 'text', text: JSON.stringify(resp, null, 2) }] };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : String(error);
    console.error(`Tool "${toolName}" failed: ${errorMessage}`);
    return {
      content: [{ type: 'text', text: `Error executing tool '${toolName}': ${errorMessage}` }],
    };
  }
}

// Singleton server module (used for stdio transport and as a fallback)
const serverModule = createServerModule(
  createPolicyInsightsServer(),
  Settings,
  pkg,
  APIManagerForAPIKey
);

// Provide a per-session server factory for multi-session Streamable HTTP
serverModule.createServer = createPolicyInsightsServer;

// Create an API runner function
const runApi = createApiRunner(serverModule);

export const server = serverModule.server;

/**
 * Main function to start the server
 */
async function main() {
  await launchMCPServer(
    join(dirname(fileURLToPath(import.meta.url)), 'server-config.json'),
    serverModule
  );
}

main().catch((error) => {
  console.error('Fatal error in main():', error instanceof Error ? error.message : String(error));
  process.exit(1);
});
