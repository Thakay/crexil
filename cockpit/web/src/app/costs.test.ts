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
  expect(usd(0.005)).toBe("$0.005"); // money that moved never rounds to a cent or to zero
  expect(usd(0.0032)).toBe("$0.0032");
  expect(usd(0.00004)).toBe("$0.00004");
  expect(usd(0.004 + 0.0001)).toBe("$0.0041");
  expect(usd(-0.0025)).toBe("-$0.0025");
});

test("money above a cent is never rounded down to the cent (laws 5 and 6)", () => {
  expect(usd(0.012)).toBe("$0.012"); // three two-second sound effects
  expect(usd(0.0336)).toBe("$0.0336"); // four voice design previews
  expect(usd(1)).toBe("$1.00");
  expect(usd(1.5)).toBe("$1.50");
  expect(usd(12.345678)).toBe("$12.345678");
  expect(usd(0.1 + 0.2)).toBe("$0.30"); // float noise past the micro-dollar is not money
  expect(usd(0.0000004)).toBe("$0.0000004");
});
