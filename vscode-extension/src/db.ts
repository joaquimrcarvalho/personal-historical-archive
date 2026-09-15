// Read-only access to archive.db.
//
// pha guarantees a Python on the archive machine, so queries go through a
// small python3 one-liner that opens the DB with mode=ro (immutable to the
// extension: it can never contend with or write to a running scan's WAL).
// This avoids shipping a native SQLite Node module. Rows come back as JSON.

import { execFile } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

export interface DocRow {
  id: number;
  filename: string;
  path: string;        // relative to the dropbox, as stored by pha
  sha256: string;
  kind: string | null;
  page_count: number | null;
  status: string;
  dir_path: string | null;
  palaeographer: string | null;
  editor: string | null;
  encoder: string | null;
  palaeographer_model: string | null;
  editor_model: string | null;
  error: string | null;
}

export interface PageRow {
  id: number;
  document_id: number;
  page_no: number;
  status: string;
  raw_text: string | null;
  source_name: string | null;
  reviewed_at: number | null;
  exported_at: number | null;
  error: string | null;
}

export interface EditRow {
  page_id: number;
  editor: string;
  status: string;
  text: string | null;
  reviewed_at: number | null;
  exported_at: number | null;
}

export class ReadOnlyDb {
  private dbPath: string;

  constructor(dbPath: string) { this.dbPath = dbPath; }

  setDbPath(p: string): void { this.dbPath = p; }

  get exists(): boolean {
    return fs.existsSync(this.dbPath);
  }

  /** Run one SQL statement; returns rows as plain objects. */
  query(sql: string, params: unknown[] = []): Promise<Record<string, unknown>[]> {
    const python = vscode.workspace.getConfiguration("pha").get<string>("pythonPath", "python3");
    // args after -- separate our flags from SQL text; params travel via env to
    // avoid any quoting-in-shell issues (execFile already avoids the shell).
    const script = [
      "import json, os, sqlite3",
      // mode=ro fails ("unable to open database file") while a scan/serve holds
      // the DB in WAL (a ro connection cannot create the -shm file); fall back
      // to immutable=1, which reads the last checkpointed state without WAL.
      "try:",
      "    conn = sqlite3.connect('file:' + os.environ['PHA_DB'] + '?mode=ro', uri=True)",
      "    conn.execute('SELECT 1')",
      "except sqlite3.OperationalError:",
      "    conn = sqlite3.connect('file:' + os.environ['PHA_DB'] + '?immutable=1', uri=True)",
      "conn.row_factory = sqlite3.Row",
      "rows = conn.execute(os.environ['PHA_SQL'], json.loads(os.environ['PHA_PARAMS'])).fetchall()",
      "print(json.dumps([dict(r) for r in rows]))",
    ].join("\n");
    return new Promise((resolve, reject) => {
      execFile(
        python, ["-c", script],
        {
          env: {
            ...process.env,
            PHA_DB: this.dbPath,
            PHA_SQL: sql,
            PHA_PARAMS: JSON.stringify(params),
          },
          timeout: 15000,
          maxBuffer: 64 * 1024 * 1024,
        },
        (err, stdout, stderr) => {
          if (err) reject(new Error(`DB query failed: ${stderr || err.message}`));
          else {
            try { resolve(JSON.parse(String(stdout))); }
            catch (e) { reject(new Error(`DB query returned bad JSON: ${String(stdout).slice(0, 200)}`)); }
          }
        });
    });
  }

  documents(): Promise<DocRow[]> {
    return this.query(
      "SELECT id, filename, path, sha256, kind, page_count, status, dir_path," +
      " palaeographer, editor, encoder, palaeographer_model, editor_model, error" +
      " FROM documents ORDER BY dir_path, filename") as unknown as Promise<DocRow[]>;
  }

  pages(docId: number): Promise<PageRow[]> {
    return this.query(
      "SELECT id, document_id, page_no, status, raw_text, source_name, reviewed_at," +
      " exported_at, error FROM pages WHERE document_id = ? ORDER BY page_no",
      [docId]) as unknown as Promise<PageRow[]>;
  }

  edits(docId: number): Promise<EditRow[]> {
    return this.query(
      "SELECT pe.page_id, pe.editor, pe.status, pe.text, pe.reviewed_at, pe.exported_at" +
      " FROM page_edits pe JOIN pages p ON p.id = pe.page_id" +
      " WHERE p.document_id = ? ORDER BY p.page_no",
      [docId]) as unknown as Promise<EditRow[]>;
  }

  async summary(): Promise<{ documents: number; byStatus: Record<string, number>; pages: number; chunks: number; embedded: number }> {
    const docs = await this.query(
      "SELECT status, COUNT(*) n FROM documents GROUP BY status");
    const pc = await this.query("SELECT COUNT(*) n FROM pages");
    const ch = await this.query(
      "SELECT COUNT(*) n, SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) emb FROM chunks");
    const byStatus: Record<string, number> = {};
    for (const r of docs) byStatus[String(r.status)] = Number(r.n);
    return {
      documents: Object.values(byStatus).reduce((a, b) => a + b, 0),
      byStatus,
      pages: Number(pc[0]?.n ?? 0),
      chunks: Number(ch[0]?.n ?? 0),
      embedded: Number(ch[0]?.emb ?? 0),
    };
  }

}

export function dbPathFor(archiveDir: string): string {
  return path.join(archiveDir, "archive.db");
}
