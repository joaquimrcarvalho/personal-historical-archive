// The PHA Explorer tree (spec §4/§5): dropbox documents + collections (with
// unscanned files), the on-hold inbox, and the Configuration section.

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { DocRow } from "./db";
import { DocumentDetail, DropboxUnit, LocalConnection } from "./local";

export type NodeKind =
  | "root" | "section" | "collection" | "document" | "page"
  | "inboxFile" | "configSection" | "configFile";

export class PhaNode extends vscode.TreeItem {
  constructor(
    public readonly kind: NodeKind,
    label: string,
    public readonly data?: unknown,
  ) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.contextValue = kind;
  }
}

export class PhaExplorer implements vscode.TreeDataProvider<PhaNode> {
  private _onTree = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onTree.event;
  private detailCache = new Map<number, DocumentDetail>();

  constructor(private readonly conn: LocalConnection) {}

  refresh(): void {
    this.detailCache.clear();
    this._onTree.fire();
  }

  getTreeItem(el: PhaNode): vscode.TreeItem { return el; }

  async getChildren(el?: PhaNode): Promise<PhaNode[]> {
    try {
      if (!el) return await this.rootsList();
      switch (el.kind) {
        case "section": {
          const label = String(el.label);
          if (label === "Documents") return this.unitsUnder("documents");
          if (label === "Collections") return this.unitsUnder("collections");
          if (label.startsWith("Inbox")) {
            const p = this.conn.paths;
            return this.conn.inboxFiles().sort().map((f) => {
              const n = new PhaNode("inboxFile", f, path.join(p.inbox, f));
              n.description = "on hold";
              n.tooltip = "Never scanned. 'Move to dropbox' runs `pha inbox --move`, then scan.";
              n.iconPath = new vscode.ThemeIcon("circle-slash");
              return n;
            });
          }
          return [];
        }
        case "collection":
          return this.unitsUnder(el.data as string);
        case "document": {
          const doc = el.data as DocRow;
          const det = await this.documentDetail(doc);
          const nodes = det.pages.map((p) => {
            const bits: string[] = [`p${String(p.page.page_no).padStart(3, "0")}`];
            if (p.page.status === "done") bits.push("✓");
            if (p.page.reviewed_at) bits.push("reviewed");
            if (p.pendingReview) bits.push("⚑ corrections pending");
            const n = new PhaNode("page", bits.join(" · "), p);
            n.description = p.edit ? `edited: ${p.edit.editor}` : "raw only";
            n.tooltip = p.page.error ?? undefined;
            n.iconPath = new vscode.ThemeIcon(p.pendingReview ? "edit" : "file-text");
            n.command = { command: "pha.openTranscription", title: "Open Transcription", arguments: [n] };
            return n;
          });
          if (!det.pages.length) {
            const n = new PhaNode("page", "no pages extracted yet");
            n.iconPath = new vscode.ThemeIcon("circle-outline");
            return [n];
          }
          return nodes;
        }
        case "configSection": return this.configChildren(el.label as string);
        default: return [];
      }
    } catch (e) {
      return [errorNode(e)];
    }
  }

  private async rootsList(): Promise<PhaNode[]> {
    const p = await this.conn.init();
    const docs: PhaNode[] = [];
    const s1 = new PhaNode("section", "Documents");
    s1.iconPath = new vscode.ThemeIcon("folder-library");
  s1.collapsibleState = vscode.TreeItemCollapsibleState.Collapsed;
    const s2 = new PhaNode("section", "Collections");
    s2.iconPath = new vscode.ThemeIcon("folder-library");
  s2.collapsibleState = vscode.TreeItemCollapsibleState.Collapsed;
    docs.push(s1, s2);
    // inbox — only when it exists and has files
    if (fs.existsSync(p.inbox)) {
      const files = this.conn.inboxFiles();
      if (files.length) {
        const s3 = new PhaNode("section", `Inbox (${files.length} on hold)`);
        s3.iconPath = new vscode.ThemeIcon("inbox");
        s3.collapsibleState = vscode.TreeItemCollapsibleState.Collapsed;
        s3.tooltip = "On hold — never scanned. `pha inbox --move` brings them into the dropbox.";
        docs.push(s3);
      }
    }
    const cfg = new PhaNode("configSection", "Configuration");
    cfg.iconPath = new vscode.ThemeIcon("settings-gear");
    cfg.collapsibleState = vscode.TreeItemCollapsibleState.Collapsed;
    docs.push(cfg);
    return docs;
  }

