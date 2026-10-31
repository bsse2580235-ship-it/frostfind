# FrostFind – Find Water Before You Travel.

Water availability and freeze-risk platform for cold mountain regions (Gilgit-Baltistan, Pakistan).
Flask + SQLite backend, Bootstrap 5 / Leaflet / Chart.js frontend.

## Team
** Team Frostfind**
- Tanisha 
- Hans
- Archita 
- Duroop

## Setup
```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
export SECRET_KEY="long-random-string"             # required for real use
export OPENWEATHER_API_KEY="..."                   # optional; demo weather is used if unset
python app.py                                      # http://127.0.0.1:5000
```
Demo logins: `demo@frostfind.local / Demo@123` and `admin@frostfind.local / Admin@123`. Change or remove before any public deployment.

## Honesty and safety design
- All seeded sources are labelled **Demo data**; coordinates and statuses are invented for demonstration.
- Freeze risk is a **predicted** score from a transparent rule-based model (`risk()` in `app.py`) and is shown separately from **verified** status. Air temperature alone never marks water as frozen.
- Find Water lists only verified, accessible, recently reported sources, never frozen ones.
- Data older than 48 h is tagged Outdated. Offline mode shows cached data with a clear "not live" banner.
- Reports store only status, access, optional temperature, optional GPS and note, linked to the account. Passwords are hashed. No secrets in the repo.

## Technologies
Flask, SQLite, Werkzeug (password hashing), Bootstrap 5, Leaflet + OpenStreetMap tiles, Chart.js, Font Awesome, OpenWeatherMap API (optional).

## Credits
OpenStreetMap contributors, OpenWeatherMap, the libraries above. Built with AI assistance (Claude); the team can explain every part.

## Known limits
Photo upload, full service-worker offline map tiles and push notifications are not included yet. The ML model slot is `risk()`, ready to be replaced.
