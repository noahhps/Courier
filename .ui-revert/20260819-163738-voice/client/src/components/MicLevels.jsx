import { useEffect, useRef } from "react";

// Seven felt right: enough to read as a meter, few enough to stay a detail.
const BARS = 7;

// Bin 0 is DC and room rumble, and at fftSize 64 each bin spans several hundred
// hertz -- so the bins above about 14 are near-silent for speech and would draw
// a row of dead bars. Taking a pair of bins per bar over that range keeps every
// bar carrying something.
const FIRST_BIN = 1;
const BINS_PER_BAR = 2;

// A floor, so silence reads as "listening" rather than "broken".
const REST = 0.08;

/**
 * A live level meter for the microphone.
 *
 * The bars are written to directly rather than through state: this runs on
 * every animation frame, and putting it through React would re-render the
 * whole composer sixty times a second to move a few pixels.
 */
export function MicLevels({ analyser, active }) {
  const bars = useRef([]);

  useEffect(() => {
    const node = active ? analyser?.current : null;
    if (!node) {
      bars.current.forEach((bar) => bar && (bar.style.transform = `scaleY(${REST})`));
      return undefined;
    }

    const spectrum = new Uint8Array(node.frequencyBinCount);
    let frame = 0;

    const draw = () => {
      node.getByteFrequencyData(spectrum);
      for (let i = 0; i < BARS; i += 1) {
        const from = FIRST_BIN + i * BINS_PER_BAR;
        let sum = 0;
        for (let bin = from; bin < from + BINS_PER_BAR; bin += 1) sum += spectrum[bin] || 0;
        // 1.6 is gain: ordinary speech sits well below the top of the range,
        // and a meter that never leaves the bottom third tells you nothing.
        const level = Math.min(1, (sum / BINS_PER_BAR / 255) * 1.6);
        const bar = bars.current[i];
        if (bar) bar.style.transform = `scaleY(${Math.max(REST, level)})`;
      }
      frame = requestAnimationFrame(draw);
    };

    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [active, analyser]);

  // No meter is better than a dead one: if the audio context could not be
  // opened, the hint text stands on its own.
  if (!analyser?.current) return null;

  return (
    <span className="levels" aria-hidden="true">
      {Array.from({ length: BARS }, (_, i) => (
        <span
          key={i}
          className="level"
          ref={(node) => {
            bars.current[i] = node;
          }}
        />
      ))}
    </span>
  );
}
