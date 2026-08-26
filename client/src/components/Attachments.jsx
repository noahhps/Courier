import { useEffect, useState } from "react";


import { useApi } from "../lib/api-context";
import { formatSize, isImage } from "../lib/files";

/**
 * The bytes of an attachment that lives on the server.
 *
 * The endpoint is authenticated, so the browser cannot be pointed at it with a
 * plain `src`: the token only travels in a header. Fetch it, wrap it in an
 * object URL, and let go of that URL when the message scrolls out of the tree
 * -- otherwise every session switch leaks another few megabytes.
 */
function useAttachmentUrl(attachment) {
  const api = useApi();
  const [url, setUrl] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // Already in hand: a file this client just sent still has its base64 from
    // the upload, so there is nothing to fetch.
    if (attachment.data) {
      setUrl(`data:${attachment.mime};base64,${attachment.data}`);
      return undefined;
    }
    if (!attachment.id || !api) return undefined;

    let objectUrl = null;
    let live = true;
    api
      .attachment(attachment.id)
      .then((blob) => {
        if (!live) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => live && setFailed(true));

    return () => {
      live = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, attachment.data, attachment.id, attachment.mime]);

  return { url, failed };
}

/** Full size, over the thread. Escape or a click anywhere dismisses it. */
function Lightbox({ url, name, onClose }) {
  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={name} onClick={onClose}>
      <img src={url} alt={name} />
      <span className="lightbox-name">{name}</span>
    </div>
  );
}

function AttachedImage({ attachment }) {
  const { url, failed } = useAttachmentUrl(attachment);
  const [open, setOpen] = useState(false);

  if (failed) return <FileChip attachment={attachment} note="could not be loaded" />;
  if (!url) return <div className="attachment-image placeholder" />;

  return (
    <>
      <button
        type="button"
        className="attachment-image"
        aria-label={`View ${attachment.name}`}
        onClick={() => setOpen(true)}
      >
        <img src={url} alt={attachment.name} />
      </button>
      {open ? <Lightbox url={url} name={attachment.name} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function FileChip({ attachment, note }) {
  return (
    <span className="file-chip">
      <span className="file-chip-name">{attachment.name}</span>
      <span className="file-chip-meta">
        {note || (attachment.size ? formatSize(attachment.size) : "")}
      </span>
    </span>
  );
}

/** What hangs off a sent message. */
export function MessageAttachments({ attachments }) {
  if (!attachments?.length) return null;

  return (
    <div className="attachments">
      {attachments.map((attachment, index) =>
        isImage(attachment) ? (
          <AttachedImage key={attachment.id || index} attachment={attachment} />
        ) : (
          <FileChip key={attachment.id || index} attachment={attachment} />
        ),
      )}
    </div>
  );
}

/** What sits above the composer before it is sent, and can still be removed. */
export function StagedAttachments({ items, onRemove }) {
  if (!items.length) return null;

  return (
    <ul className="staged">
      {items.map((item) => (
        <li key={item.key} className="staged-item">
          {item.preview ? (
            <img className="staged-thumb" src={item.preview} alt="" />
          ) : null}
          <span className="staged-name" title={item.file.name}>
            {item.file.name}
          </span>
          <span className="staged-size">{formatSize(item.file.size)}</span>
          <button
            type="button"
            className="staged-remove"
            aria-label={`Remove ${item.file.name}`}
            onClick={() => onRemove(item.key)}
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  );
}
