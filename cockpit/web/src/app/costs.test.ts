import { expect, test } from "vitest";
import { monthWord, tzMinutes, usd } from "./costs";

test("tz is minutes east of UTC — the server's sign, not the DOM's", () => {
  const d = new Date();
  expect(tzMinutes(d)).toBe(-d.getTimezoneOffset());
});

test("month word and money spelling", () => {
  expect(monthWord("2026-08")).toBe("AUG");
  expect(monthWord("2026-12")).toBe("DEC");
  expect(monthWord("2027-01")).toBe("JAN");
  expect(monthWord("bogus")).toBe("bogus"); // never invents a month
  expect(usd(0)).toBe("$0.00");
  expect(usd(12.4)).toBe("$12.40");
  expect(usd(0.005)).toBe("$0.01");
});
