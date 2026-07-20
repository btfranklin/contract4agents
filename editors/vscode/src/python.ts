import {spawn} from "node:child_process";
import path from "node:path";

export interface PythonCandidate {
  command: string;
  args: readonly string[];
  source: string;
}

export interface PythonProbeResult {
  ok: boolean;
  detail: string;
}

export interface PythonResolution {
  candidate: PythonCandidate;
  attempts: readonly string[];
}

export type PythonProbe = (candidate: PythonCandidate, cwd: string) => Promise<PythonProbeResult>;

export async function resolvePython(
  workspaceFolders: readonly string[],
  configuredPath: string,
  cwd: string,
  probe: PythonProbe = probePython,
): Promise<PythonResolution> {
  const candidates = pythonCandidates(workspaceFolders, configuredPath);
  const attempts: string[] = [];

  for (const candidate of candidates) {
    const result = await probe(candidate, cwd);
    attempts.push(`${displayCommand(candidate)} (${candidate.source}) — ${result.detail}`);
    if (result.ok) {
      return {candidate, attempts};
    }
    if (candidate.source === "configured setting") {
      break;
    }
  }

  throw new Error(
    [
      "No Python interpreter could import contract4agents.language_server.",
      ...attempts.map((attempt) => `- ${attempt}`),
    ].join("\n"),
  );
}

export function pythonCandidates(
  workspaceFolders: readonly string[],
  configuredPath: string,
): readonly PythonCandidate[] {
  const candidates: PythonCandidate[] = [];
  if (configuredPath.trim()) {
    candidates.push({
      command: expandWorkspaceFolder(configuredPath.trim(), workspaceFolders[0]),
      args: [],
      source: "configured setting",
    });
  } else {
    for (const folder of workspaceFolders) {
      candidates.push(
        {
          command: path.join(folder, ".venv", "bin", "python"),
          args: [],
          source: "workspace .venv",
        },
        {
          command: path.join(folder, ".venv", "Scripts", "python.exe"),
          args: [],
          source: "workspace .venv",
        },
      );
    }
    if (process.platform === "win32") {
      candidates.push({command: "py", args: ["-3"], source: "system PATH"});
    }
    candidates.push(
      {command: "python3", args: [], source: "system PATH"},
      {command: "python", args: [], source: "system PATH"},
    );
  }
  return deduplicate(candidates);
}

export function displayCommand(candidate: PythonCandidate): string {
  return [candidate.command, ...candidate.args].join(" ");
}

async function probePython(candidate: PythonCandidate, cwd: string): Promise<PythonProbeResult> {
  const probe = "import contract4agents.language_server";
  return new Promise((resolve) => {
    const child = spawn(candidate.command, [...candidate.args, "-c", probe], {
      cwd,
      env: process.env,
      stdio: ["ignore", "ignore", "pipe"],
      windowsHide: true,
    });
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      resolve({ok: false, detail: "timed out while importing the language server"});
    }, 5_000);
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({ok: false, detail: error.message});
    });
    child.on("exit", (code) => {
      clearTimeout(timer);
      if (code === 0) {
        resolve({ok: true, detail: `ready (${candidate.source})`});
        return;
      }
      resolve({ok: false, detail: stderr.trim() || `exited with code ${code ?? "unknown"}`});
    });
  });
}

function expandWorkspaceFolder(value: string, workspaceFolder: string | undefined): string {
  if (!workspaceFolder) {
    return value;
  }
  return value.replaceAll("${workspaceFolder}", workspaceFolder);
}

function deduplicate(candidates: readonly PythonCandidate[]): readonly PythonCandidate[] {
  const seen = new Set<string>();
  return candidates.filter((candidate) => {
    const key = JSON.stringify([candidate.command, candidate.args]);
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}
