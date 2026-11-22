import fs from 'node:fs/promises';
import path from 'node:path';
import { SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const [inputPath, outputDir, previewDir, mode] = process.argv.slice(2);
if (!previewDir) throw new Error('Usage: export_rule_workbook.mjs input.json output-dir preview-dir');
const data = JSON.parse(await fs.readFile(inputPath, 'utf8'));
const workbook = Workbook.create();
const summary = workbook.worksheets.add('Model comparison');
const facilities = workbook.worksheets.add('Facilities');
const evidence = workbook.worksheets.add('Count evidence');
const navy = '#19374A';
const teal = '#0C625B';
const body = { name: 'Arial', size: 10, color: '#182D3A' };
const header = { fill: navy, font: { ...body, bold: true, color: '#FFFFFF' },
  verticalAlignment: 'center', rowHeight: 32, wrapText: true };

for (const sheet of [summary, facilities, evidence]) sheet.showGridLines = false;
summary.tabColor = navy;
facilities.tabColor = teal;

function width(sheet, widths) {
  widths.forEach((size, index) => {
    sheet.getRangeByIndexes(0, index, 1, 1).format.columnWidth = size;
  });
}

function safeText(value) {
  if (typeof value !== 'string') return value;
  const cleaned = value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g, '');
  return cleaned.startsWith('=') ? `'${cleaned}` : cleaned;
}

function excelDate(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? new Date(`${value}T00:00:00Z`) : value;
}

function writeBlock(sheet, firstRow, values) {
  const blockSize = 500;
  const columns = values[0].length;
  for (let start = 0; start < values.length; start += blockSize) {
    const block = values.slice(start, start + blockSize);
    sheet.getRangeByIndexes(firstRow + start, 0, block.length, columns).values = block;
  }
}

summary.getRange('A2:I2').merge();
summary.getRange('A2').values = [['Facility capacity comparison']];
summary.getRange('A2').format.font = { ...body, size: 15, bold: true };
summary.getRange('A2:I2').format.rowHeight = 27;
summary.getRange('A3:I3').merge();
summary.getRange('A3').values = [[`Released model: ${data.selected_policy} with ${data.selected_tier.toLowerCase()} features. Selected on validation MAE.`]];
summary.getRange('A4:I4').merge();
summary.getRange('A4').values = [[`${data.test_n} common test branches. ${data.release.distinct_facilities_imputed.toLocaleString('en-US')} branches received a bed estimate.`]];
const summaryHeadings = ['Count selection', 'Feature tier', 'Training branches', 'Validation MAE, beds',
  'Test MAE, beds', 'Test RMSE, beds', 'Test R²', '90% interval coverage', 'Release'];
writeBlock(summary, 5, [summaryHeadings, ...data.score_table]);
summary.getRange('A6:I6').format = header;
const summaryEnd = 6 + data.score_table.length;
summary.getRange(`D7:F${summaryEnd}`).setNumberFormat('0.00');
summary.getRange(`G7:G${summaryEnd}`).setNumberFormat('0.000');
summary.getRange(`H7:H${summaryEnd}`).setNumberFormat('0.0%');
for (const [index, row] of data.score_table.entries()) {
  if (row[8]) summary.getRange(`A${index + 7}:I${index + 7}`).format.fill = '#DFF1ED';
}
const noteStart = summaryEnd + 3;
const notes = [
  ['Confident', 'Every populated reported count has corroborating source evidence or direct document review.'],
  ['Facility confidence', '70% verified-field share plus 30% populated-field share. This is an evidence and completeness score.'],
  ['Five important fields', 'Branch, phone, services, beds, and doctors. Model estimates count toward completeness.'],
  ['Capacity estimates', 'Intervals use held-out calibration. Occupancy and service availability require their own data.'],
];
for (const [index, [label, explanation]] of notes.entries()) {
  const n = noteStart + index;
  summary.getRange(`A${n}`).values = [[label]];
  summary.getRange(`A${n}`).format.font = { ...body, bold: true };
  summary.getRange(`B${n}:I${n}`).merge();
  summary.getRange(`B${n}`).values = [[explanation]];
}
width(summary, [22, 28, 12, 14, 14, 14, 10, 16, 12]);

