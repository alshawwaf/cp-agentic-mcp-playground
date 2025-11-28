// Add global declaration for the cpMcpSettings property
declare global {
  // Add cpMcpSettings to globalThis for type safety
  // eslint-disable-next-line no-var
  var cpMcpSettings: import('./settings').Settings | undefined;
}

// Extend the type of globalThis to include cpMcpSettings
interface GlobalThis {
  cpMcpSettings?: import('./settings').Settings;
}

export {};
