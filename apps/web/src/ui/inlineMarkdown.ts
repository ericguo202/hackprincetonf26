// Lane D: the only markdown the concierge may use: **bold**, *italic* and _italic_.
// Everything else (headings, lists, links, code) stays as plain text on purpose.

export type InlineSpan = { text: string; bold: boolean; italic: boolean };

// Delimiters must hug non-space text, so "2 * 3 * 4" and snake_case_names stay literal.
const BOLD = /\*\*(?=\S)([\s\S]*?\S)\*\*/;
const STAR_ITALIC = /(?<![*\w])\*([^\s*](?:[^*]*?[^\s*])?)\*(?![*\w])/;
const UNDER_ITALIC = /(?<![_\w])_([^\s_](?:[^_]*?[^\s_])?)_(?![_\w])/;

function earliest(text: string, patterns: { re: RegExp; bold: boolean }[]) {
  let best: { index: number; length: number; inner: string; bold: boolean } | null = null;
  for (const { re, bold } of patterns) {
    const m = re.exec(text);
    if (m && (best === null || m.index < best.index)) {
      best = { index: m.index, length: m[0].length, inner: m[1], bold };
    }
  }
  return best;
}

function parse(text: string, bold: boolean, italic: boolean, out: InlineSpan[]): void {
  const patterns = [
    ...(bold ? [] : [{ re: BOLD, bold: true }]),
    ...(italic ? [] : [{ re: STAR_ITALIC, bold: false }, { re: UNDER_ITALIC, bold: false }]),
  ];
  let rest = text;
  while (rest) {
    const hit = patterns.length ? earliest(rest, patterns) : null;
    if (!hit) {
      push(out, { text: rest, bold, italic });
      return;
    }
    if (hit.index > 0) push(out, { text: rest.slice(0, hit.index), bold, italic });
    if (hit.bold) parse(hit.inner, true, italic, out);
    else parse(hit.inner, bold, true, out);
    rest = rest.slice(hit.index + hit.length);
  }
}

function push(out: InlineSpan[], span: InlineSpan): void {
  const last = out[out.length - 1];
  if (last && last.bold === span.bold && last.italic === span.italic) last.text += span.text;
  else out.push(span);
}

/** Split text into spans styled by **bold**, *italic* and _italic_; nothing else is interpreted. */
export function parseInlineMarkdown(text: string): InlineSpan[] {
  const out: InlineSpan[] = [];
  parse(text, false, false, out);
  return out;
}
