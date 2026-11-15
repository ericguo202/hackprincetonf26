import { describe, expect, it } from "vitest";

import { parseInlineMarkdown } from "./inlineMarkdown";

const plain = (text: string) => ({ text, bold: false, italic: false });

describe("parseInlineMarkdown", () => {
  it("leaves plain text alone", () => {
    expect(parseInlineMarkdown("Listed at $425,000.")).toEqual([plain("Listed at $425,000.")]);
  });

  it("renders bold and both italic forms", () => {
    expect(parseInlineMarkdown("The **couch** faces the *TV* and _rug_.")).toEqual([
      plain("The "),
      { text: "couch", bold: true, italic: false },
      plain(" faces the "),
      { text: "TV", bold: false, italic: true },
      plain(" and "),
      { text: "rug", bold: false, italic: true },
      plain("."),
    ]);
  });

  it("nests italic inside bold", () => {
    expect(parseInlineMarkdown("**a *b* c**")).toEqual([
      { text: "a ", bold: true, italic: false },
      { text: "b", bold: true, italic: true },
      { text: " c", bold: true, italic: false },
    ]);
  });

  it("keeps stray markers, math and snake_case literal", () => {
    for (const s of ["2 * 3 * 4", "max_price_usd", "a ** b", "unclosed **bold", "# Heading", "- item"]) {
      expect(parseInlineMarkdown(s)).toEqual([plain(s)]);
    }
  });
});
