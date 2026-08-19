import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const baseDir = path.dirname(fileURLToPath(import.meta.url));
const reportPath = path.join(baseDir, "S26_record_time_update_summary.json");
const outsideSegmentReportPath = path.join(
  baseDir,
  "S26_record_time_outside_segment_update_summary.json"
);
const sourceErrorRetryReportPath = path.join(
  baseDir,
  "S26_record_time_source_error_retry_summary.json"
);
const forceEventErrorReportPath = path.join(
  baseDir,
  "S26_force_event_error_summary.json"
);
const forceOutsideThresholdReportPath = path.join(
  baseDir,
  "S26_force_outside_threshold_summary.json"
);
const forceSourceRetryThresholdReportPath = path.join(
  baseDir,
  "S26_force_source_retry_threshold_summary.json"
);
const outputDir = path.join(baseDir, "outputs");
const outputPath = path.join(
  outputDir,
  "S26_skipped_record_list_after_forced_update.xlsx"
);

const report = JSON.parse(await fs.readFile(reportPath, "utf8"));
const outsideSegmentReport = JSON.parse(
  await fs.readFile(outsideSegmentReportPath, "utf8")
);
const sourceErrorRetryReport = JSON.parse(
  await fs.readFile(sourceErrorRetryReportPath, "utf8")
);
const forceEventErrorReport = JSON.parse(
  await fs.readFile(forceEventErrorReportPath, "utf8")
);
const forceOutsideThresholdReport = JSON.parse(
  await fs.readFile(forceOutsideThresholdReportPath, "utf8")
);
const forceSourceRetryThresholdReport = JSON.parse(
  await fs.readFile(forceSourceRetryThresholdReportPath, "utf8")
);
const finalResults = [
  ...report.results.filter(
    (row) =>
      row.status !== "over_threshold" &&
      row.status !== "source_error" &&
      row.status !== "event_error" &&
      row.status !== "velocity_not_unique"
  ),
  ...outsideSegmentReport.results.filter((row) => row.status !== "over_threshold"),
  ...sourceErrorRetryReport.results.filter(
    (row) => row.status !== "over_threshold"
  ),
  ...forceEventErrorReport.results,
  ...forceOutsideThresholdReport.results,
  ...forceSourceRetryThresholdReport.results,
];
const skipped = finalResults.filter(
  (row) =>
    row.status !== "updated" &&
    row.status !== "unchanged" &&
    row.status !== "forced_updated"
);
const skippedCounts = Object.entries(
  skipped.reduce((counts, row) => {
    counts[row.status] = (counts[row.status] ?? 0) + 1;
    return counts;
  }, {})
);

function pathParts(recordPath) {
  const parts = recordPath.split("\\");
  const validIndex = parts.findIndex((part) => part.endsWith("_valid"));
  return {
    city: validIndex >= 0 ? parts[validIndex].replace("_valid", "") : "",
    eventType: validIndex >= 0 ? parts[validIndex + 1] ?? "" : "",
    segment: validIndex >= 0 ? parts[validIndex + 2] ?? "" : "",
    event: validIndex >= 0 ? parts[validIndex + 3] ?? "" : "",
  };
}

const rows = skipped.map((row) => {
  const parts = pathParts(row.record);
  return [
    parts.city,
    parts.eventType,
    parts.segment,
    parts.event,
    row.status,
    row.start_error_ms ?? null,
    row.end_error_ms ?? null,
    row.detail ?? "",
    row.record,
  ];
});

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const details = workbook.worksheets.add("Skipped Records");

summary.getRange("A1:B1").merge();
summary.getRange("A1").values = [["S26 Record Time Update - Skipped Records"]];
summary.getRange("A3:B3").values = [["Status", "Count"]];
const summaryRows = skippedCounts.map(([status, count]) => [status, count]);
if (summaryRows.length) {
  summary.getRangeByIndexes(3, 0, summaryRows.length, 2).values = summaryRows;
}
summary.getRange(`A3:B${Math.max(summaryRows.length + 3, 3)}`).format.borders = {
  preset: "all",
  style: "thin",
  color: "#D9E2F3",
};
summary.getRange("A1:B1").format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
summary.getRange("A3:B3").format = {
  fill: "#D9EAF7",
  font: { bold: true, color: "#1F1F1F" },
};
summary.getRange("B4:B100").format.numberFormat = "#,##0";
summary.getRange("A1:B100").format.autofitColumns();
summary.getRange("A1").format.rowHeight = 24;
summary.showGridLines = false;

const headers = [
  "City",
  "Event Type",
  "Segment",
  "Event",
  "Skip Status",
  "Start Error (ms)",
  "End Error (ms)",
  "Detail",
  "Record Path",
];
details.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
if (rows.length) {
  details.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
  details.tables.add(`A1:I${rows.length + 1}`, true, "SkippedRecordsTable");
}
details.getRange("A1:I1").format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
if (rows.length) {
  details.getRange(`F2:G${rows.length + 1}`).format.numberFormat = "#,##0";
}
details.getRange(`A1:I${rows.length + 1}`).format.wrapText = false;
details.getRange("A:A").format.columnWidth = 13;
details.getRange("B:B").format.columnWidth = 18;
details.getRange("C:C").format.columnWidth = 54;
details.getRange("D:D").format.columnWidth = 12;
details.getRange("E:E").format.columnWidth = 22;
details.getRange("F:G").format.columnWidth = 16;
details.getRange("H:H").format.columnWidth = 45;
details.getRange("I:I").format.columnWidth = 110;
details.freezePanes.freezeRows(1);
details.showGridLines = false;

await fs.mkdir(outputDir, { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

const check = await workbook.inspect({
  kind: "table",
  range: "Skipped Records!A1:I8",
  include: "values",
  tableMaxRows: 8,
  tableMaxCols: 9,
});
console.log(check.ndjson);
const preview = await workbook.render({
  sheetName: "Skipped Records",
  range: "A1:I12",
  scale: 1,
  format: "png",
});
await fs.writeFile(
  path.join(outputDir, "S26_skipped_record_list_preview.png"),
  new Uint8Array(await preview.arrayBuffer()),
);
console.log(JSON.stringify({ outputPath, skipped: rows.length }));
