/* What this device can say about where and when it is.
 *
 * All four values are already in the browser -- no permission prompt, no
 * network call, no third party. That is the whole design: a timezone names a
 * city that stands for a region, which is exactly the resolution "general
 * location" asks for, and it costs nothing to obtain.
 *
 * Every reader is wrapped, because each of these is missing or throws
 * somewhere: `Intl.DisplayNames` is newer than the rest, a locale can arrive
 * without a region subtag, and a hardened browser can refuse the lot. A
 * partial answer is useful and a thrown one is not, so anything that fails is
 * simply left out.
 */

function resolved() {
  try {
    return Intl.DateTimeFormat().resolvedOptions();
  } catch {
    return {};
  }
}

/* The country behind a locale, named in the reader's own language: en-GB ->
 * "United Kingdom". Falls back to the bare code, which the model reads fine. */
function regionName(locale) {
  const code = String(locale || "").split("-").find((part) => /^[A-Z]{2}$/.test(part));
  if (!code) return null;
  try {
    return new Intl.DisplayNames([locale], { type: "region" }).of(code) || code;
  } catch {
    return code;
  }
}

export function clientContext() {
  const options = resolved();
  const locale = options.locale || navigator.language || null;

  let utcOffset = null;
  try {
    // getTimezoneOffset() counts minutes *behind* UTC, which inverts the sign
    // everything else uses. Negated here so the server stores one convention.
    utcOffset = -new Date().getTimezoneOffset();
  } catch {
    utcOffset = null;
  }

  return {
    timezone: options.timeZone || null,
    locale,
    utc_offset: utcOffset,
    region: regionName(locale),
  };
}
