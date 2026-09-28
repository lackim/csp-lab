import assert from "node:assert/strict";
import { test } from "node:test";
import { parseAddress, parseProtocol } from "../dist/lab.js";

test("accepts only explicitly supported CSP versions", () => {
  assert.equal(parseProtocol("1"), 1);
  assert.equal(parseProtocol("2"), 2);
  assert.throws(() => parseProtocol("3"), /Protocol must be 1 or 2/);
});

test("rejects addresses outside the selected protocol range", () => {
  assert.equal(parseAddress("31", 1), 31);
  assert.equal(parseAddress("16383", 2), 16383);
  assert.throws(() => parseAddress("32", 1), /between 1 and 31/);
  assert.throws(() => parseAddress("0", 2), /between 1 and 16383/);
  assert.throws(() => parseAddress("2foo", 2), /decimal integer/);
});
