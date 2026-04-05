# AIGoogleSheets

Browser-based Google Sheets integration with a Kimi-first multi-provider AI architecture, optional Google Drive persistence, and Apps Script sidebar support.

## What is implemented
- Browser OAuth with Google Identity Services (base Sheets scope + incremental Drive scope)
- Sheets read + append logging using `gapi`
- AI proxy endpoints:
  - `POST /api/ai/kimi`
  - `POST /api/ai/openai`
  - `POST /api/ai/analyze` (provider fallback)
- Drive session persistence endpoints:
  - `POST /api/drive/save`
  - `GET /api/drive/list`
- Security headers, request IDs, basic per-route/IP in-memory rate limiting
- Apps Script sidebar wrapper (`apps-script/`)
- GitHub Actions auto-deploy workflow (`.github/workflows/deploy.yml`)

## Local run
1. Create and activate a virtualenv.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set environment variables:
   ```bash
   export GOOGLE_CLIENT_ID="your-client-id"
   export KIMI_API_KEY="your-kimi-key"
   export OPENAI_API_KEY="your-openai-key"
   export ALLOWED_ORIGINS="http://localhost:8080"
   ```
4. Start app:
   ```bash
   python app.py
   ```
5. Open `http://localhost:8080`.

## Apps Script sidebar setup
1. Create a new bound Apps Script project from a Google Sheet.
2. Copy files from `apps-script/` into the script project.
3. Replace `YOUR_DEPLOYED_APP_URL` in `Sidebar.html`.
4. Reload the sheet and use **AI Assistant → Open Sidebar**.

## GitHub → Google Cloud auto deploy
The workflow uses Workload Identity Federation (OIDC) and deploys on pushes to `main`.

Set repository secrets:
- `GCP_PROJECT_ID`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`

Then push to `main`; workflow runs tests and deploys App Engine.

## Notes
- In-memory rate limit is fine for low traffic; use Redis/Memorystore for production.
- Store API keys in Secret Manager in production, not plain env vars.
