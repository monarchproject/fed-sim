# Fed Chair Simulator - Render Deployment

This package is ready for Render as a Python Web Service.

## Files Render needs

- `main.py` - Flask backend, exposes `app`
- `index.html` - frontend served by Flask
- `macro_dataset_v3.json` and `irf_v3.json` - bundled cache so the app starts without waiting for FRED
- `requirements.txt` - Python dependencies
- `render.yaml` - optional Render Blueprint
- `Procfile` - fallback start command
- `.python-version` - pins Python 3.11.11

## Deploy steps

1. Create a new GitHub repository.
2. Upload all files from this folder to the repository root. Do not upload the outer zip folder as a nested subfolder.
3. In Render, click **New > Web Service** and connect the repo.
4. Use:
   - Runtime: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn main:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1`
5. Add environment variable if Render does not read `render.yaml` automatically:
   - `PYTHON_VERSION=3.11.11`
6. After deploy, open:
   - `/api/health` to confirm backend/data status
   - `/` to play the game

## Notes

- The app uses bundled data first for fast cold starts.
- `/api/refresh` will attempt a live FRED rebuild. On Render free instances this may take time, but the bundled cache remains available if refresh fails.
- The server must bind to `0.0.0.0:$PORT`; this package already does that through Gunicorn.
