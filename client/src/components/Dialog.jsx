/* Asking a question and waiting for the answer, without the platform's help.
 *
 * `prompt`, `confirm` and `alert` are not available here. WKWebView only
 * provides them when the host implements the UI delegate that draws them, and
 * the Tauri shell does not -- so every one of them was a silent no-op in the
 * desktop app: "+ New project" appeared to do nothing at all, because the
 * prompt that should have asked for a name never opened and returned
 * undefined.
 *
 * Replacing them rather than reaching for Tauri's dialog plugin, for two
 * reasons. The plugin has ask/confirm/message but no text input, so it could
 * not have answered the project-name question at all. And a dialog drawn here
 * is one implementation for every host -- the phone gets it too, where iOS
 * Safari's own prompt is a grey slab with none of the app's typography.
 *
 * The API is promise-shaped so a call site reads almost the way it did:
 *
 *     if (await confirm("Delete this conversation?")) onDelete(id);
 *     const name = await ask("Name for the new project");
 *
 * The app draws no other modals on purpose -- recall sits in the margin, not
 * over the top. This is the exception, because a question that blocks what
 * happens next has nowhere else to go.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

const DialogContext = createContext(null);

/** The three shapes. `notify` is the one that takes no answer. */
const ASK = "ask";
const CONFIRM = "confirm";
const NOTIFY = "notify";

export function DialogProvider({ children }) {
  // One at a time. A second request while one is open would either stack --
  // which nothing here needs -- or silently drop an unresolved promise.
  const [request, setRequest] = useState(null);
  const [draft, setDraft] = useState("");
  const resolver = useRef(null);
  const inputRef = useRef(null);
  const confirmRef = useRef(null);

  const open = useCallback((next) => {
    // Any request still open is answered negatively rather than abandoned. An
    // unresolved promise would leave its caller awaiting forever.
    if (resolver.current) resolver.current(next.kind === ASK ? null : false);
    setDraft(next.value || "");
    setRequest(next);
    return new Promise((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  const settle = useCallback((answer) => {
    const resolve = resolver.current;
    resolver.current = null;
    setRequest(null);
    setDraft("");
    if (resolve) resolve(answer);
  }, []);

  const api = useMemo(
    () => ({
      /** Ask for a line of text. Resolves to the trimmed string, or null. */
      ask: (message, options = {}) =>
        open({ kind: ASK, message, ...options }),
      /** Ask a yes/no question. Resolves true only on the affirmative. */
      confirm: (message, options = {}) =>
        open({ kind: CONFIRM, message, ...options }),
      /** Say something. Resolves when it has been dismissed. */
      notify: (message, options = {}) =>
        open({ kind: NOTIFY, message, ...options }),
    }),
    [open],
  );

  // Focus follows the dialog: the field when there is one to type in, the
  // confirming button otherwise. Without this, Escape and Enter would go to
  // whatever was focused behind the dialog.
  useEffect(() => {
    if (!request) return;
    const target = request.kind === ASK ? inputRef.current : confirmRef.current;
    target?.focus();
    if (request.kind === ASK) inputRef.current?.select();
  }, [request]);

  useEffect(() => {
    if (!request) return;
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        settle(request.kind === ASK ? null : false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [request, settle]);

  const submit = (event) => {
    event.preventDefault();
    if (!request) return;
    if (request.kind === ASK) {
      const value = draft.trim();
      // An empty answer is a cancel. Every caller checked for that anyway,
      // and it saves each of them repeating the trim.
      settle(value || null);
    } else {
      settle(true);
    }
  };

  const cancel = () => settle(request?.kind === ASK ? null : false);

  return (
    <DialogContext.Provider value={api}>
      {children}
      {request ? (
        <div
          className="dlg-scrim"
          // A click outside is a cancel, but only when it is the scrim itself
          // and not a click inside the card that bubbled up to it.
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) cancel();
          }}
        >
          <form
            className="dlg"
            role="dialog"
            aria-modal="true"
            aria-label={request.title || request.message}
            onSubmit={submit}
          >
            {request.title ? <h2 className="dlg-title">{request.title}</h2> : null}
            <p className="dlg-message">{request.message}</p>

            {request.kind === ASK ? (
              <input
                ref={inputRef}
                type="text"
                className="dlg-input"
                value={draft}
                placeholder={request.placeholder || ""}
                spellCheck="false"
                autoComplete="off"
                onChange={(event) => setDraft(event.target.value)}
              />
            ) : null}

            <div className="dlg-actions">
              {request.kind === NOTIFY ? null : (
                <button type="button" className="btn" onClick={cancel}>
                  {request.cancelLabel || "Cancel"}
                </button>
              )}
              <button
                ref={confirmRef}
                type="submit"
                className="btnp"
                data-destructive={request.destructive ? "" : undefined}
              >
                {request.confirmLabel ||
                  (request.kind === NOTIFY ? "OK" : request.kind === ASK ? "Save" : "Confirm")}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </DialogContext.Provider>
  );
}

/**
 * The three askers.
 *
 * Throws when used outside the provider rather than returning a no-op, because
 * a silently absent dialog is precisely the bug this file exists to fix.
 */
export function useDialog() {
  const context = useContext(DialogContext);
  if (!context) {
    throw new Error("useDialog must be used inside <DialogProvider>");
  }
  return context;
}
