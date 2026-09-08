// pha CLI discovery + subprocess runner.
//
// Per the spec (§11 "pha discovery"): resolve from the pha.executablePath
// setting, then PATH, then <workspace>/.venv/bin/pha, then the uv tool
// locations — and report "pha not found" with the exact fix, never guess
// silently. Honour PHA_ARCHIVE_DIR.

import * as cp from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

export class PhaNotFoundError extends Error {}

export class PhaCli {
  private _path: string | undefined;
  constructor(_ctx: vscode.ExtensionContext) {}

  private candidates(): string[] {
    const out: string[] = [];
    const setting = vscode.workspace.getConfiguration("pha").get<string>("executablePath", "");
    if (setting) out.push(setting);
    out.push("pha");
    for (const wf of vscode.workspace.workspaceFolders ?? []) {
      out.push(path.join(wf.uri.fsPath, ".venv", "bin", "pha"));
      out.push(path.join(wf.uri.fsPath, ".venv", "Scripts", "pha.exe"));
    }
    out.push(path.join(process.env.HOME ?? "", ".local", "bin", "pha"));
    return out;
  }

  async resolve(): Promise<string> {
    if (this._path) return this._path;
    for (const cand of this.candidates()) {
      if (cand === "pha") {
        const onPath = await new Promise<boolean>((res) =>
          cp.execFile("pha", ["help"], { timeout: 5000 }, (e) => res(!e)));
        if (onPath) return (this._path = "pha");
      } else if (fs.existsSync(cand)) {
        return (this._path = cand);
      }
    }
    throw new PhaNotFoundError(
      "pha was not found. Set pha.executablePath to the full path of the pha " +
      "executable (e.g. personal-historical-archive/.venv/bin/pha), or install " +
      "it globally with: uv tool install --editable .");
  }

  /** Run pha with args; resolves with combined stdout+stderr text. */
  run(args: string[], opts: { cwd?: string; timeoutMs?: number } = {}): Promise<string> {
    return this.resolve().then((exe) =>
      new Promise<string>((resolve, reject) => {
        cp.execFile(exe, args, {
          cwd: opts.cwd,
          timeout: opts.timeoutMs ?? 0,
          maxBuffer: 32 * 1024 * 1024,
          env: this.env(),
        }, (err, stdout, stderr) => {
          if (err) reject(new Error(`${exe} ${args.join(" ")} failed:\n${stderr || stdout || err.message}`));
          else resolve(String(stdout) + String(stderr));
        });
      }));
  }

  env(): NodeJS.ProcessEnv {
    const env = { ...process.env };
    const archiveDir = archiveDirSetting();
    if (archiveDir) env.PHA_ARCHIVE_DIR = archiveDir;
    return env;
  }
}

export function archiveDirSetting(): string | undefined {
  const v = vscode.workspace.getConfiguration("pha").get<string>("archiveDir", "");
  return v || undefined;
}
