// The top bar's money corner (the right-aligned readout, drawn on every
// screen): the LOCAL ONLY switch (a real switch, green when it means it)
// and the charged totals. The switch is the artist's claim; every money
// verb's request carries it and the server refuses paid work beneath it
// (the proof). The totals are CHARGED only: the ledger summed server-side,
// today and this month on the artist's clock, refreshed with the workspace
// feed (invalidate.ts).
import { $api } from "../../api/client";
import { monthWord, tzMinutes, usd } from "../costs";
import { useUi } from "../state/ui";
import styles from "./Shell.module.css";

export const LOCAL_ONLY_ON =
  "On — every run, reroll and backfill goes out under the switch, and the " +
  "server refuses any paid take beneath it. No take leaves this machine.";
export const LOCAL_ONLY_OFF =
  "Off — external models sit on the roster at their prices.";
export const TOTALS_TITLE =
  "Charged, from the ledger, across every workspace — a quote that hasn't " +
  "been billed doesn't count. Today and this month in your own clock.";

export function LocalOnlySwitch() {
  const on = useUi((s) => s.localOnly);
  const setLocalOnly = useUi((s) => s.setLocalOnly);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      className={styles.localOnly}
      onClick={() => setLocalOnly(!on)}
      title={on ? LOCAL_ONLY_ON : LOCAL_ONLY_OFF}
    >
      <span className={styles.knob} aria-hidden="true" />
      <span className="mono-label">LOCAL ONLY</span>
    </button>
  );
}

export function ChargedTotals() {
  const costs = $api.useQuery("get", "/api/costs", {
    params: { query: { tz: tzMinutes() } },
  });
  const c = costs.data;
  return (
    <span
      role="status"
      aria-label="Charged totals"
      className={`mono-label ${styles.totals}`}
      title={TOTALS_TITLE}
    >
      {costs.isError ? (
        "TOTALS · SERVER AWAY"
      ) : c === undefined ? (
        "TODAY … · MONTH …"
      ) : (
        <>
          TODAY <b data-spent={c.today_usd > 0}>{usd(c.today_usd)}</b> ·{" "}
          {monthWord(c.month)}{" "}
          <b data-spent={c.month_usd > 0}>{usd(c.month_usd)}</b>
        </>
      )}
    </span>
  );
}
