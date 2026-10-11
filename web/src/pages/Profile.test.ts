import { describe, expect, test } from "bun:test";

import { completeness, pictureOf } from "../account.tsx";
import type { Account } from "../types.ts";
import { changes, draftOf, githubName } from "./Profile.tsx";

const account: Account = {
  email: "ada@example.com",
  name: "Ada Lovelace",
  picture_url: null,
  picture_uploaded: false,
  can_upload_picture: true,
  google_linked: false,
  github: "ada",
  company: null,
  role: null,
  location: null,
  website: null,
  bio: null,
  can_hunt: true,
  is_ambassador: false,
  is_admin: false,
  is_owner: false,
  invite_only: true,
  verified_breaks: 0,
  credits: { balance: 25, cap: 25, window_hours: 24, tier: "Starter", next_tier: "Proven", next_cap: 200, costs: [] },
};

describe("profile", () => {
  test("a GitHub username is taken out of whatever was pasted", () => {
    for (const pasted of ["ada", "@ada", "github.com/ada", "https://github.com/ada/", "  https://www.github.com/ada  "]) {
      expect(githubName(pasted)).toBe("ada");
    }
    expect(githubName("")).toBe("");
  });

  test("only what changed is sent, and clearing a field sends it empty", () => {
    const draft = draftOf(account);
    expect(changes(draft, account)).toEqual({});
    expect(changes({ ...draft, name: " Ada Lovelace " }, account)).toEqual({});
    expect(changes({ ...draft, company: " Engines ", github: "" }, account)).toEqual({ company: "Engines", github: "" });
  });

  test("completeness counts the fields that are filled in", () => {
    expect(completeness(account)).toEqual({ done: 2, total: 7, percent: 29 });
    const full = { ...account, company: "c", role: "r", location: "l", website: "https://x.y", bio: "b" };
    expect(completeness(full).percent).toBe(100);
  });

  test("the picture is Google's, then GitHub's, then none", () => {
    expect(pictureOf({ picture_url: "https://lh3.googleusercontent.com/a", github: "ada" })).toBe("https://lh3.googleusercontent.com/a");
    expect(pictureOf({ picture_url: null, github: "ada" })).toBe("https://github.com/ada.png?size=160");
    expect(pictureOf({ picture_url: null, github: null })).toBeNull();
  });
});