  private async unitsUnder(prefix: string): Promise<PhaNode[]> {
    const units = await this.conn.dropboxUnits();
    const out: PhaNode[] = [];
    if (prefix === "documents") {
      // loose documents: directly at the dropbox root OR inside documents/
      // (pha's seeded layout puts individual sources in dropbox/documents/)
      for (const u of units) {
        if (u.relPath.includes("/")) {
          if (!u.relPath.startsWith("documents/")) continue;
          if (u.relPath.slice("documents/".length).includes("/")) continue;
        }
        out.push(this.unitNode(u));
      }
      return out;
    }
    if (prefix === "collections") {
      const cols = new Set<string>();
      for (const u of units) {
        const m = u.relPath.match(/^collections\/([^/]+)/);
        if (m) cols.add(m[1]);
      }
      for (const c of [...cols].sort()) {
        const n = new PhaNode("collection", c, `collections/${c}`);
        n.iconPath = new vscode.ThemeIcon("file-directory");
        n.collapsibleState = vscode.TreeItemCollapsibleState.Collapsed;
        out.push(n);
      }
      return out;
    }
    // inside one collection: units whose path starts with the prefix
    for (const u of units) {
      if (u.relPath.startsWith(`${prefix}/`) && u.relPath.slice(prefix.length + 1) !== "") {
        const rest = u.relPath.slice(prefix.length + 1);
        if (rest.includes("/")) continue; // nested; shown via DB dir grouping later
        out.push(this.unitNode(u));
      }
    }
    return out;
  }

  private unitNode(u: DropboxUnit): PhaNode {
    if (u.doc) {
      const d = u.doc;
      const n = new PhaNode("document", u.name, d);
      n.description = [
        d.status,
        d.page_count != null ? `${d.page_count}p` : "",
        d.palaeographer ?? "",
      ].filter(Boolean).join(" · ");
      n.tooltip = new vscode.MarkdownString(
        `**${u.name}** — ${d.status}\n\n- id: ${d.id}\n- palaeographer: ${d.palaeographer ?? "—"}\n` +
        `- editor: ${d.editor ?? "none"}\n- encoder: ${d.encoder ?? "none"}\n- pages: ${d.page_count ?? 0}` +
        (d.error ? `\n\nerror: ${d.error}` : ""));
      n.iconPath = new vscode.ThemeIcon(
        d.status === "done" ? "file-text" : d.status === "error" ? "error" : "sync~spin");
      n.collapsibleState = vscode.TreeItemCollapsibleState.Collapsed;
      n.command = { command: "pha.openDocumentWhole", title: "Open Document", arguments: [n] };
      return n;
    }
    const n = new PhaNode("document", u.name, { relPath: u.relPath, unscanned: true, isDir: u.isDir });
    n.description = "not yet scanned";
    n.iconPath = new vscode.ThemeIcon("circle-outline");
    return n;
  }

  private async documentDetail(doc: DocRow): Promise<DocumentDetail> {
    if (!this.detailCache.has(doc.id)) {
      this.detailCache.set(doc.id, await this.conn.documentDetail(doc));
    }
    return this.detailCache.get(doc.id)!;
  }

  // ---- configuration tree -----------------------------------------------

  private configChildren(section?: string): PhaNode[] {
    const dirs: [string, string][] = [
      ["Models", "models"],
      ["Palaeographers", "palaeographers"],
      ["Editors", "editors"],
      ["Encoders", "encoders"],
    ];
    if (!section || section === "Configuration") {
      return dirs.map(([label, dir]) => {
        const n = new PhaNode("configSection", label, dir);
        n.iconPath = new vscode.ThemeIcon("folder");
        n.collapsibleState = vscode.TreeItemCollapsibleState.Collapsed;
        return n;
      });
    }
    const dir = dirs.find(([l]) => l === section)?.[1];
    if (!dir) return [];
    // definition files live under the archive root (models/palaeographers/...)
    // plus, for encoders, collection-local dropbox/collections/COL/encoders/
    const nodes: PhaNode[] = [];
    const root = path.join(this.conn.paths?.root ?? "", dir);
    try {
      for (const f of fs.readdirSync(root).sort()) {
        if (!f.endsWith(".md")) continue;
        const n = new PhaNode("configFile", f, path.join(root, f));
        n.iconPath = new vscode.ThemeIcon(f.startsWith("_sample") ? "gift" : "file-code");
        n.description = f.startsWith("_sample") ? "template" : "";
        n.command = { command: "pha.openConfigFile", title: "Open Configuration File", arguments: [n] };
        nodes.push(n);
      }
    } catch { /* dir missing */ }
    return nodes;
  }
}

function errorNode(e: unknown): PhaNode {
  const n = new PhaNode("section", `⚠️ ${(e as Error).message.split("\n")[0]}`);
  n.iconPath = new vscode.ThemeIcon("warning");
  n.tooltip = (e as Error).message;
  return n;
}