const keys = Object.keys(data.rows[0]);
const labels = { facility: 'Facility', PIN: 'PIN', state: 'State', district: 'District', beds_final: 'Final beds',
  doctors_selected: 'Selected doctors', important_fields_verified: 'Important fields verified',
  facility_confidence_score: 'Facility confidence score',
  important_fields_populated: 'Important fields populated', bed_estimate_lower: 'Bed estimate lower',
  bed_estimate_upper: 'Bed estimate upper', district_population: 'District population',
  original_beds: 'Original beds', original_doctors: 'Original doctors', routing_eligible: 'Routing eligible',
  bed_source_URL: 'Bed source URL', doctor_source_URL: 'Doctor source URL' };
const numeric = new Set(['original_beds', 'original_doctors']);
const dates = new Set(['bed_observation_date', 'doctor_observation_date']);
const displayValue = (key, value) => {
  if (dates.has(key)) return excelDate(value);
  if (numeric.has(key) && typeof value === 'string' && /^\d+(?:\.0+)?$/.test(value)) return Number(value);
  return safeText(value);
};
const records = [keys.map(key => labels[key] ?? key), ...data.rows.map(row => keys.map(key => displayValue(key, row[key])))];
writeBlock(facilities, 0, records);
facilities.getRangeByIndexes(0, 0, 1, keys.length).format = header;
width(facilities, keys.map((key, i) => ({ facility: 39, PIN: 11, state: 19, district: 18,
  beds_final: 12, doctors_selected: 13, verification: 23, facility_confidence_score: 16,
  important_fields_verified: 14, important_fields_populated: 14, address: 40, facility_id: 48,
  documented_services: 35, bed_source_URL: 40, doctor_source_URL: 40,
  bed_observation_date: 16, doctor_observation_date: 16, district_population: 18 }[key] ?? (i < 22 ? 18 : 24))));
facilities.freezePanes.freezeRows(1);
for (const field of ['beds_final', 'doctors_selected', 'bed_estimate_lower', 'bed_estimate_upper', 'district_population']) {
  facilities.getRangeByIndexes(1, keys.indexOf(field), data.rows.length, 1).setNumberFormat('#,##0');
}
for (const field of dates) {
  facilities.getRangeByIndexes(1, keys.indexOf(field), data.rows.length, 1).setNumberFormat('yyyy-mm-dd');
}
facilities.getRangeByIndexes(1, keys.indexOf('facility_confidence_score'), data.rows.length, 1).setNumberFormat('0');

const evidenceLabels = ['Facility ID', 'Field', 'Count', 'Definition', 'Observation date', 'Publisher',
  'Source overlap or review', 'Source URL', 'Source record or review ID'];
writeBlock(evidence, 0, [evidenceLabels, ...data.evidence.map(row => row.map((value, i) => {
  if (i === 4) return excelDate(value);
  return i === 5 && value === 'sbi_general_archived_quality_disclosure' ? 'SBI General' : safeText(value);
}))]);
evidence.getRange('A1:I1').format = header;
evidence.getRange(`E2:E${data.evidence.length + 1}`).setNumberFormat('yyyy-mm-dd');
evidence.freezePanes.freezeRows(1);
width(evidence, [48, 20, 11, 34, 17, 27, 35, 49, 29]);

workbook.recalculate();
const inspected = await workbook.inspect({ kind: 'table', range: 'Model comparison!A6:I10',
  include: 'values,formulas', tableMaxRows: 5, tableMaxCols: 9 });
console.log(inspected.ndjson);
if (facilities.getRange(`A${records.length}`).values[0][0] !== records.at(-1)[0]) {
  throw new Error('Last facility did not survive the block export.');
}
if (mode !== '--skip-previews') {
  await fs.mkdir(previewDir, { recursive: true });
  for (const [sheet, range, filename] of [
    [summary, `A1:I${noteStart + 3}`, 'model-comparison.png'],
    [facilities, 'A1:J14', 'facilities-preview.png'],
    [evidence, 'A1:F12', 'evidence-preview.png'],
  ]) {
    const preview = await workbook.render({ sheetName: sheet.name, range, scale: 1, format: 'png' });
    await fs.writeFile(path.join(previewDir, filename), new Uint8Array(await preview.arrayBuffer()));
  }
}
await fs.mkdir(outputDir, { recursive: true });
const result = await SpreadsheetFile.exportXlsx(workbook);
await result.save(path.join(outputDir, 'Facility capacity and verification.xlsx'));
