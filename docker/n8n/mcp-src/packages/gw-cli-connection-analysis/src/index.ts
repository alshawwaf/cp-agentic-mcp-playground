#!/usr/bin/env node

import { z } from 'zod';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { Settings, APIManagerForAPIKey } from '@chkp/quantum-infra';
import { 
  launchMCPServer, 
  createServerModule,
  createApiRunner
} from '@chkp/mcp-utils';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

import * as Scripts from './scripts/index.js';

// Import all script classes
import { runScript } from '@chkp/quantum-gw-cli-base';

const pkg = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../package.json'), 'utf-8')
);

process.env.CP_MCP_MAIN_PKG = `${pkg.name} v${pkg.version}`;


// Build a fresh MCP server instance with all tools registered. A factory is used
// (instead of a shared singleton) so that Streamable HTTP can create one server
// per session. The MCP SDK forbids connecting a single server to more than one
// transport, which otherwise breaks concurrent/multi-client use.
function createGwCliConnectionAnalysisServer(): McpServer {
  const server = new McpServer({
  name: 'gw-cli',
  description: 'MCP server to run Connection Analysis on a Check Point gateway',
  version: '0.0.1'
});

// Connection Analysis Tools
server.tool(
  'start_connection_analysis',
  'Start a debug connection analysis on the target gateway, the user can then reproduce the issue and report back.',
  {
    target_gateway: z.string().describe('The target gateway to run the command on'),
    source_ip: z.string().describe('Source IP address for the connection'),
    destination_ip: z.string().describe('Destination IP address for the connection')
  },
  async ({ target_gateway, source_ip, destination_ip }, extra) => {
    const result = await runScript(server, 
      Scripts.StartConnectionDebugScript,
      target_gateway,
      { source_ip, destination_ip },
      serverModule,
      extra
    );
    
    return {
      content: [{ type: 'text', text: result }]
    };
  }
);

server.tool(
  'stop_connection_analysis',
  'Stop a debug connection analysis on the target gateway and get the results of the debug script.',
  {
    target_gateway: z.string().describe('The target gateway to run the command on'),
    source_ip: z.string().describe('Source IP address for the connection'),
    destination_ip: z.string().describe('Destination IP address for the connection')
  },
  async ({ target_gateway, source_ip, destination_ip }, extra) => {
    const result = await runScript(server, 
      Scripts.StopConnectionDebugScript,
      target_gateway,
      { source_ip, destination_ip },
      serverModule,
      extra
    );
    
    return {
      content: [{ type: 'text', text: result }]
    };
  }
);

  return server;
}

// Singleton server module (used for stdio transport and as a fallback)
const serverModule = createServerModule(
  createGwCliConnectionAnalysisServer(),
  Settings,
  pkg,
  APIManagerForAPIKey
);

// Provide a per-session server factory for multi-session Streamable HTTP
serverModule.createServer = createGwCliConnectionAnalysisServer;

// Create an API runner function (reads serverModule at call time)
const runApiScript = createApiRunner(serverModule);

export const server = serverModule.server;

const main = async () => {
  await launchMCPServer(
    join(dirname(fileURLToPath(import.meta.url)), 'server-config.json'),
    serverModule
  );
};

main().catch((error) => {
  console.error('Fatal error in main():', error);
  process.exit(1);
});
