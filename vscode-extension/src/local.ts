// LocalConnection (spec §3): filesystem + read-only SQLite + the pha CLI.
// Resolves the archive dir exactly like pha (PHA_ARCHIVE_DIR env > .env in the
// project dir > config.yaml paths.archive_dir > workspace root) and merges
// the three disjoint sets (documents DB / unscanned dropbox files / inbox).

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { DocRow, EditRow, PageRow, ReadOnlyDb, dbPathFor } from "./db";
import { PhaCli, archiveDirSetting } from "./pha";

export interface ArchivePaths {
  root: string;      // archive_dir
  dropbox: string;
  inbox: string;
  library: string;
  renders: string;
  db: string;
}

export interface PageDetail {
  page: PageRow;
  edit?: EditRow;
  rawFile?: string;       // library .../transcription-*/<page>.md
  editedFile?: string;    // library .../edited-*/<page>.md
  renderFile?: string;    // renders/<sha256>/pNNN.jpg or <source stem>.jpg
  pendingReview: boolean; // file mtime newer than exported_at (human-corrected, unimported)
}

export interface DocumentDetail {
  doc: DocRow;
  pages: PageDetail[];
  recordFiles: string[];  // library .../records-*.json + concatenated-*.md
    config: { palaeographer?: string; editor?: string; encoders?: string[] };
}

const DOC_EXTS = new Set([".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"]);

export class LocalConnection {
  readonly db: ReadOnlyDb;
  private _paths: ArchivePaths | undefined;

  get paths(): ArchivePaths {
    return this._paths as ArchivePaths;
  }

  constructor(_cli: PhaCli) {
    // placeholder; real path set in init()
    this.db = new ReadOnlyDb("");
  }

  async init(): Promise<ArchivePaths> {
    if (this._paths) return this._paths;
    const root = await this.resolveArchiveDir();
    const dbPath = dbPathFor(root);
    (this.db as { setDbPath: (p: string) => void }).setDbPath(dbPath);
    this._paths = {
      root,
      dropbox: path.join(root, "dropbox"),
      inbox: await this.inboxDir(root),
      library: path.join(root, "library"),
      renders: path.join(root, "renders"),
      db: dbPath,
    };
    return this._paths;
  }

  private async inboxDir(root: string): Promise<string> {
    // paths.inbox in config.yaml may override the default; fall back to <root>/inbox
    const cfgPath = path.join(root, "config.yaml");
    try {
      const text = fs.readFileSync(cfgPath, "utf8");
      const m = text.match(/^\s*inbox:\s*(\S+)/m);
      if (m && path.isAbsolute(m[1])) return m[1];
      if (m) return path.join(root, m[1]);
    } catch { /* no config.yaml */ }
    return path.join(root, "inbox");
  }

