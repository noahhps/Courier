import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { StagedAttachments } from "./Attachments";
import { Icon } from "./Icon";


/**
 * The composer from artboard 1a: one rounded box, the text on its own line,
 * and a row of chips beneath it with the send button at the right.
 *
 * The reasoning control is a three-way chip group rather than the old slider.
 * The design has no sliders in it, and three named states read faster than a
 * track with an output label under it.
 */
/* The reasoning control, drawn from whatever the live model actually takes.
 *
 * Four shapes, because the families disagree about what "think harder" even
 * is -- an effort word, a switch, a token budget, or nothing. The server says
 * which on /status; nothing here knows a model name, so pulling a new model
 * changes the control without a client release. */
// What to draw when /status says nothing about thinking at all. That means an
// older server, which took `ThinkingLevel` and nothing else -- so the effort
// chips are its correct control, not a guess. Distinct from a server that
// answers "none", which is a real answer about a model that cannot reason and
// must draw nothing.
const LEGACY = {
  mode: "effort",
  options: ["low", "medium", "high"],
  default: "medium",
  label: "Effort",
};

function ThinkingControl({ control, value, onChange, disabled }) {
  const mode = control?.mode || "none";
  if (mode === "none") return null;

  if (mode === "effort") {
    return (
      <div className="effort" role="group" aria-label={control.label}>
        {(control.options || []).map((level) => (
          <button
            key={level}
            type="button"
            aria-pressed={value === level}
            disabled={disabled}
            onClick={() => onChange(level)}
          >
            {level}
          </button>
        ))}
      </div>
    );
  }

  if (mode === "switch") {
    return (
      <button
        type="button"
        className="chip"
        role="switch"
        aria-checked={value === true}
        data-on={value === true ? "true" : undefined}
        disabled={disabled}
        onClick={() => onChange(value === true ? false : true)}
      >
        {control.label}
      </button>
    );
  }

  // budget: a real range, because the value is continuous and the useful part
  // is how far along it sits, not which of three words it is.
  return (
    <label className="budget">
      <span className="mi">{control.label}</span>
      <input
        type="range"
        min={control.min}
        max={control.max}
        step={control.step}
        value={typeof value === "number" ? value : control.default}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <output className="mi">
        {Math.round((typeof value === "number" ? value : control.default) / 1024)}k
      </output>
    </label>
  );
}

