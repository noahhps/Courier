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
  onStop,
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
  const [popping, setPopping] = useState(false);
  const form = useRef(null);
  const input = useRef(null);
  const picker = useRef(null);
  const dragDepth = useRef(0);
  const stagedRef = useRef(staged);
  stagedRef.current = staged;

  // Grow with the text, up to 40% of the viewport.
  //
  // The ceiling has a floor under it, which is not the tautology it sounds
  // like. A window that has not been shown yet reports an innerHeight of 0 --
  // the desktop shell now creates its window hidden and reveals it once the
  // server answers, and a background tab can do the same -- and 40% of nothing
  // is nothing, so `Math.min` collapsed the box to a sliver with the
  // placeholder clipped in half. It then stayed that way, because this only
  // re-runs when the text or the attachments change: nobody types into a
  // composer they cannot see, so nothing ever asked it to grow back.
  const autosize = useCallback(() => {
    const node = input.current;
    if (!node) return;
    node.style.height = "auto";
    // 320px is roughly eight lines -- a sane box to be handed if the viewport
    // will not say how tall it is yet. The real value arrives on the next
    // resize and replaces it.
    const ceiling = (window.innerHeight || 800) * 0.4 || 320;
    node.style.height = Math.min(node.scrollHeight, ceiling) + "px";

    // How much of the composer is still on screen once it has withdrawn.
    //
    // On a thread the composer floats over the bottom of the sheet, so the
    // thread has to keep clear of it -- but only of the part that is actually
    // there. Reserving the whole box would leave a band of nothing between the
    // last turn and a composer that has slid most of the way off the edge.
    //
    // `--tuck` is read back out of the stylesheet rather than repeated here,
    // so how far it withdraws stays a single decision made in one place.
    const box = form.current?.querySelector(".composer-box");
    if (form.current && box) {
      const tuck = parseFloat(getComputedStyle(box).getPropertyValue("--tuck")) / 100 || 0;
      const peek = form.current.offsetHeight - tuck * box.offsetHeight;
      document.documentElement.style.setProperty(
        "--composer-peek",
        Math.max(0, Math.round(peek)) + "px",
      );
    }
  }, []);

  useLayoutEffect(autosize, [autosize, value, staged]);

  // And again whenever the window itself changes size, because the ceiling is
  // a fraction of it. Listened for on `window` rather than folded into the
  // ResizeObserver below: that one watches the composer, and resizing the
  // composer from inside its own observer is how a resize loop starts.
  useEffect(() => {
    window.addEventListener("resize", autosize);
    return () => window.removeEventListener("resize", autosize);
  }, [autosize]);

  // The layout effect above covers everything that changes the composer's
  // contents. This covers everything that changes its box for other reasons --
  // the drawer opening and narrowing it, the window resizing, a font landing --
  // any of which can rewrap the text and leave --composer-peek stale, so the
  // last turn ends up behind the input.
  useEffect(() => {
    const node = form.current;
    const observer = new ResizeObserver(() => {
      const box = node.querySelector(".composer-box");
      if (!box) return;
      const tuck = parseFloat(getComputedStyle(box).getPropertyValue("--tuck")) / 100 || 0;
      const peek = node.offsetHeight - tuck * box.offsetHeight;
      document.documentElement.style.setProperty(
        "--composer-peek",
        Math.max(0, Math.round(peek)) + "px",
      );
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // The composer withdraws until you reach for it.
  //
  // Idle it sits half below the edge, dimmed. `--near` is how close the cursor
  // is -- 0 far away, 1 touching it or focused -- and the stylesheet turns that
  // into height, opacity and the glow. See the `.composer` block in styles.css.
  //
  // Deliberately outside React state: this updates on every pointer move, and
  // a setState there would re-render the composer (and rewrap its textarea)
  // sixty times a second. Writing one custom property straight to the node
  // costs a style recalc on a single element and nothing else.
  useLayoutEffect(() => {
    const node = form.current;
    if (!node) return undefined;
    const box = node.querySelector(".composer-box");

    // How far away the cursor starts having an effect, and how sharply it
    // ramps once it does. These are the two dials for the feel of the thing.
    //
    // REACH is the outer edge: beyond this the composer is fully tucked and
    // nothing is happening at all. ONSET bends the response inside that range.
    // At 1 it is linear, and the box starts drifting up the instant you cross
    // the boundary -- which read as the composer reacting to the cursor merely
    // being on the same screen. Above 1 it stays down until the cursor is
    // genuinely close and then comes up quickly: at ONSET 2, halfway through
    // the reach has moved it only a quarter of the way.
    //
    // The pair matters more than either number. Shrinking REACH alone makes
    // the onset a hard edge you can see snapping on; the curve is what keeps
    // a late start from also being an abrupt one.
    const REACH = 130;
    const ONSET = 2;

    // A cursor is the whole mechanism, so a device without one keeps the
    // composer present permanently -- on the phone there is nothing to
    // "approach" with and no way to discover a box that is half off screen.
    // Reduced motion opts out for the same reason it opts out of the rail:
    // this is motion, and following the pointer is the most of it.
    const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
    const enabled = () => fine.matches && !calm.matches;

    let frame = 0;
    let latest = null;
    // Set the moment the thread is scrolled, cleared on the next pointer move.
    // Reading is the one activity the composer is most in the way of, and the
    // cursor is usually still sitting over the box from whatever was clicked
    // last -- so without this, scrolling back through an answer happens with
    // the composer parked over the end of it.
    let reading = false;

    const set = (near) => node.style.setProperty("--near", near.toFixed(3));

    const measure = () => {
      frame = 0;
      if (!enabled()) return set(1);
      // Focus outranks everything: while you are typing, the box stays put --
      // including through the autoscroll that a streaming reply causes, which
      // would otherwise pull the composer out from under the caret.
      if (node.contains(document.activeElement)) return set(1);
      if (reading) return set(0);
      if (!latest) return set(0);

      // Distance to the nearest edge of the box, which is 0 anywhere inside
      // it. Using the centre instead would mean a wide composer felt far away
      // at its own left edge.
      const rect = box.getBoundingClientRect();
      const dx = Math.max(rect.left - latest.x, 0, latest.x - rect.right);
      const dy = Math.max(rect.top - latest.y, 0, latest.y - rect.bottom);
      const closeness = Math.max(0, Math.min(1, 1 - Math.hypot(dx, dy) / REACH));
      set(Math.pow(closeness, ONSET));
    };

    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(measure);
    };

    const onMove = (event) => {
      latest = { x: event.clientX, y: event.clientY };
      // Moving the pointer is how you ask for it back. Reaching toward the
      // composer is the same gesture as before -- the scroll only suppresses
      // it until you next show an interest.
      reading = false;
      schedule();
    };

    // Scroll does not bubble, so this is a capture-phase listener on the
    // document rather than one bound to the thread: the composer has no
    // reference to the scroller, and this way it also covers anything else
    // that scrolls underneath it.
    const onScroll = () => {
      if (reading) return; // already down; nothing to recompute
      reading = true;
      schedule();
    };
    // The pointer leaving the window entirely reads as "gone", not as "last
    // seen at the edge" -- otherwise dragging off the top of the screen leaves
    // the composer frozen at whatever it was.
    const onLeave = () => {
      latest = null;
      schedule();
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    document.addEventListener("pointerleave", onLeave);
    node.addEventListener("focusin", schedule);
    node.addEventListener("focusout", schedule);
    fine.addEventListener("change", schedule);
    calm.addEventListener("change", schedule);

    measure();
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("scroll", onScroll, { capture: true });
      document.removeEventListener("pointerleave", onLeave);
      node.removeEventListener("focusin", schedule);
      node.removeEventListener("focusout", schedule);
      fine.removeEventListener("change", schedule);
      calm.removeEventListener("change", schedule);
      // Left present rather than tucked: if this component is going away the
      // next thing to mount should not inherit a half-hidden composer.
      node.style.removeProperty("--near");
    };
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

  // The older proximity effect used to live here. It wrote `--near` to
  // document.documentElement, which `.composer` shadows with a declaration of
  // its own -- so every value it set was discarded before it could reach the
  // box, including the one that was supposed to reveal it on focus. Removed
  // rather than repaired: the effect above already does this, on the node
  // whose value actually wins, and two writers for one property is how they
  // drift apart again.


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

  // The flourish, for a composer that was actually tucked away.
  //
  // Strictly transient: the class goes on, the animation plays once, and 600ms
  // later it comes off and `--near` is back in sole charge of where the box
  // sits. It used to also latch a second class holding the animation paused on
  // its final frame, which pinned the composer open for the rest of the
  // session -- a filling animation outranks the proximity transform, so
  // nothing could lower it again.
  //
  // The guard below is the other half of that. Popping is a movement from
  // hidden to present, so if the box is already present there is no movement
  // to make and replaying it just jerks something that was sitting still --
  // which is what happens on every click once the latch is gone.
  //
  // Worth knowing this makes the flourish nearly unreachable: clicking the box
  // requires the pointer to be on it, and a pointer on it means proximity has
  // already raised it. A click is the wrong trigger for this animation.
  const handleComposerClick = () => {
    if (popping) return;
    // Absent means the proximity effect never ran -- a touch screen, or
    // reduced motion -- where the composer is permanently present and there is
    // likewise nothing to pop out of.
    const near = parseFloat(form.current?.style.getPropertyValue("--near"));
    if (!(near < 0.9)) return;
    setPopping(true);
    setTimeout(() => setPopping(false), 600);
  };

  return (
    <form
      className="composer"
      ref={form}
      onSubmit={submit}
      onClick={handleComposerClick}
      data-dropping={dropping ? "" : undefined}
      data-popping={popping ? "" : undefined}
    >
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

          {/* While a turn is streaming this is the way out of it, not a
              greyed-out arrow. A disabled control says "not now"; the thing
              the reader actually wants at that moment is to call the answer
              off, and on local hardware a wrong one can run for a while.
              `type="button"` so it cannot submit the form it lives in. */}
          {disabled ? (
            <button
              type="button"
              className="send"
              aria-label="Stop generating"
              onClick={onStop}
            >
              <Icon name="stop" />
            </button>
          ) : (
            <button type="submit" className="send" aria-label="Send">
              <Icon name="send" />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
