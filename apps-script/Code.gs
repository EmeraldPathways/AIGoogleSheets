function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('AI Assistant')
    .addItem('Open Sidebar', 'showSidebar')
    .addItem('Open Wide Dialog', 'showWideDialog')
    .addToUi();
}

function showSidebar() {
  var html = HtmlService.createHtmlOutputFromFile('Sidebar')
    .setTitle('Sheets AI Assistant');
  SpreadsheetApp.getUi().showSidebar(html);
}

function showWideDialog() {
  var html = HtmlService.createHtmlOutputFromFile('Sidebar')
    .setWidth(1100)
    .setHeight(820);
  SpreadsheetApp.getUi().showModalDialog(html, 'Sheets AI Assistant');
}

function getActiveSpreadsheetContext() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getActiveSheet();
  var range = sheet.getActiveRange();
  var dataRange = sheet.getDataRange();
  return {
    spreadsheetId: ss.getId(),
    spreadsheetName: ss.getName(),
    activeSheetName: sheet.getName(),
    activeRangeA1: range ? range.getA1Notation() : null,
    dataRangeA1: dataRange ? dataRange.getA1Notation() : null,
  };
}

function getActiveSheetPayload() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getActiveSheet();
  var dataRange = sheet.getDataRange();
  var values = dataRange.getValues().map(function(row) {
    return row.map(function(cell) {
      if (cell instanceof Date) {
        return cell.toISOString();
      }
      if (cell === null || cell === undefined) {
        return '';
      }
      return cell;
    });
  });
  return {
    spreadsheetId: ss.getId(),
    spreadsheetName: ss.getName(),
    activeSheetName: sheet.getName(),
    range: "'" + sheet.getName().replace(/'/g, "''") + "'!" + dataRange.getA1Notation(),
    values: values,
  };
}

function insertAnalysisResult(payload) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getActiveSheet();
  var targetRange = payload && payload.range ? payload.range : 'A1';
  var resultText = JSON.stringify((payload && payload.result) || {}, null, 2);
  sheet.getRange(targetRange).setValue(resultText);
  return {
    ok: true,
    targetRange: targetRange,
    insertedAt: new Date().toISOString(),
  };
}

function restoreSessionIntoSidebar(session) {
  return {
    ok: true,
    session: session,
  };
}
