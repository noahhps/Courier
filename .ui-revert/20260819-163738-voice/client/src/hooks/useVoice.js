import { useCallback, useEffect, useRef, useState } from "react";

// What the browser will actually record in. Chrome gives WebM/Opus; iOS Safari
// only does MP4. Asked in order, first supported wins -- the server hands the
// bytes to a decoder that reads either.
const CONTAINERS = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];

// How often a partial pass is attempted while speech is still going. Long
// enough that a sentence has somewhere to land, short enough that words appear
// while you are still talking.
const INTERIM_MS = 2000;

// getUserMedia exists only in a secure context. Over plain HTTP on the tailnet
// -- how the phone reaches this -- navigator.mediaDevices is simply absent, so
// the button has to say why rather than throw when pressed.
export const voiceSupported = () =>
  typeof window !== "undefined" &&
  Boolean(navigator.mediaDevices?.getUserMedia) &&
  typeof window.MediaRecorder !== "undefined";

const pickContainer = () =>
  CONTAINERS.find((type) => window.MediaRecorder.isTypeSupported?.(type)) || "";

/**
 * Dictation that types as you speak.
 *
 * Every couple of seconds the audio so far is transcribed again from the
 * beginning and the result replaces what was written last, rather than the
 * tail being transcribed and appended. Two reasons: the chunks after the first
 * carry no container header and are not independently decodable, and a model
 * given the whole utterance revises its earlier guesses as the sentence
 * finishes -- so the text firms up as you talk instead of accumulating
 * mistakes it can never take back.
 *
 * The transcript is handed to the caller rather than sent. The sheet's own
 * hint is "speak, edit as you go".
 */
export function useVoice(api, { onStart, onText }) {
  const [state, setState] = useState("idle"); // idle | recording | transcribing
  const [error, setError] = useState("");

  const recorder = useRef(null);
  const stream = useRef(null);
  // Set when a stop is asked for before there is anything to stop. Granting
  // the microphone takes as long as the person takes to answer the prompt, and
  // a stop that arrives during that wait would otherwise be dropped -- leaving
  // a recorder that starts afterwards with nothing able to end it.
  const stopWanted = useRef(false);
  // Re-entry guard. `state` is still "idle" for as long as the permission
  // prompt is open, so a second press would sail past the check below and
  // open a second stream, orphaning the first with its indicator still lit.
  const starting = useRef(false);
  const chunks = useRef([]);
  const live = useRef(true);
  // The level meter taps the same stream the recorder is using. Kept as refs
  // rather than state: the bars are redrawn every animation frame, and routing
  // that through React would re-render the whole composer sixty times a second
  // to move a few pixels.
  const audio = useRef(null);
  const analyser = useRef(null);
  // Interim passes can finish out of order, and a stale one would undo the
  // words after it. Only a newer result is ever allowed to land.
  const seq = useRef(0);
  const applied = useRef(0);
  const inFlight = useRef(false);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      // Leaving tracks open keeps the browser's recording indicator lit long
      // after the component is gone.
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
      analyser.current = null;
      audio.current?.close?.();
      audio.current = null;
    };
  }, []);

  const openMeter = useCallback((mediaStream) => {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      ctx.resume?.();
      const node = ctx.createAnalyser();
      node.fftSize = 64; // 32 bins, which is plenty to drive a handful of bars
      node.smoothingTimeConstant = 0.7;
      // Connected to the analyser and nowhere else. Wiring it to the
      // destination would play your own voice back at you through the speakers.
      ctx.createMediaStreamSource(mediaStream).connect(node);
      audio.current = ctx;
      analyser.current = node;
    } catch {
      // A meter is not worth failing a recording over.
    }
  }, []);

  const closeMeter = useCallback(() => {
    analyser.current = null;
    audio.current?.close?.();
    audio.current = null;
  }, []);

  const pass = useCallback(
    async (mimeType, { final }) => {
      if (!chunks.current.length) return;
      // While speaking, skip a pass rather than queue it: on CPU the model is
      // barely faster than real time, and a backlog would still be catching up
      // long after you stopped.
      if (!final && inFlight.current) return;

      const ticket = ++seq.current;
      const blob = new Blob(chunks.current, { type: mimeType || "audio/webm" });
      inFlight.current = true;
      try {
        const { text } = await api.transcribe(await toBase64(blob), blob.type);
        if (!live.current || ticket <= applied.current) return;
        applied.current = ticket;
        if (text?.trim()) onText(text.trim());
      } catch (failure) {
        // A partial pass failing is not worth interrupting speech over; only
        // the final one gets to report.
        if (final && live.current) setError(failure.message || "Could not transcribe that.");
      } finally {
        inFlight.current = false;
      }
    },
    [api, onText],
  );

  const start = useCallback(async () => {
    if (!voiceSupported() || state !== "idle" || starting.current) return;
    setError("");
    stopWanted.current = false;
    starting.current = true;
    try {
      try {
        stream.current = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch {
        // Denied, or no input device. Either way it is not retryable by us.
        setError("No microphone available.");
        return;
      }

      // Stopped while the prompt was open. Hand the microphone straight back
      // rather than opening a recording nobody asked to keep.
      if (stopWanted.current || !live.current) {
        stream.current?.getTracks().forEach((track) => track.stop());
        stream.current = null;
        return;
      }

      openMeter(stream.current);

      const mimeType = pickContainer();
      chunks.current = [];
      seq.current = 0;
      applied.current = 0;
      inFlight.current = false;
      onStart?.();

      const media = new window.MediaRecorder(stream.current, mimeType ? { mimeType } : {});
      recorder.current = media;

      media.ondataavailable = (event) => {
        if (!event.data?.size) return;
        chunks.current.push(event.data);
        // Fires on the timeslice while recording, and once more on stop -- the
        // stop handler owns the final pass, so only run partials here.
        if (media.state === "recording") pass(media.mimeType || mimeType, { final: false });
      };

      media.onstop = async () => {
        stream.current?.getTracks().forEach((track) => track.stop());
        stream.current = null;
        recorder.current = null;
        closeMeter();

        // A tap rather than a hold: nothing was said, so nothing is sent.
        if (!chunks.current.length) {
          if (live.current) setState("idle");
          return;
        }

        if (live.current) setState("transcribing");
        await pass(media.mimeType || mimeType, { final: true });
        chunks.current = [];
        if (live.current) setState("idle");
      };

      // A timeslice is what makes ondataavailable fire during the recording
      // rather than only at the end.
      media.start(INTERIM_MS);
      setState("recording");
    } finally {
      starting.current = false;
    }
  }, [closeMeter, onStart, openMeter, pass, state]);

  const stop = useCallback(() => {
    stopWanted.current = true;
    if (recorder.current?.state === "recording") recorder.current.stop();
  }, []);

  // Click to start, click again to stop -- which is how the sheet draws it, and
  // the only shape that survives a desktop mouse. Holding the button meant the
  // pointer drifting a few pixels off it cancelled the recording, and releasing
  // outside it never ended one.
  const toggle = useCallback(() => {
    if (state === "recording") stop();
    else start();
  }, [state, start, stop]);

  return { supported: voiceSupported(), state, error, start, stop, toggle, analyser };
}

// Same FileReader route as lib/files.js: it chunks a multi-megabyte blob for
// us instead of blowing the argument limit on String.fromCharCode.
function toBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read the recording."));
    reader.onload = () => {
      const result = String(reader.result);
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.readAsDataURL(blob);
  });
}
