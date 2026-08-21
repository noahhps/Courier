import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { useVoice } from "../hooks/useVoice";
import { useApi } from "../lib/api-context";
import { StagedAttachments } from "./Attachments";
import { Icon } from "./Icon";

let stagedKey = 0;

export function Composer({ disabled, focusToken, defaultThinking, onSend }) {
  const [value, setValue] = useState("");
  const [staged, setStaged] = useState([]);
  const [dropping, setDropping] = useState(false);
  // Starts where the server is configured to start it, then it is the
  // reader's to change for the rest of the session.
  const [thinkingOn, setThinkingOn] = useState(Boolean(defaultThinking));
  const form = useRef(null);
  const input = useRef(null);
  const picker = useRef(null);

  // Dictation types into the box as you speak. Each pass re-transcribes the
  // whole utterance, so the dictated span is rewritten rather than appended
  // to -- anchored to whatever was in the box when the mic went down, which is
  // what keeps already-typed text safe.
  //
  // Declared up here because the layout effect below measures a composer whose
  // height includes the dictation hint.
  const dictationBase = useRef("");
  const valueRef = useRef("");
  valueRef.current = value;

  const beginDictation = useCallback(() => {
    dictationBase.current = valueRef.current;
  }, []);

  const writeSpoken = useCallback((text) => {
    const base = dictationBase.current;
    setValue(base ? base.replace(/\s*$/, " ") + text : text);
  }, []);

  const voice = useVoice(useApi(), { onStart: beginDictation, onText: writeSpoken });

  // Grow with the text, up to 40% of the viewport. The message list pads
  // itself by --composer-h, so the last bubble is never behind the box --
  // which means anything else that changes the composer's height has to
  // re-measure too: staged files, and the dictation hint.
  useLayoutEffect(() => {
    const node = input.current;
    node.style.height = "auto";
    node.style.height = Math.min(node.scrollHeight, window.innerHeight * 0.4) + "px";
    document.documentElement.style.setProperty(
      "--composer-h",
      form.current.offsetHeight + "px",
    );
  }, [value, staged, voice.state, voice.error, dropping]);

  // Starting a new conversation puts the caret in the box. Only then -- an
  // unprompted focus on a phone throws the keyboard up over the thread.
  useEffect(() => {
    if (focusToken) input.current.focus();
  }, [focusToken]);

  // Thumbnails for staged images are object URLs, which have to be handed back
  // or they hold their file in memory for the life of the page.
  useEffect(() => {
    return () => staged.forEach((item) => item.preview && URL.revokeObjectURL(item.preview));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stage = (fileList) => {
    // Copied out of the FileList before anything else happens. It is a live
    // view of the input, and the handler below clears the input on the very
    // next line -- read inside the updater, which React runs later, it would
    // be empty by then and the files would vanish.
    const added = [...fileList].map((file) => ({
      key: ++stagedKey,
      file,
      preview: file.type.startsWith("image/") ? URL.createObjectURL(file) : null,
    }));
    setStaged((prev) => [...prev, ...added]);
  };

  // Files dropped anywhere on the window land here. The whole window rather
  // than the textarea alone: the composer is a thin strip and aiming at it
  // with a dragged file is a needless test of the wrist. The strip is what
  // lights up, so it is still obvious where they are going.
  useEffect(() => {
    // dragenter/dragleave fire for every child element crossed, so a plain
    // boolean flickers. Counting entries against leaves does not.
    let depth = 0;
    const carriesFiles = (event) =>
      Array.from(event.dataTransfer?.types || []).includes("Files");

    const onEnter = (event) => {
      if (!carriesFiles(event)) return;
      depth += 1;
      setDropping(true);
    };
    const onOver = (event) => {
      // Without this the browser navigates to the file and the page is gone.
      if (carriesFiles(event)) event.preventDefault();
    };
    const onLeave = (event) => {
      if (!carriesFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (!depth) setDropping(false);
    };
    const onDrop = (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth = 0;
      setDropping(false);
      if (event.dataTransfer.files?.length) stage(event.dataTransfer.files);
    };

    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const unstage = (key) => {
    setStaged((prev) => {
      const going = prev.find((item) => item.key === key);
      if (going?.preview) URL.revokeObjectURL(going.preview);
      return prev.filter((item) => item.key !== key);
    });
  };

  // Vision and reasoning are mutually exclusive on the local runner; see the
  // note in providers/ollama.py.
  const blocksThinking = staged.some((item) => item.file.type.startsWith("image/"));


  // One line, one meaning at a time -- and only when there is something to
  // say. A permanent hint under the box is furniture.
  const hint = dropping
    ? "drop to attach"
    : voice.error
      ? voice.error
      : voice.state === "recording"
        ? "listening — speak, edit as you go"
        : voice.state === "transcribing"
          ? "transcribing…"
          : "";

  const submit = (event) => {
    event.preventDefault();
    // The button is disabled while a turn streams, but Enter still submits --
    // and clearing the box for a send that gets refused loses what was typed.
    if (disabled) return;
    if (!value.trim() && !staged.length) return;

    const text = value;
    const files = staged.map((item) => item.file);
    staged.forEach((item) => item.preview && URL.revokeObjectURL(item.preview));
    setValue("");
    setStaged([]);
    onSend(text, files, thinkingOn);
  };

  return (
    <form
      className="composer"
      ref={form}
      onSubmit={submit}
      data-dropping={dropping ? "true" : undefined}
    >
      <StagedAttachments items={staged} onRemove={unstage} />

      <div className="composer-row">
        {/* Not a toggle -- it opens the picker. It only *looks* switched on,
            via data-active, to say that something is staged; announcing it as
            pressed would tell a screen reader it latches, which it doesn't. */}
        <button
          type="button"
          className="icon-btn"
          aria-label="Attach files"
          data-active={staged.length ? "true" : undefined}
          disabled={disabled}
          onClick={() => picker.current.click()}
        >
          <Icon name="attachment" badge="plus" />
        </button>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            stage(event.target.files);
            // Cleared so that picking the same file twice in a row still
            // counts as a change and fires this handler again.
            event.target.value = "";
          }}
        />

        {/* This one does latch, so it says so. Switched off and greyed while
            an image is staged, because the server suppresses reasoning on a
            turn carrying pictures -- offering the choice there would be a lie. */}
        <button
          type="button"
          className="icon-btn"
          aria-label={blocksThinking ? "Thinking (unavailable with images)" : "Thinking"}
          title={
            blocksThinking
              ? "Reasoning is turned off for turns with images -- the model stops seeing them."
              : "Reason before answering"
          }
          aria-pressed={thinkingOn && !blocksThinking}
          disabled={disabled || blocksThinking}
          onClick={() => setThinkingOn((on) => !on)}
        >
          <Icon name="thinking" />
        </button>

        <textarea
          ref={input}
          rows="1"
          placeholder="Message"
          autoComplete="off"
          autoCapitalize="sentences"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends on a real keyboard; on a phone it should insert a
            // newline -- there is nowhere else to put one.
            const isTouch = window.matchMedia("(pointer: coarse)").matches;
            if (event.key === "Enter" && !event.shiftKey && !isTouch) {
              event.preventDefault();
              form.current.requestSubmit();
            }
          }}
        />

        {/* Between the box and Send, as the sheet has it. Not aria-pressed:
            it is push-to-talk, and the state it reports is what the recorder
            is doing rather than a setting that latches. */}
        <button
          type="button"
          className="mic"
          data-state={voice.state}
          aria-label={voice.supported ? "Dictate" : "Dictation unavailable"}
          title={
            voice.supported
              ? "Hold to dictate"
              : "Dictation needs a microphone and a secure (https) connection."
          }
          disabled={disabled || !voice.supported || voice.state === "transcribing"}
          onPointerDown={voice.start}
          onPointerUp={voice.stop}
          onPointerLeave={voice.stop}
          onPointerCancel={voice.stop}
        >
          <Icon name="mic" />
        </button>

        <button type="submit" className="send" aria-label="Send" disabled={disabled}>
          <Icon name="send" />
        </button>
      </div>

      {hint ? (
        <div className="composer-hint" role="status">
          {hint}
        </div>
      ) : null}
    </form>
  );
}
