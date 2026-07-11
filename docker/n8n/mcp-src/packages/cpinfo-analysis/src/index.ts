#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import {
  launchMCPServer,
  createServerModule,
  createApiRunner
} from "@chkp/mcp-utils";
import { readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { CpInfoAPIManager } from "./api-manager.js";
import { Settings } from "./settings.js";
import { CpInfoService } from "./cpinfo-service.js";
import { registerCpinfoTools } from "./tool-handlers.js";
import { logger } from "./logger.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const pkg = JSON.parse(
  readFileSync(join(__dirname, "../package.json"), "utf-8")
);

// Log startup info
logger.info("CPInfo MCP Server starting up");
logger.info(`Version: ${pkg.version}`);
logger.info(`Log file: ${logger.getLogFilePath()}`);
logger.info(`Working directory: ${process.cwd()}`);

process.env.CP_MCP_MAIN_PKG = `${pkg.name} v${pkg.version}`;

// Build a fresh MCP server instance with all tools registered. A factory is used
// (instead of a shared singleton) so that Streamable HTTP can create one server per
// session. The MCP SDK forbids connecting a single server to more than one transport,
// which otherwise breaks concurrent/multi-client use.
function createCpinfoAnalysisServer(): McpServer {
  const server = new McpServer({
    name: "cpinfo-analysis",
    description: "Semantic CPInfo analysis server",
    version: pkg.version || "1.0.0"
  });

  const service = new CpInfoService();
  const registeredTools = registerCpinfoTools(server, service);

  logger.info(`Registering ${registeredTools} tools`);

  return server;
}

// Singleton server module (used for stdio transport and as a fallback)
const serverModule = createServerModule(
  createCpinfoAnalysisServer(),
  Settings,
  pkg,
  CpInfoAPIManager
);

// Provide a per-session server factory for multi-session Streamable HTTP
serverModule.createServer = createCpinfoAnalysisServer;

// Create an API runner function (reads serverModule at call time)
const runApi = createApiRunner(serverModule);

export const server = serverModule.server;

const main = async () => {
  logger.info("Launching MCP server...");
  await launchMCPServer(
    join(__dirname, "server-config.json"),
    serverModule
  );
  logger.info("MCP server launched successfully");
};

main().catch((error) => {
  logger.error("Fatal error during startup", error);
  console.error("Fatal error:", error);
  process.exit(1);
});