export function Composer({
  disabled,
  focusToken,
  draft,
  sessionLabel,
  thinking,
  onSend,
}) {
  const [value, setValue] = useState("");
  // Whatever the current control's value is. Reset when the control changes
  // shape, because "medium" means nothing to a switch and `true` means nothing
  // to a budget -- carrying it across would send the model a value it cannot
  // read.
  const [effort, setEffort] = useState(null);
  // An absent descriptor is not the same as "none" -- see LEGACY above.
  const control = thinking || LEGACY;
  const mode = control.mode;
  useEffect(() => {
    setEffort(control.default);
    // Keyed on the mode, not the object: a re-fetch of /status hands back a
    // fresh object with identical contents, and depending on identity would
    // throw away the reader's choice each time one arrived.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);
  // Picked but not sent. Each carries its own key because two files can have
  // the same name, and an object URL for images so the thumbnail costs nothing
  // -- revoked on removal and on send, or the previews leak.
  const [staged, setStaged] = useState([]);
  const [dropping, setDropping] = useState(false);
  const form = useRef(null);
  const input = useRef(null);
  const picker = useRef(null);
  const dragDepth = useRef(0);
  const stagedRef = useRef(staged);
  stagedRef.current = staged;

  // Grow with the text, up to 40% of the viewport. The message list pads
  // itself by --composer-h, so the last turn is never behind the box -- which
  // means anything that changes the composer's height has to re-measure too.
  useLayoutEffect(() => {
    const node = input.current;
    node.style.height = "auto";
    node.style.height = Math.min(node.scrollHeight, window.innerHeight * 0.4) + "px";
    document.documentElement.style.setProperty(
      "--composer-h",
      form.current.offsetHeight + "px",
    );
  }, [value, staged]);

  // The layout effect above covers everything that changes the composer's
  // contents. This covers everything that changes its box for other reasons --
  // the drawer opening and narrowing it, the window resizing, a font landing --
  // any of which can rewrap the text and leave --composer-h stale, so the last
  // turn ends up behind the input.
  useEffect(() => {
    const node = form.current;
    const observer = new ResizeObserver(() => {
      document.documentElement.style.setProperty("--composer-h", node.offsetHeight + "px");
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // A starter was picked. `draft` is a fresh object every time, so choosing the
  // same one twice still fires -- comparing the string would swallow the second
  // press and look broken.
  //
  // The caret goes to the end rather than selecting the text: these prompts are
  // written to be typed into, and a selection would mean the next keystroke
  // wiped what was just inserted.
  useEffect(() => {
    if (!draft) return;
    setValue(draft.text);
    const node = input.current;
    node.focus();
    node.selectionStart = node.selectionEnd = draft.text.length;
  }, [draft]);

  // Starting a new conversation puts the caret in the box. Only then -- an
  // unprompted focus on a phone throws the keyboard up over the thread.
  useEffect(() => {
    if (focusToken) input.current.focus();
  }, [focusToken]);

  const stage = useCallback((incoming) => {
    const items = [...incoming].map((file) => ({
      key: `${file.name}:${file.size}:${file.lastModified}:${Math.random()}`,
      file,
      preview: file.type.startsWith("image/") ? URL.createObjectURL(file) : null,
    }));
    setStaged((prev) => [...prev, ...items]);
  }, []);

  const unstage = useCallback((key) => {
    setStaged((prev) => {
      const going = prev.find((item) => item.key === key);
      if (going?.preview) URL.revokeObjectURL(going.preview);
      return prev.filter((item) => item.key !== key);
    });
  }, []);

  // Anywhere on the window, not just over the composer -- but the composer is
  // what lights up, since that is where they land. Depth-counted because
  // dragenter/dragleave fire for every child element crossed on the way in.
  useEffect(() => {
    const enter = (event) => {
      if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
      dragDepth.current += 1;
      setDropping(true);
    };
    const leave = () => {
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (!dragDepth.current) setDropping(false);
    };
    const over = (event) => event.preventDefault();
    const drop = (event) => {
      event.preventDefault();
      dragDepth.current = 0;
      setDropping(false);
      if (event.dataTransfer?.files?.length) stage(event.dataTransfer.files);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  }, [stage]);

  // Previews outlive the component otherwise. Read through a ref so the
  // cleanup runs once, on unmount, rather than after every staging change.
  useEffect(
    () => () => stagedRef.current.forEach((i) => i.preview && URL.revokeObjectURL(i.preview)),
    [],
  );

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
    onSend(text, files, effort);
  };

  return (
    <form className="composer" ref={form} onSubmit={submit} data-dropping={dropping ? "" : undefined}>
      <div className="composer-box">
        <StagedAttachments items={staged} onRemove={unstage} />

        <textarea
          ref={input}
          rows="1"
          placeholder="Ask me. Task me."
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

        <div className="composer-row">
          <input
            ref={picker}
            type="file"
            multiple
            hidden
            onChange={(event) => {
              stage(event.target.files);
              // Cleared so picking the same file twice in a row still fires.
              event.target.value = "";
            }}
          />
          <button
            type="button"
            className="chip chip-icon"
            aria-label="Attach files"
            title="Attach files"
            disabled={disabled}
            onClick={() => picker.current.click()}
          >
            <Icon name="attachment" />
          </button>

          {sessionLabel ? <span className="chip">{sessionLabel}</span> : null}

          <div className="spacer" />

          <ThinkingControl
            control={control}
            value={effort}
            onChange={setEffort}
            disabled={disabled}
          />

          <button type="submit" className="send" aria-label="Send" disabled={disabled}>
            <Icon name="send" />
          </button>
        </div>
      </div>
    </form>
  );
}
