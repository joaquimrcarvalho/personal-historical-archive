// Managed background jobs (spec §7.3). All mutating pha commands run through
// here: one job at a time, output streamed to a dedicated channel, pha's own
// scan/edit lock decides contention ("another scan/edit job is running" is
// surfaced verbatim, never hidden). The extension does NOT implement a second
// lock.

import { spawn } from "child_process";
import * as vscode from "vscode";
import { PhaCli } from "./pha";

export class JobManager {
  private channel: vscode.OutputChannel;
  private current: { label: string; child: ReturnType<typeof spawn> } | undefined;
  private _onDone = new vscode.EventEmitter<void>();
  readonly onDone = this._onDone.event;

  constructor(private readonly cli: PhaCli) {
    this.channel = vscode.window.createOutputChannel("PHA Jobs");
  }

  get running(): boolean { return !!this.current; }

  show(): void { this.channel.show(true); }

  async run(label: string, args: string[]): Promise<void> {
    if (this.current) {
      const pick = await vscode.window.showWarningMessage(
        `A PHA job is already running (${this.current.label}). pha scan/edit share one ` +
        `local-model lock — starting another local-model job can wedge the machine.`,
        "Show Running Job");
      if (pick) this.channel.show();
      return;
    }
    const exe = await this.cli.resolve();
    const child = spawn(exe, args, { env: this.cli.env() });
    this.current = { label, child };
    this.channel.appendLine(`\n$ pha ${args.join(" ")}`);
    child.stdout.on("data", (d) => this.channel.append(String(d)));
    child.stderr.on("data", (d) => this.channel.append(String(d)));
    vscode.commands.executeCommand("setContext", "pha.jobRunning", true);
    return new Promise<void>((resolve) => {
      child.on("close", (code) => {
        this.channel.appendLine(`\n[exit ${code ?? "signal"}] ${label}`);
        if (code !== 0) {
          this.channel.show(true);
          vscode.window.showErrorMessage(`PHA ${label} failed (exit ${code}). See PHA Jobs output.`);
        }
        this.current = undefined;
        vscode.commands.executeCommand("setContext", "pha.jobRunning", false);
        this._onDone.fire();
        resolve();
      });
    });
  }
}
