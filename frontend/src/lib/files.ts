import type { PickedFile } from "../api/client";

export const ACCEPT = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.txt,application/pdf,image/png,image/jpeg,image/tiff,text/plain";

// OS metadata files (.DS_Store, ._foo) would only show up as preflight rejections.
const isHidden = (name: string) => name.startsWith(".");

function readEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

function entryFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

const isFileEntry = (entry: FileSystemEntry): entry is FileSystemFileEntry => entry.isFile;
const isDirectoryEntry = (entry: FileSystemEntry): entry is FileSystemDirectoryEntry => entry.isDirectory;

async function walk(entry: FileSystemEntry, out: PickedFile[]): Promise<void> {
  if (isHidden(entry.name)) return;
  if (isFileEntry(entry)) {
    out.push({ file: await entryFile(entry), path: entry.fullPath.replace(/^\/+/, "") });
    return;
  }
  if (isDirectoryEntry(entry)) {
    const reader = entry.createReader();
    // readEntries returns at most ~100 entries per call; keep reading until it returns none.
    for (let batch = await readEntries(reader); batch.length > 0; batch = await readEntries(reader)) {
      for (const child of batch) await walk(child, out);
    }
  }
}

export interface DroppedFiles {
  files: PickedFile[];
  fromFolder: boolean;
}

export async function filesFromDrop(transfer: DataTransfer): Promise<DroppedFiles> {
  // Entries must be taken synchronously: the DataTransfer is cleared once the handler yields.
  const entries = Array.from(transfer.items)
    .filter((item) => item.kind === "file")
    .map((item) => item.webkitGetAsEntry())
    .filter((entry): entry is FileSystemEntry => entry !== null);
  if (entries.length === 0) {
    return { files: Array.from(transfer.files).map((file) => ({ file, path: file.name })), fromFolder: false };
  }
  const files: PickedFile[] = [];
  for (const entry of entries) await walk(entry, files);
  return { files, fromFolder: entries.some((entry) => entry.isDirectory) };
}

export function filesFromInput(list: FileList | null): PickedFile[] {
  return Array.from(list ?? [])
    .filter((file) => !isHidden(file.name))
    .map((file) => ({ file, path: file.webkitRelativePath || file.name }));
}

export function chunk<T>(items: readonly T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}
