function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('AI Assistant')
    .addItem('Open Sidebar', 'showSidebar')
    .addToUi();
}

function showSidebar() {
  var html = HtmlService.createHtmlOutputFromFile('Sidebar')
    .setTitle('Sheets AI Assistant');
  SpreadsheetApp.getUi().showSidebar(html);
}

function getActiveSpreadsheetContext() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getActiveSheet();
  var range = sheet.getActiveRange();
  return {
    spreadsheetId: ss.getId(),
    spreadsheetName: ss.getName(),
    activeSheetName: sheet.getName(),
    activeRangeA1: range ? range.getA1Notation() : null,
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