  private async resolveArchiveDir(): Promise<string> {
    const setting = archiveDirSetting();
    if (setting) return setting;
    if (process.env.PHA_ARCHIVE_DIR) return process.env.PHA_ARCHIVE_DIR;
    for (const wf of vscode.workspace.workspaceFolders ?? []) {
      // .env beside the project (pha set archive-dir writes PHA_ARCHIVE_DIR=...)
      const envFile = path.join(wf.uri.fsPath, ".env");
      if (fs.existsSync(envFile)) {
        const m = fs.readFileSync(envFile, "utf8").match(/^PHA_ARCHIVE_DIR=(.+)$/m);
        if (m) return m[1].trim().replace(/^["']|["']$/g, "");
      }
      // config.yaml paths.archive_dir
      const cfg = path.join(wf.uri.fsPath, "config.yaml");
      if (fs.existsSync(cfg)) {
        const m = fs.readFileSync(cfg, "utf8").match(/^\s*archive_dir:\s*(\S+)/m);
        if (m) {
          const v = m[1].replace(/^["']|["']$/g, "");
          return path.isAbsolute(v) ? v : path.join(wf.uri.fsPath, v);
        }
      }
      // an archive opened directly as the workspace folder
      if (fs.existsSync(path.join(wf.uri.fsPath, "dropbox"))) return wf.uri.fsPath;
    }
    throw new Error(
      "No pha archive is configured or found. Set the pha.archiveDir setting " +
      "(or run `pha set archive-dir <path>` in the project that has the .env).");
  }

  // ---- dropbox tree ----------------------------------------------------

  /** Documents rows keyed by dropbox-relative path. */
  private docIndex: Map<string, DocRow> = new Map();

  async loadDocs(): Promise<Map<string, DocRow>> {
    const docs = await this.db.documents();
    this.docIndex = new Map(docs.map((d) => [d.path, d]));
    return this.docIndex;
  }

  /**
   * Walk the dropbox and return every archive unit:
   * scanned docs (with DB row) and unscanned files, in path order.
   * A directory containing only images collapses to one unit (dir-of-images).
   */
  async dropboxUnits(): Promise<DropboxUnit[]> {
    const p = await this.init();
    await this.loadDocs();
    const units: DropboxUnit[] = [];
    const walk = (rel: string, abs: string, depth: number) => {
      let entries: fs.Dirent[];
      try { entries = fs.readdirSync(abs, { withFileTypes: true }); }
      catch { return; }
      // collection config files / encoder definitions are not documents
      const files = entries.filter(
        (e) => e.isFile() && DOC_EXTS.has(path.extname(e.name).toLowerCase()));
      const dirs = entries.filter((e) => e.isDirectory());
      const onlyImages = files.length > 0 && dirs.length === 0 &&
        files.every((f) => [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"]
          .includes(path.extname(f.name).toLowerCase()));
      if (onlyImages && rel !== "") {
        // whole directory is ONE document unit
        const docRel = rel;
        units.push({
          relPath: docRel,
          name: path.basename(rel),
          isDir: true,
          doc: this.docIndex.get(docRel),
        });
        return;
      }
      for (const f of files) {
        const relFile = rel ? `${rel}/${f.name}` : f.name;
        units.push({ relPath: relFile, name: f.name, isDir: false, doc: this.docIndex.get(relFile) });
      }
      for (const d of dirs) {
        walk(rel ? `${rel}/${d.name}` : d.name, path.join(abs, d.name), depth + 1);
      }
    };
    if (fs.existsSync(p.dropbox)) walk("", p.dropbox, 0);
    return units;
  }

  /** Unscanned inbox files (on hold — never scanned; `pha inbox --move`). */
  inboxFiles(): string[] {
    // sync use guarded by init() having run; returns file names.
    try {
      return fs.readdirSync((this._paths as ArchivePaths).inbox).filter(
        (f) => fs.statSync(path.join((this._paths as ArchivePaths).inbox, f)).isFile());
    } catch { return []; }
  }

  // ---- document detail --------------------------------------------------

  /** Resolve the document's library folder: library/<dir_path>/<stem>_<date>/ (newest wins). */
  libraryDir(doc: DocRow): string | undefined {
    const p = this._paths!;
    const relDir = doc.dir_path && doc.dir_path !== "(root)" ? doc.dir_path : "";
    const stem = path.basename(doc.path, path.extname(doc.path));
    const base = path.join(p.library, relDir);
    if (!fs.existsSync(base)) return undefined;
    const candidates = fs.readdirSync(base)
      .filter((d) => d.startsWith(`${stem}_`) || d === stem)
      .sort()
      .map((d) => path.join(base, d))
      .filter((d) => fs.statSync(d).isDirectory());
    return candidates.length ? candidates[candidates.length - 1] : undefined;
  }

  async documentDetail(doc: DocRow): Promise<DocumentDetail> {
    const pages = await this.db.pages(doc.id);
    const edits = await this.db.edits(doc.id);
    const editByPage = new Map(edits.map((e) => [e.page_id, e]));
    const lib = this.libraryDir(doc);

    const pageDetails: PageDetail[] = pages.map((pg) => {
      const edit = editByPage.get(pg.id);
      let rawFile: string | undefined;
      let editedFile: string | undefined;
      if (lib) {
        // page file: source stem (dir-of-images) else page-NNN
        const pageName = pg.source_name
          ? pg.source_name.replace(/\.[^.]+$/, ".md")
          : `page-${String(pg.page_no).padStart(3, "0")}.md`;
        rawFile = findVariant(lib, "transcription-", pageName);
        editedFile = edit ? findVariant(lib, "edited-", pageName) : undefined;
      }
      let renderFile: string | undefined;
      const rdir = path.join(this._paths!.renders, doc.sha256);
      if (fs.existsSync(rdir)) {
        const cand = pg.source_name
          ? path.join(rdir, pg.source_name.replace(/\.[^.]+$/, ".jpg"))
          : path.join(rdir, `p${String(pg.page_no).padStart(3, "0")}.jpg`);
        renderFile = fs.existsSync(cand) ? cand : undefined;
      }
      const exported = Math.max(pg.exported_at ?? 0, edit?.exported_at ?? 0);
      const pending = !!(exported > 0) && !!(rawFile || editedFile) &&
        Math.max(mtime(rawFile), mtime(editedFile)) > exported;
      return { page: pg, edit, rawFile, editedFile, renderFile, pendingReview: pending };
    });

    const recordFiles: string[] = [];
    if (lib) {
      for (const f of fs.readdirSync(lib)) {
        if (f.startsWith("records-") && f.endsWith(".json")) recordFiles.push(path.join(lib, f));
        if (f.startsWith("concatenated-") && f.endsWith(".md")) recordFiles.push(path.join(lib, f));
      }
    }
    return {
      doc,
      pages: pageDetails,
      recordFiles,
      config: {
        palaeographer: doc.palaeographer ?? undefined,
        editor: doc.editor ?? undefined,
      },
    };
  }

  /** Human-corrected-but-unimported pages across the archive (from `pha status`). */
  async pendingReviewSummary(): Promise<{ docs: Map<number, { filename: string; pages: number[] }> }> {
    const result = new Map<number, { filename: string; pages: number[] }>();
    const docs = await this.db.documents();
    for (const d of docs) {
      const det = await this.documentDetail(d);
      const pages = det.pages.filter((p) => p.pendingReview).map((p) => p.page.page_no);
      if (pages.length) result.set(d.id, { filename: d.filename, pages });
    }
    return { docs: result };
  }
}

export interface DropboxUnit {
  relPath: string;          // dropbox-relative
  name: string;
  isDir: boolean;           // directory-of-images unit
  doc?: DocRow;             // present when scanned/archived
}

function findVariant(libDir: string, prefix: string, pageName: string): string | undefined {
  try {
    for (const d of fs.readdirSync(libDir)) {
      if (d.startsWith(prefix)) {
        const f = path.join(libDir, d, pageName);
        if (fs.existsSync(f)) return f;
      }
    }
  } catch { /* lib dir unreadable */ }
  return undefined;
}

function mtime(f?: string): number {
  if (!f) return 0;
  try { return fs.statSync(f).mtimeMs / 1000; } catch { return 0; }
}

export function homeRelative(p: string): string {
  const h = os.homedir();
  return p.startsWith(h) ? `~${p.slice(h.length)}` : p;
}
