// Page drill-down commands (spec §5): open transcription / edited / encoded,
// split raw ⇄ edited, show the page render image, open whole-document text,
// copy page text.

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { DocRow } from "./db";
import { DocumentDetail, PageDetail } from "./local";

export async function openTranscription(pd: PageDetail): Promise<void> {
  await openOrWarn(pd.rawFile,
    "No transcription file found for this page (not extracted yet). Run PHA: Scan.");
}

export async function openEdited(pd: PageDetail): Promise<void> {
  if (!pd.edit) {
    const cfg = await vscode.window.showInformationMessage(
      "No editor configured for this document — there is no edited variant.",
      "How to set an editor");
    if (cfg === "How to set an editor") {
      vscode.env.openExternal(vscode.Uri.parse(
        "https://github.com/joaquimrcarvalho/personal-historical-archive#editors"));
    }
    return;
  }
  await openOrWarn(pd.editedFile, "Edited variant file not found in the library.");
}

export async function openEncoded(det: DocumentDetail): Promise<void> {
  if (!det.recordFiles.length) {
    vscode.window.showInformationMessage("No encoded records for this document (no encoder configured or not yet encoded).");
    return;
  }
  for (const f of det.recordFiles) await vscode.window.showTextDocument(vscode.Uri.file(f), { preview: true });
}

export async function splitPage(pd: PageDetail): Promise<void> {
  if (!pd.rawFile || !pd.editedFile) {
    vscode.window.showWarningMessage("Split view needs both the raw transcription and the edited page.");
    return;
  }
  await vscode.window.showTextDocument(vscode.Uri.file(pd.rawFile), {
    viewColumn: vscode.ViewColumn.One, preview: false,
  });
  await vscode.window.showTextDocument(vscode.Uri.file(pd.editedFile), {
    viewColumn: vscode.ViewColumn.Two, preview: false,
  });
}

export async function showPageImage(pd: PageDetail): Promise<void> {
  if (!pd.renderFile) {
    vscode.window.showWarningMessage("No cached render for this page (the model's input image).");
    return;
  }
  // Webview panel: image on the left, transcription text on the right.
  const img = fs.readFileSync(pd.renderFile).toString("base64");
  const text = pd.page.raw_text ?? "(no transcription)";
  const panel = vscode.window.createWebviewPanel(
    "phaPageImage", `page ${pd.page.page_no} — image`,
    vscode.ViewColumn.Beside, { enableScripts: false });
  panel.webview.html = `<!DOCTYPE html><html><body style="margin:0;display:flex;gap:8px;font-family:var(--vscode-font-family)">
    <div style="flex:1"><img src="data:image/jpeg;base64,${img}" style="max-width:100%"/></div>
    <pre style="flex:1;white-space:pre-wrap;padding:8px;overflow:auto">${escapeHtml(text)}</pre>
  </body></html>`;
}

export async function openWholeDocument(det: DocumentDetail): Promise<void> {
  // concatenated raw text from the DB rows, shown in an untitled doc
  const text = det.pages.map((p) => `--- page ${p.page.page_no} ---\n${p.page.raw_text ?? ""}`).join("\n\n");
  const doc = await vscode.workspace.openTextDocument({ content: text, language: "markdown" });
  await vscode.window.showTextDocument(doc, { preview: true });
}

export async function copyPageText(pd: PageDetail, edited: boolean): Promise<void> {
  const text = edited ? pd.edit?.text : pd.page.raw_text;
  if (text == null) { vscode.window.showWarningMessage("No text for this variant."); return; }
  await vscode.env.clipboard.writeText(text);
  vscode.window.setStatusBarMessage(`Copied page ${pd.page.page_no} ${edited ? "edited" : "raw"} text`, 3000);
}

async function openOrWarn(file: string | undefined, warn: string): Promise<void> {
  if (file && fs.existsSync(file)) {
    await vscode.window.showTextDocument(vscode.Uri.file(file), { preview: true });
  } else {
    vscode.window.showWarningMessage(warn);
  }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
}

export function docLabel(doc: DocRow): string {
  return path.basename(doc.path);
}
