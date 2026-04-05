# AIGoogleSheets

Browser-based Google Sheets integration with a Kimi-first multi-provider AI architecture, optional Google Drive persistence, and Apps Script sidebar support.

## What is implemented
- Browser OAuth with Google Identity Services (base Sheets scope + incremental Drive scope)
- Server-side Google bearer token verification with audience and scope enforcement for Drive APIs
- Sheets read + append logging using `gapi`
- AI proxy endpoints:
  - `POST /api/ai/kimi`
  - `POST /api/ai/openai`
  - `POST /api/ai/analyze` (provider fallback)
- Drive session persistence endpoints:
  - `POST /api/drive/save`
  - `GET /api/drive/list`
  - `GET /api/drive/restore`
- Versioned Drive session format with optimistic conflict handling
- Standardized backend error contracts plus centralized retry policy
- Structured observability endpoints and frontend event ingestion
- Redis-backed rate limiting when `REDIS_URL` is configured
- Apps Script sidebar bridge with context sync and result insertion
- Environment-aware secret lookup via Secret Manager resource references
- Production App Engine config (`app.prod.yaml`) and deployment rollback workflow

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
   export REDIS_URL="redis://..."
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
The workflow uses Workload Identity Federation (OIDC) and deploys production on pushes to `main` or via manual workflow dispatch.

Set repository secrets:
- `GCP_PROJECT_ID`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`

Then push to `main`; workflow runs tests and deploys App Engine.

## Notes
- Production should set `REDIS_URL` to Memorystore/Redis and use `*_SECRET_RESOURCE` env vars for API secrets.
- The deploy workflow runs compile checks, tests, environment-specific deploys, a smoke test, and traffic rollback on failure.
