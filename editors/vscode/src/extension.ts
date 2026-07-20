import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
} from "vscode-languageclient/node";

import {displayCommand, PythonCandidate, resolvePython} from "./python";

let client: LanguageClient | undefined;
let output: vscode.LogOutputChannel | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  output = vscode.window.createOutputChannel("Contract4Agents", {log: true});
  context.subscriptions.push(output);
  context.subscriptions.push(
    vscode.commands.registerCommand("contract4agents.restartLanguageServer", async () => {
      await stopClient();
      await startClient();
    }),
    vscode.commands.registerCommand("contract4agents.showLanguageServerOutput", () => output?.show()),
    vscode.workspace.onDidChangeConfiguration(async (event) => {
      if (event.affectsConfiguration("contract4agents.pythonPath")) {
        await stopClient();
        await startClient();
      }
    }),
  );
  await startClient();
}

export async function deactivate(): Promise<void> {
  await stopClient();
}

async function startClient(): Promise<void> {
  if (client) {
    return;
  }
  const workspaceFolders = vscode.workspace.workspaceFolders?.map((folder) => folder.uri.fsPath) ?? [];
  const cwd = workspaceFolders[0] ?? process.cwd();
  const configuredPath = vscode.workspace
    .getConfiguration("contract4agents")
    .get<string>("pythonPath", "");

  try {
    const resolution = await resolvePython(workspaceFolders, configuredPath, cwd);
    output?.appendLine("Python discovery:");
    for (const attempt of resolution.attempts) {
      output?.appendLine(`  ${attempt}`);
    }
    output?.appendLine(`Starting with ${displayCommand(resolution.candidate)}`);
    client = createClient(resolution.candidate, cwd);
    await client.start();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    output?.appendLine(message);
    const action = await vscode.window.showErrorMessage(
      "Contract4Agents language features could not start. Install contract4agents in your workspace environment or configure its Python path.",
      "Show Output",
      "Open Settings",
    );
    if (action === "Show Output") {
      output?.show();
    } else if (action === "Open Settings") {
      await vscode.commands.executeCommand(
        "workbench.action.openSettings",
        "@ext:btfranklin.contract4agents-vscode contract4agents.pythonPath",
      );
    }
  }
}

function createClient(python: PythonCandidate, cwd: string): LanguageClient {
  const serverOptions: ServerOptions = {
    command: python.command,
    args: [...python.args, "-m", "contract4agents.language_server"],
    options: {cwd},
  };
  const documentSelector = [
    {language: "contract4agents", scheme: "file"},
    {language: "contract4agents-eval", scheme: "file"},
  ];
  const clientOptions: LanguageClientOptions = {
    documentSelector,
    outputChannel: output,
    synchronize: {
      fileEvents: vscode.workspace.createFileSystemWatcher("**/*.{contract,eval}"),
    },
  };
  return new LanguageClient(
    "contract4agents",
    "Contract4Agents Language Server",
    serverOptions,
    clientOptions,
  );
}

async function stopClient(): Promise<void> {
  const running = client;
  client = undefined;
  if (running) {
    await running.stop();
  }
}
