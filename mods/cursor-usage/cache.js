// Last dashboard report, kept so the pane can draw before Cursor answers.
// The file is { savedAt, report }. The report has no access token.

export function cachedReport(text) {
  if (typeof text !== "string" || !text.trim()) return null;
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  if (!data || typeof data !== "object") return null;
  const report = data.report;
  const savedAt = Number(data.savedAt);
  if (!report || report.ok !== true || !Number.isFinite(savedAt)) return null;
  return { report, savedAt };
}

export function cacheText(report, savedAt) {
  return JSON.stringify({ savedAt, report });
}

// Paint a usable cache, then start the collector. The paint runs first so the
// cards are on screen before any Cursor call.
export function showCachedThenFetch(text, paint, fetchReport) {
  const cached = cachedReport(text);
  if (cached) paint(cached);
  return fetchReport();
}

// A live collector holds this long. The collector itself stops at 90 seconds.
export const FETCH_LIMIT_MS = 100 * 1000;

export function lockRecord(text) {
  if (typeof text !== "string" || !text.trim()) return null;
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  const startedAt = Number(data?.startedAt);
  if (!Number.isFinite(startedAt)) return null;
  return { startedAt };
}

// True while another view's collector is still the one to wait for.
// A report saved after the lock means that collector already finished.
export function isFetchInFlight(cacheText, lockText, now, limitMs = FETCH_LIMIT_MS) {
  const lock = lockRecord(lockText);
  if (!lock || now - lock.startedAt >= limitMs) return false;
  const cached = cachedReport(cacheText);
  if (cached && cached.savedAt >= lock.startedAt) return false;
  return true;
}

// The bytes to write, or null when a live collector already owns the lock.
// `expected` is the previous file text, or null when the file is absent.
export function claimLock(lockText, cacheText, now, limitMs = FETCH_LIMIT_MS) {
  if (isFetchInFlight(cacheText, lockText, now, limitMs)) return null;
  return {
    expected: lockText == null ? null : lockText,
    text: JSON.stringify({ startedAt: now }),
  };
}
