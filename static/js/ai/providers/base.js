export class AIServiceProvider {
  constructor(config) {
    this.name = config.name;
    this.endpoint = config.endpoint;
    this.authType = config.authType;
    this.config = config;
  }

  async analyze() {
    throw new Error('Must implement analyze()');
  }

  formatPrompt(sheetData, taskType) {
    const [headers = [], ...rows] = sheetData;
    return {
      system: `You are analyzing spreadsheet data. Task: ${taskType}. Return JSON with key findings.`,
      data: { headers, rowCount: rows.length, sampleRows: rows.slice(0, 25) },
    };
  }
}
