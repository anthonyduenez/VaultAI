import { FilePlus2, FolderUp, Upload } from "lucide-react";
import { type DragEvent, useEffect, useRef, useState } from "react";
import type { PickedFile, UploadSource } from "../api/client";
import { ACCEPT, filesFromDrop, filesFromInput } from "../lib/files";

interface DropZoneProps {
  onFiles: (files: PickedFile[], source: UploadSource) => void;
  disabled?: boolean;
}

export function DropZone({ onFiles, disabled = false }: DropZoneProps) {
  const [over, setOver] = useState(false);
  const [reading, setReading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const depth = useRef(0);

  useEffect(() => {
    // React's DOM typings do not include webkitdirectory, so it is set as a plain attribute.
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, []);

  const onDragEnter = (event: DragEvent) => {
    event.preventDefault();
    depth.current += 1;
    setOver(true);
  };
  const onDragLeave = (event: DragEvent) => {
    event.preventDefault();
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setOver(false);
  };
  const onDrop = async (event: DragEvent) => {
    event.preventDefault();
    depth.current = 0;
    setOver(false);
    if (disabled) return;
    setReading(true);
    try {
      const { files, fromFolder } = await filesFromDrop(event.dataTransfer);
      if (files.length > 0) onFiles(files, fromFolder ? "folder" : "upload");
    } finally {
      setReading(false);
    }
  };

  return (
    <div
      className="dropzone"
      data-over={over || undefined}
      data-disabled={disabled || undefined}
      onDragEnter={onDragEnter}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={onDragLeave}
      onDrop={(e) => void onDrop(e)}
    >
      <Upload size={32} strokeWidth={1.5} aria-hidden="true" />
      <p className="dropzone-title">{reading ? "Reading folder…" : "Drop files or whole folders here"}</p>
      <p className="dropzone-hint">PDF, PNG, JPEG, TIFF, and TXT · subfolders are included · files stay on this device</p>
      <div className="row gap-1 center">
        <button type="button" className="btn" disabled={disabled} onClick={() => fileInput.current?.click()}>
          <FilePlus2 size={15} aria-hidden="true" /> Select files
        </button>
        <button type="button" className="btn" disabled={disabled} onClick={() => folderInput.current?.click()}>
          <FolderUp size={15} aria-hidden="true" /> Select folder
        </button>
      </div>
      <input
        ref={fileInput}
        type="file"
        multiple
        accept={ACCEPT}
        hidden
        aria-label="Select files"
        onChange={(e) => {
          const files = filesFromInput(e.target.files);
          e.target.value = "";
          if (files.length > 0) onFiles(files, "upload");
        }}
      />
      <input
        ref={folderInput}
        type="file"
        multiple
        hidden
        aria-label="Select folder"
        onChange={(e) => {
          const files = filesFromInput(e.target.files);
          e.target.value = "";
          if (files.length > 0) onFiles(files, "folder");
        }}
      />
    </div>
  );
}
