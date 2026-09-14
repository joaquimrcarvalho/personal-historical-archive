// PHA VS Code extension — Phase 1 MVP (spec: VSCODE_EXTENSION_SPEC.md §16).
// LocalConnection only: filesystem + read-only archive.db + the pha CLI.
// Everything mutating goes through pha as a managed job (one at a time, pha's
// own lock decides contention).

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { PhaExplorer, PhaNode } from "./explorer";
import { JobManager } from "./jobs";
import { DocRow } from "./db";
import { LocalConnection } from "./local";
import * as pages from "./pages";
import { PhaCli } from "./pha";
import { PhaStatus } from "./status";

export function activate(_context: vscode.ExtensionContext) {
  const cli = new PhaCli(_context);
  const conn = new LocalConnection(cli);
  const jobs = new JobManager(cli);
  const explorer = new PhaExplorer(conn);
  const status = new PhaStatus(conn);

  _context.subscriptions.push(
    vscode.window.registerTreeDataProvider("phaExplorer", explorer),
    vscode.window.registerTreeDataProvider("phaStatus", status),
    jobs.onDone(() => { explorer.refresh(); status.refresh(); }),
  );

  const statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusBarItem.command = "pha.refresh";
  statusBarItem.show();
  _context.subscriptions.push(statusBarItem);
  const updateStatusBar = async () => {
    try {
      await conn.init();
      if (!conn.db.exists) { statusBarItem.text = "$(database) PHA: no archive.db yet"; return; }
      const s = await conn.db.summary();
      const pending = await conn.pendingReviewSummary();
      const pendingCount = pending.docs.size;
      statusBarItem.text = `$(database) PHA ${s.documents} docs · ${s.pages}p` +
        (pendingCount ? ` · $(edit) ${pendingCount} pending review` : "");
      statusBarItem.tooltip = Object.entries(s.byStatus).map(([k, v]) => `${k}: ${v}`).join("\n");
    } catch (e) {
      statusBarItem.text = "$(database) PHA";
      statusBarItem.tooltip = (e as Error).message;
    }
  };
  const refreshAll = async () => {
    explorer.refresh();
    status.refresh();
    await updateStatusBar();
  };
  _context.subscriptions.push({ dispose: () => statusBarItem.dispose() });
  void updateStatusBar();

  const reg = (id: string, fn: (...a: never[]) => unknown) =>
    _context.subscriptions.push(vscode.commands.registerCommand(id, fn));

  // ---- refresh ----
  reg("pha.refresh", refreshAll);

  // ---- pipeline jobs (through JobManager — one at a time, lock-aware) ----
  reg("pha.scan", () => jobs.run("scan", ["scan"]).then(refreshAll));
  reg("pha.scanPath", (n?: PhaNode) => withRelPath(n, (rel) =>
    jobs.run("scan", ["scan", "--path", rel]).then(refreshAll)));
  reg("pha.edit", () => jobs.run("edit", ["edit"]).then(refreshAll));
  reg("pha.editPath", (n?: PhaNode) => withRelPath(n, (rel) =>
    jobs.run("edit", ["edit", "--path", rel]).then(refreshAll)));
  reg("pha.encode", () => jobs.run("encode", ["encode"]).then(refreshAll));
  reg("pha.reindex", () => jobs.run("reindex", ["reindex"]).then(refreshAll));
  reg("pha.review", () => confirmStaleness(
    "Import corrections from the library .md files into the DB? Reviewed pages are stamped and never overwritten by later scans.",
    () => jobs.run("review", ["review"]).then(refreshAll)));
  reg("pha.reviewDoc", (n?: PhaNode) => {
    const doc = n?.data as DocRow | undefined;
    if (!doc) return;
    return confirmStaleness(
      `Import corrections for ${doc.filename}?`,
      () => jobs.run("review", ["review", "--doc", String(doc.id)]).then(refreshAll));
  });
  reg("pha.doctor", () => jobs.run("doctor", ["doctor"]));
  reg("pha.showOutput", () => jobs.show());

  reg("pha.inboxMove", async (n?: PhaNode) => {
    if (!n) return;
    const name = String(n.label);
    const go = await vscode.window.showInformationMessage(
      `Move ${name} from the inbox into the dropbox? It will be scanned on the next scan.`,
      "Move");
    if (go !== "Move") return;
    await jobs.run("inbox --move", ["inbox", "--move"]);
    await jobs.run("scan", ["scan"]);
    await refreshAll();
  });

  // ---- page drill-down ----
  reg("pha.openTranscription", (n?: PhaNode) => n && pages.openTranscription(n.data as never));
  reg("pha.openEdited", (n?: PhaNode) => n && pages.openEdited(n.data as never));
  reg("pha.openEncoded", async (n?: PhaNode) => {
    if (!n) return;
    const doc = n.data as DocRow;
    await pages.openEncoded(await conn.documentDetail(doc));
  });
  reg("pha.splitPage", (n?: PhaNode) => n && pages.splitPage(n.data as never));
  reg("pha.showPageImage", (n?: PhaNode) => n && pages.showPageImage(n.data as never));
  reg("pha.openDocumentWhole", async (n?: PhaNode) => {
    if (!n) return;
    const doc = n.data as DocRow;
    if (!doc.id) {
      vscode.window.showInformationMessage(
        "This document is not yet scanned — run PHA: Scan to extract it first.");
      return;
    }
    await pages.openWholeDocument(await conn.documentDetail(doc));
  });

  // ---- configuration files ----
  reg("pha.openConfigFile", (n?: PhaNode) => {
    const file = n?.data as string | undefined;
    if (file && fs.existsSync(file)) vscode.window.showTextDocument(vscode.Uri.file(file));
  });
  reg("pha.newFromSample", async (n?: PhaNode) => {
    const section = String(n?.label ?? n?.data ?? "");
    const dirBySection: Record<string, string> = {
      Models: "models", Palaeographers: "palaeographers", Editors: "editors",
      Encoders: "encoders", Configuration: "",
    };
    const dir = dirBySection[section];
    if (!dir) return;
    const samples = fs.readdirSync(path.join(conn.paths.root, dir))
      .filter((f) => f.startsWith("_sample"));
    const pick = await vscode.window.showQuickPick(samples, { placeHolder: `Duplicate which sample into ${dir}/?` });
    if (!pick) return;
    const id = await vscode.window.showInputBox({
      prompt: "New definition id (file stem)", validateInput: (v) => /\s/.test(v) ? "No spaces" : null });
    if (!id) return;
    const dest = path.join(conn.paths.root, dir, `${id}.md`);
    if (fs.existsSync(dest)) { vscode.window.showWarningMessage(`${id}.md already exists.`); return; }
    fs.copyFileSync(path.join(conn.paths.root, dir, pick), dest);
    await vscode.window.showTextDocument(vscode.Uri.file(dest));
    vscode.window.showWarningMessage(
      `Saved ${id}.md. Editing a definition triggers re-processing of affected documents on the next pass (mtime staleness).`);
    explorer.refresh();
  });

  // file watcher: keep the tree honest when scans run outside the editor
  const watch = async () => { await refreshAll(); };
  if (vscode.workspace.workspaceFolders?.length) {
    const w1 = vscode.workspace.createFileSystemWatcher("**/dropbox/**");
    const w2 = vscode.workspace.createFileSystemWatcher("**/library/**/*.md");
    for (const w of [w1, w2]) {
      _context.subscriptions.push(w);
      w.onDidChange(watch); w.onDidCreate(watch); w.onDidDelete(watch);
    }
  }

  async function withRelPath(n: PhaNode | undefined, fn: (rel: string) => Promise<unknown>) {
    if (!n) return;
    const data = n.data as { relPath?: string } | string | undefined;
    const rel = typeof data === "string" ? data : data?.relPath;
    if (!rel) {
      vscode.window.showWarningMessage("Select a collection or document in the PHA tree first.");
      return;
    }
    await fn(rel);
  }

  async function confirmStaleness(msg: string, go: () => Promise<unknown>) {
    const ok = await vscode.window.showInformationMessage(msg, { modal: true }, "Continue");
    if (ok === "Continue") await go();
  }
}

export function deactivate() { /* nothing to clean up beyond subscriptions */ }
