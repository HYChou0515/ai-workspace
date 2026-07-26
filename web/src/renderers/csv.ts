/** Rows of string cells → delimited text, the inverse of `parseCsv`. A field is
 * quoted ONLY when it holds the delimiter, a quote or a newline (so an untouched
 * file doesn't grow quotes just by being opened); an embedded `"` is escaped as
 * `""`. Pass `"\t"` for TSV.
 *
 * `like` is the file's ORIGINAL text, and it exists because `parseCsv` is lossy
 * in two ways the round-trip would otherwise expose: it drops `\r`, and it can't
 * tell a trailing newline from its absence. Without `like`, opening a CRLF file
 * and saving it rewrites every line ending, and a file with no final newline
 * grows one — a whole-file diff nobody asked for. Pass the text the rows were
 * parsed from and both styles are preserved. Omitted ⇒ LF + a trailing newline.
 */
export function serializeCsv(rows: string[][], delimiter: string = ",", like?: string): string {
  const eol = like !== undefined && like.includes("\r\n") ? "\r\n" : "\n";
  const trailing = like === undefined || /\r?\n$/.test(like) ? eol : "";
  const needsQuotes = (field: string): boolean =>
    field.includes(delimiter) || field.includes('"') || field.includes("\n") || field.includes("\r");
  const cell = (field: string): string => (needsQuotes(field) ? `"${field.replaceAll('"', '""')}"` : field);
  return rows.map((row) => row.map(cell).join(delimiter)).join(eol) + trailing;
}

/** Minimal RFC-4180-ish delimited-text parser → rows of string cells. Handles
 * quoted fields (delimiter + embedded newlines inside quotes), `""` escapes, and
 * CRLF. A trailing newline does not produce an empty final row. The delimiter
 * defaults to a comma (CSV); pass `"\t"` for TSV (#255). */
export function parseCsv(text: string, delimiter: string = ","): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
        } else {
          inQuotes = false;
          i += 1;
        }
      } else {
        field += c;
        i += 1;
      }
      continue;
    }
    if (c === '"') {
      inQuotes = true;
      i += 1;
    } else if (c === delimiter) {
      row.push(field);
      field = "";
      i += 1;
    } else if (c === "\r") {
      i += 1;
    } else if (c === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      i += 1;
    } else {
      field += c;
      i += 1;
    }
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}
