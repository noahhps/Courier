# UI snapshots

Each directory here is the client's look at a point in time, taken immediately
before a redesign. Nothing else in the project reads from this folder — it
exists so a restyle can be undone without unpicking it by hand.

`LATEST` names the most recent snapshot.

## Restoring

From the repository root, with `<stamp>` being the directory name:

```bash
cp -r .ui-revert/<stamp>/client/src/styles.css client/src/styles.css
cp -r .ui-revert/<stamp>/client/index.html client/index.html
cp -r .ui-revert/<stamp>/client/public/manifest.webmanifest client/public/manifest.webmanifest
rm -rf client/public/fonts && cp -r .ui-revert/<stamp>/client/public/fonts client/public/fonts
cp .ui-revert/<stamp>/client/src/*.jsx client/src/components/
cd client && npm run build
```

The components are snapshotted whole, so restoring them also reverts any
behaviour that shipped alongside the look. If you only want the appearance back,
copy `styles.css`, `index.html`, `manifest.webmanifest` and `fonts/` and leave
the `.jsx` files alone.

## Snapshots

- `20260816-023318-yiqi-paper` — the Yiqi Sheet v1 look: warm paper `#FAF9F6`,
  ink `#14161A`, blue `#0000CC`, Archivo + IBM Plex Mono, rounded geometry,
  light and dark themes. Taken before the v2 "terminal dress" redesign.

  **Incomplete.** It does not contain `client/public/attachment.svg`,
  `thinking.svg` or `rag.svg`, which were deleted during the redesign and are
  not recoverable from here. Restoring this snapshot brings back the paper look
  but leaves those two buttons on the inline fallback glyphs now in `Icon.jsx`.

- `20260819-163738-voice` — the dictation feature as it stood before removal:
  `useVoice.js`, `MicLevels.jsx`, `transcribe.py` (faster-whisper), the
  `/api/transcribe` endpoint, the mic button and its level meter. Removed on
  request; restore by copying these back and re-adding `faster-whisper` to
  `server/pyproject.toml`.

- `20260820-161938-backend-files-thinking` — the server-side implementation of
  attachments and the thinking pass, before they were removed so they could be
  rewritten by hand. Includes `attachments.py` (validation, image
  normalisation), `extract.py` (PDF/Office text), and the attachment and
  `think` paths through `api.py`, `orchestrator.py`, `store.py` and the
  providers. The client was left untouched and still sends both.
