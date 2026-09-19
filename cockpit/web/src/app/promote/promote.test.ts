// The plinth's words. The register is the feature here: promote is
// the one act the artist can't take back from inside the cockpit, so every
// assertion below is about what a sentence SAYS, not that one exists.
import { expect, test } from "vitest";
import {
  IRREVERSIBLE,
  alreadyLine,
  brokenLines,
  browseHead,
  emptyLine,
  fenceLine,
  fileCount,
  folderWhy,
  holdLabel,
  promoteErr,
  startAt,
  targetLines,
  type Folder,
  type Preview,
  type Target,
} from "./promote";

const target = (over: Partial<Target> = {}): Target => ({
  path: "/mnt/d/PROMOTED",
  ok: true,
  message: null,
  remedy: null,
  where: "on the Windows drive mounted at /mnt/d",
  source: "env",
  default_path: "/mnt/d/PROMOTED",
  windows_path: "D:\\PROMOTED",
  roots: ["/mnt/d"],
  roots_said: "D:\\",
  ...over,
});

const preview = (over: Partial<Preview> = {}): Preview => ({
  node_id: "n1",
  asset_id: "a1",
  folder: "/mnt/d/PROMOTED/hero-sword",
  files: [
    { path: "/mnt/d/PROMOTED/hero-sword/a1.png", what: "the take" },
    { path: "/mnt/d/PROMOTED/hero-sword/a1.json", what: "its manifest" },
  ],
  facts: "12b · RAW-SDXL · S 7",
  cost: "This take cost no money — it ran on your own card.",
  replayable: "exact: the submitted graph is verbatim",
  target: target(),
  already: [],
  ...over,
});

test("the hold says PROMOTE once, and AGAIN when it isn't the first", () => {
  expect(holdLabel(preview())).toBe("HOLD TO PROMOTE");
  expect(holdLabel(preview({ already: ["2026-08-27T10:00:00Z"] }))).toBe(
    "HOLD TO PROMOTE AGAIN",
  );
});

test("an earlier promotion is stated, and never used to claim the files are still there", () => {
  expect(alreadyLine(preview())).toBeNull();
  const once = alreadyLine(preview({ already: ["2026-08-27T10:00:00Z"] }))!;
  expect(once).toMatch(/already gone out once/);
  expect(once).toMatch(/second copy under a new id/);
  // the honest half: the cockpit does not watch that folder
  expect(once).toMatch(/doesn't watch the folder/);
  expect(
    alreadyLine(
      preview({ already: ["2026-08-27T10:00:00Z", "2026-08-27T11:00:00Z"] }),
    ),
  ).toMatch(/gone out 2 times/);
});

test("the destination leads with the artist's spelling, and keeps ours", () => {
  // D:\PROMOTED is how a Windows user names this tree;
  // /mnt/d/PROMOTED is what a refusal and a manifest will say
  expect(targetLines(target())).toEqual([
    "D:\\PROMOTED — on the Windows drive mounted at /mnt/d",
    "/mnt/d/PROMOTED in here",
  ]);
  // off a Windows drive there is one spelling, and it stands alone
  const own = targetLines(
    target({
      path: "/home/artist/out",
      windows_path: null,
      where: "on the cockpit's own disk",
    }),
  );
  expect(own).toEqual(["/home/artist/out — on the cockpit's own disk"]);
  // re-pointed: what .env says is still on screen, so RESET means something
  const moved = targetLines(
    target({ path: "/home/artist/out", windows_path: null, source: "set-here", where: "on the cockpit's own disk" }),
  );
  expect(moved[1]).toMatch(/\/mnt\/d\/PROMOTED is what \.env says/);
});

test("the fence is printed, not just enforced", () => {
  // a fence you can't see is just a locked door: the plinth says where a
  // re-point MAY go, in the artist's spelling, beside the field
  const line = fenceLine(target());
  expect(line).toMatch(/Anywhere on D:\\/);
  expect(line).toMatch(/has to be there already/);
  expect(line).toMatch(/never creates one/);
  expect(fenceLine(target({ roots_said: "/srv/assets" }))).toMatch(
    /Anywhere on \/srv\/assets/,
  );
});

test("a broken target gives back both of D5's halves, in order", () => {
  expect(brokenLines(target())).toEqual([]);
  expect(
    brokenLines(
      target({
        ok: false,
        message: "the promote target /mnt/d/PROMOTED isn't there",
        remedy: "put the folder back, or point the cockpit at another folder",
      }),
    ),
  ).toEqual([
    "the promote target /mnt/d/PROMOTED isn't there",
    "put the folder back, or point the cockpit at another folder",
  ]);
});

test("a refusal is the server's own words, and the fallback still says the cost", () => {
  expect(
    promoteErr({ detail: "the drive isn't mounted — put it back", remedy: "put it back" }),
  ).toEqual({ detail: "the drive isn't mounted — put it back", remedy: "put it back" });
  const fallback = promoteErr(undefined);
  expect(fallback.detail).toMatch(/nothing was copied out/);
  expect(fallback.detail).toMatch(/the take is untouched/);
  expect(fallback.remedy).toBeUndefined();
});

test("the irreversible line says what stays as well as what leaves", () => {
  expect(IRREVERSIBLE).toMatch(/cannot be un-happened/);
  expect(IRREVERSIBLE).toMatch(/The take stays on the canvas/);
});

test("the file count agrees with itself", () => {
  expect(fileCount(preview())).toBe("2 FILES");
  expect(fileCount(preview({ files: [preview().files[0]] }))).toBe("1 FILE");
});

// ── the picker ──────────────────────────────────────────────────────────

const folder = (over: Partial<Folder> = {}): Folder => ({
  name: "hero-sword",
  path: "/mnt/d/PROMOTED/hero-sword",
  windows_path: "D:\\PROMOTED\\hero-sword",
  ok: true,
  why: null,
  ...over,
});

test("the browser opens where the cockpit points, or at the drive when that is gone", () => {
  expect(startAt(target())).toBe("/mnt/d/PROMOTED");
  expect(startAt(target({ ok: false }))).toBe("");
});

test("the head line is the artist's spelling, and the fence at the top", () => {
  expect(browseHead({ here: folder() }, target())).toBe(
    "D:\\PROMOTED\\hero-sword",
  );
  expect(
    browseHead({ here: folder({ windows_path: null, path: "/srv/x" }) }, target()),
  ).toBe("/srv/x");
  expect(browseHead({ here: null }, target())).toBe("D:\\");
});

test("an unfit folder keeps its name and gains its reason (law 12)", () => {
  expect(folderWhy(folder())).toBeNull();
  expect(folderWhy(folder({ ok: false, why: "it can't be written to" }))).toBe(
    "it can't be written to",
  );
  expect(folderWhy(folder({ ok: false, why: null }))).toBe(
    "can't take a promotion right now",
  );
});

test("an empty level says so, in the server's words when it has them", () => {
  expect(emptyLine({ folders: [folder()], note: null })).toBeNull();
  expect(emptyLine({ folders: [], note: null })).toBe(
    "No folders inside this one.",
  );
  expect(emptyLine({ folders: [], note: "this folder can't be read" })).toBe(
    "this folder can't be read",
  );
});
