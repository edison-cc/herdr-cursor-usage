import { cachedReport, claimLock, isFetchInFlight, showCachedThenFetch } from "../mods/cursor-usage/cache.js";

const events = [];
const text = JSON.stringify({ savedAt: 1000, report: { ok: true, meter: { value: "20%" } } });
showCachedThenFetch(
  text,
  (cached) => events.push(["paint", cached.report.meter.value]),
  () => events.push(["fetch"]),
);
if (events.length !== 2 || events[0][0] !== "paint" || events[0][1] !== "20%" || events[1][0] !== "fetch") {
  console.error(events);
  process.exit(1);
}
if (cachedReport("not json") !== null) process.exit(2);
if (cachedReport(null) !== null) process.exit(3);
if (cachedReport(JSON.stringify({ savedAt: 1, report: { ok: false } })) !== null) process.exit(4);
if (cachedReport("") !== null) process.exit(5);

const report = JSON.stringify({ savedAt: 500, report: { ok: true } });
const lock = JSON.stringify({ startedAt: 1000 });
if (!isFetchInFlight(report, lock, 1500)) process.exit(6);
if (isFetchInFlight(JSON.stringify({ savedAt: 1000, report: { ok: true } }), lock, 1500)) process.exit(7);
if (isFetchInFlight(null, "", 1500)) process.exit(8);
if (isFetchInFlight(null, lock, 1000 + 100000)) process.exit(9);
const claim = claimLock(null, null, 20);
if (!claim || claim.expected !== null || !claim.text.includes("20")) process.exit(10);
if (claimLock(lock, report, 1500) !== null) process.exit(11);
const stale = claimLock(lock, report, 1000 + 100000);
if (!stale || stale.expected !== lock) process.exit(12);
