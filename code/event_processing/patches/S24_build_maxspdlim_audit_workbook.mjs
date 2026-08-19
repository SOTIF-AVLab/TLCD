import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputPath = process.argv[2];
const outputPath = process.argv[3];
const previewPath = process.argv[4];
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const events = workbook.worksheets.add("Review Events");

summary.showGridLines = false;
summary.getRange("A1:D1").merge();
summary.getRange("A1").values = [["Nanjing Valid MaxSpdlim Rule Audit"]];
summary.getRange("A1:D1").format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF", size: 16 },
  rowHeight: 28,
};
summary.getRange("A3:B9").values = [
  ["Metric", "Count"],
  ["Total events", data.summary.total_events],
  ["Pass events", data.summary.pass_events],
  ["Review events", data.summary.review_events],
  ["Evidence mismatch events", data.summary.evidence_mismatch_events],
  ["JSON mismatch events", data.summary.json_mismatch_events],
  ["Data issue events", data.summary.data_issue_events],
];
summary.getRange("A3:B3").format = {
  fill: "#D9EAF7",
  font: { bold: true, color: "#1F1F1F" },
  borders: { preset: "doubleBottom", style: "thin", color: "#7F8C8D" },
};
summary.getRange("B4:B9").format.numberFormat = "0";
summary.getRange("A11:B11").values = [["Issue type", "Affected events"]];
summary.getRange("A11:B11").format = {
  fill: "#D9EAF7",
  font: { bold: true },
  borders: { preset: "doubleBottom", style: "thin", color: "#7F8C8D" },
};
const issueRows = Object.entries(data.summary.issue_event_counts);
if (issueRows.length) {
  summary.getRangeByIndexes(11, 0, issueRows.length, 2).values = issueRows;
}
const assumptionStart = 13 + issueRows.length;
summary.getRangeByIndexes(assumptionStart - 1, 0, 1, 4).merge();
summary.getRangeByIndexes(assumptionStart - 1, 0, 1, 4).values = [["Audit assumptions"]];
summary.getRangeByIndexes(assumptionStart - 1, 0, 1, 4).format = {
  fill: "#E2F0D9",
  font: { bold: true },
};
const assumptionRows = data.assumptions.map((text, index) => [index + 1, text]);
summary.getRangeByIndexes(assumptionStart, 0, assumptionRows.length, 2).values = assumptionRows;
summary.getRangeByIndexes(assumptionStart, 1, assumptionRows.length, 3).merge(true);
summary.getRangeByIndexes(assumptionStart, 1, assumptionRows.length, 3).format.wrapText = true;
summary.getRangeByIndexes(assumptionStart, 0, assumptionRows.length, 4).format.rowHeight = 32;
summary.getRangeByIndexes(0, 0, assumptionStart + assumptionRows.length, 4).format.font = {
  name: "Calibri",
  size: 10,
};
summary.getRange("A:A").format.columnWidth = 24;
summary.getRange("B:B").format.columnWidth = 18;
summary.getRange("C:D").format.columnWidth = 26;

const headers = data.review_events.length
  ? Object.keys(data.review_events[0])
  : ["segment", "event", "status", "issue"];
const rows = data.review_events.map((row) => headers.map((header) => row[header] ?? ""));
events.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
if (rows.length) {
  events.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
}
const used = events.getRangeByIndexes(0, 0, Math.max(rows.length + 1, 1), headers.length);
used.format.font = { name: "Calibri", size: 9 };
events.getRangeByIndexes(0, 0, 1, headers.length).format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF", size: 9 },
  wrapText: true,
};
events.freezePanes.freezeRows(1);
events.freezePanes.freezeColumns(2);
events.showGridLines = false;
events.tables.add(
  `A1:${String.fromCharCode(64 + Math.min(headers.length, 26))}${Math.max(rows.length + 1, 1)}`,
  true,
  "ReviewEventsTable",
);
used.format.autofitColumns();
for (let column = 0; column < headers.length; column += 1) {
  const header = headers[column];
  const width = header === "event_path" ? 48 : header === "segment" ? 42 : header === "issue" ? 28 : 16;
  events.getRangeByIndexes(0, column, Math.max(rows.length + 1, 1), 1).format.columnWidth = width;
}

const summaryPreview = await workbook.render({
  sheetName: "Summary",
  autoCrop: "all",
  scale: 1.5,
  format: "png",
});
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(previewPath, new Uint8Array(await summaryPreview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

console.log((await workbook.inspect({
  kind: "table",
  range: "Summary!A1:D25",
  include: "values,formulas",
  tableMaxRows: 25,
  tableMaxCols: 4,
})).ndjson);
console.log((await workbook.inspect({
  kind: "table",
  range: "Review Events!A1:H8",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 8,
})).ndjson);
console.log((await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 50 },
  summary: "final formula error scan",
})).ndjson);
