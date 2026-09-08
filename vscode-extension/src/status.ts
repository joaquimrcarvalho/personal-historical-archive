// Status view (spec §7): archive summary, per-document status, pending
// review, unscanned files. Backed by db.summary + documents + the same
// pending-review rule as the explorer.

import * as vscode from "vscode";
import { LocalConnection } from "./local";
import { PhaNode } from "./explorer";

export class PhaStatus implements vscode.TreeDataProvider<PhaNode> {
  private _onTree = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onTree.event;

  constructor(private readonly conn: LocalConnection) {}
  refresh(): void { this._onTree.fire(); }

  getTreeItem(el: PhaNode): vscode.TreeItem { return el; }

  async getChildren(el?: PhaNode): Promise<PhaNode[]> {
    try {
      if (!this.conn.db.exists) return [err("archive.db not found — run a scan first.")];
      if (!el) {
        await this.conn.init();
        const s = await this.conn.db.summary();
        const nodes: PhaNode[] = [];
        for (const [status, n] of Object.entries(s.byStatus)) {
          const n2 = new PhaNode("section", `${status}: ${n}`);
          n2.iconPath = new vscode.ThemeIcon(
            status === "done" ? "check-all" : status === "error" ? "error" : "sync");
          nodes.push(n2);
        }
        nodes.push(new PhaNode("section",
          `${s.pages} pages · ${s.chunks} chunks indexed · ${s.embedded} embedded`));
        if (s.chunks > s.embedded) {
          const w = new PhaNode("section", "keyword-only chunks — run PHA: Reindex");
          w.iconPath = new vscode.ThemeIcon("warning");
          nodes.push(w);
        }
        // pending review
        const pending = await this.conn.pendingReviewSummary();
        for (const [, v] of pending.docs) {
          const n = new PhaNode("section",
            `⚑ ${v.filename}: ${v.pages.length} page(s) corrected, not imported`);
          n.tooltip = "Library .md edited by a human after export. Run PHA: Import corrections.";
          n.iconPath = new vscode.ThemeIcon("edit");
          nodes.push(n);
        }
        return nodes;
      }
      return []; // sections are leaves
    } catch (e) {
      return [err((e as Error).message)];
    }
  }
}

function err(msg: string): PhaNode {
  const n = new PhaNode("section", `⚠️ ${msg}`);
  n.iconPath = new vscode.ThemeIcon("warning");
  return n;
}
