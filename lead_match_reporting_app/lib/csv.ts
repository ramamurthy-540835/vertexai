export type CsvRow = Record<string, string>;

export function parseCsv(input: string): CsvRow[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let value = "";
  let quoted = false;

  for (let index = 0; index < input.length; index += 1) {
    const char = input[index];
    const next = input[index + 1];

    if (quoted) {
      if (char === '"' && next === '"') {
        value += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        value += char;
      }
      continue;
    }

    if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(value);
      value = "";
    } else if (char === "\n") {
      row.push(value);
      rows.push(row);
      row = [];
      value = "";
    } else if (char !== "\r") {
      value += char;
    }
  }

  if (value || row.length > 0) {
    row.push(value);
    rows.push(row);
  }

  const [headers, ...records] = rows;
  if (!headers) {
    return [];
  }

  return records
    .filter((record) => record.some((field) => field !== ""))
    .map((record) =>
      headers.reduce<CsvRow>((accumulator, header, index) => {
        accumulator[header] = record[index] ?? "";
        return accumulator;
      }, {}),
    );
}

export function toCsv(rows: CsvRow[]): string {
  if (!rows.length) {
    return "";
  }

  const headers = Object.keys(rows[0]);
  const escape = (value: string) => {
    if (/[",\n\r]/.test(value)) {
      return `"${value.replaceAll('"', '""')}"`;
    }
    return value;
  };

  return [
    headers.join(","),
    ...rows.map((row) => headers.map((header) => escape(row[header] ?? "")).join(",")),
  ].join("\n");
}
