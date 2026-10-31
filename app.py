"""FrostFind - water availability & freeze-risk platform (Flask + SQLite)."""
import os, math, json, sqlite3, urllib.request
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, g, request, jsonify, session, render_template, redirect
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
DB = os.path.join(os.path.dirname(__file__), "frostfind.db")
OWM_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
LOCS = {"Gilgit": (35.9208, 74.3080), "Hunza": (36.3167, 74.65), "Karimabad": (36.3250, 74.6700),
        "Skardu": (35.2970, 75.6370), "Nagar": (36.2610, 74.6270)}
DEMO_BASE = {"Gilgit": 4, "Hunza": 0, "Karimabad": 0, "Skardu": -2, "Nagar": -3}
STATUSES = {"available", "frozen", "partial", "unknown", "risk"}
ACCESS = {"accessible", "difficult", "inaccessible"}

def now(): return (datetime.utcnow() - timedelta(0)).isoformat(timespec="seconds")
def ago(h): return (datetime.utcnow() - timedelta(hours=h)).isoformat(timespec="seconds")

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db
@app.teardown_appcontext
def _close(e):
    d = g.pop("db", None)
    if d is not None: d.close()
def q(sql, a=(), one=False):
    r = [dict(x) for x in db().execute(sql, a).fetchall()]
    return (r[0] if r else None) if one else r
def run(sql, a=()):
    c = db().execute(sql, a); db().commit(); return c.lastrowid

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, pw TEXT, role TEXT DEFAULT 'user');
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, name TEXT, location TEXT, lat REAL, lng REAL, type TEXT,
  status TEXT, temp REAL, access TEXT, verified TEXT, updated TEXT, demo INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, user_id INTEGER, source_id INTEGER, status TEXT, temp REAL,
  access TEXT, lat REAL, lng REAL, note TEXT, created TEXT, state TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS saved(user_id INTEGER, source_id INTEGER, PRIMARY KEY(user_id, source_id));
"""
# DEMO records only: coordinates are approximate, statuses are invented for demonstration.
SEED = [
 ("Gilgit City Spring","Gilgit",35.9208,74.3080,"spring","available",6,"accessible","verified",3),
 ("Jutial Community Tap","Gilgit",35.9340,74.3350,"pipe","frozen",-2,"accessible","verified",5),
 ("Danyore Stream Point","Gilgit",35.9230,74.3700,"stream","partial",0,"difficult","unverified",20),
 ("Aliabad Public Tank","Hunza",36.3120,74.6170,"tank","available",3,"accessible","verified",8),
 ("Baltit Spring","Karimabad",36.3250,74.6700,"spring","risk",1,"difficult","verified",6),
 ("Altit Village Pipe","Karimabad",36.3290,74.6830,"pipe","frozen",-3,"accessible","unverified",30),
 ("Skardu Bazaar Tap","Skardu",35.2970,75.6370,"pipe","unknown",None,"accessible","unverified",96),
 ("Shigar Road Spring","Skardu",35.3380,75.7340,"spring","available",2,"accessible","verified",10),
 ("Hopar Glacier Stream","Nagar",36.2610,74.6270,"stream","frozen",-4,"difficult","verified",12),
 ("Nagar Khas Public Tap","Nagar",36.2700,74.6100,"pipe","available",1,"accessible","verified",7),
]
def init():
    with app.app_context():
        db().executescript(SCHEMA)
        if not q("SELECT 1 FROM users", one=True):
            run("INSERT INTO users(name,email,pw,role) VALUES(?,?,?,?)", ("Admin","admin@frostfind.local",generate_password_hash("Admin@123"),"admin"))
            run("INSERT INTO users(name,email,pw,role) VALUES(?,?,?,?)", ("Demo User","demo@frostfind.local",generate_password_hash("Demo@123"),"user"))
        if not q("SELECT 1 FROM sources", one=True):
            for s in SEED:
                run("INSERT INTO sources(name,location,lat,lng,type,status,temp,access,verified,updated,demo) VALUES(?,?,?,?,?,?,?,?,?,?,1)", s[:9] + (ago(s[9]),))

def me(): return q("SELECT id,name,email,role FROM users WHERE id=?", (session.get("uid"),), one=True) if session.get("uid") else None
def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not me(): return jsonify(error="Please log in"), 401
        return f(*a, **k)
    return w
def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        u = me()
        if not u or u["role"] != "admin": return jsonify(error="Admins only"), 403
        return f(*a, **k)
    return w

# ---------- pages ----------
@app.route("/")
def index():
    u = me()
    return render_template("index.html", user=u) if u else redirect("/login")
@app.route("/login", methods=["GET", "POST"])
def login():
    err = None
    if request.method == "POST":
        u = q("SELECT * FROM users WHERE email=?", (request.form["email"].strip().lower(),), one=True)
        if u and check_password_hash(u["pw"], request.form["password"]):
            session["uid"] = u["id"]; return redirect("/")
        err = "Email or password is incorrect."
    return render_template("auth.html", mode="login", err=err)
@app.route("/register", methods=["GET", "POST"])
def register():
    err = None
    if request.method == "POST":
        f = request.form; email = f["email"].strip().lower()
        if len(f["password"]) < 8: err = "Use a password with at least 8 characters."
        elif q("SELECT 1 FROM users WHERE email=?", (email,), one=True): err = "That email is already registered."
        else:
            session["uid"] = run("INSERT INTO users(name,email,pw) VALUES(?,?,?)", (f["name"].strip(), email, generate_password_hash(f["password"])))
            return redirect("/")
    return render_template("auth.html", mode="register", err=err)
@app.route("/logout")
def logout(): session.clear(); return redirect("/login")

# ---------- weather + risk ----------
def weather(loc):
    lat, lng = LOCS.get(loc, LOCS["Gilgit"])
    if OWM_KEY:
        try:
            u = f"https://api.openweathermap.org/data/2.5/%s?lat={lat}&lon={lng}&units=metric&appid={OWM_KEY}"
            cur = json.load(urllib.request.urlopen(u % "weather", timeout=6))
            fc = json.load(urllib.request.urlopen(u % "forecast", timeout=6))
            return {"source": "live", "loc": loc, "updated": now(),
                    "current": {"temp": cur["main"]["temp"], "feels": cur["main"]["feels_like"], "wind": cur["wind"]["speed"] * 3.6,
                                "humidity": cur["main"]["humidity"], "desc": cur["weather"][0]["description"]},
                    "series": [{"t": x["dt_txt"][5:16], "temp": x["main"]["temp"]} for x in fc["list"][:8]], "series_kind": "forecast"}
        except Exception:
            pass
    base = DEMO_BASE.get(loc, 0); h = datetime.utcnow().hour
    series = [{"t": f"{(h - 23 + i) % 24:02d}:00", "temp": round(base + 5 * math.sin((h - 23 + i - 9) / 24 * 2 * math.pi), 1)} for i in range(24)]
    c = series[-1]["temp"]
    return {"source": "demo", "loc": loc, "updated": now(),
            "current": {"temp": c, "feels": round(c - 3, 1), "wind": 14, "humidity": 48, "desc": "demo: clear sky"},
            "series": series, "series_kind": "history"}

def risk(src, w):
    """Transparent rule-based model v1 (score 0-100). Predicts RISK only; it never confirms ice."""
    t = w["current"]["temp"]; s = 0; why = []
    for lim, pts in ((-5, 40), (0, 30), (3, 15)):
        if t <= lim: s += pts; why.append(f"Air temperature {t:.1f}°C is at or below {lim}°C (+{pts})"); break
    d = w["series"][-1]["temp"] - w["series"][0]["temp"]
    if d <= -3: s += 10; why.append(f"Temperature trending down {d:.1f}°C (+10)")
    if w["current"]["wind"] > 20: s += 5; why.append("Strong wind (+5)")
    k = {"pipe": 15, "stream": 10, "tank": 8, "spring": 3}.get(src["type"], 5)
    s += k; why.append(f"Source type '{src['type']}' (+{k})")
    rec = q("SELECT COUNT(*) n FROM reports WHERE source_id=? AND status IN('frozen','partial') AND created>?", (src["id"], ago(72)), one=True)["n"]
    if rec: s += 20; why.append(f"{rec} community report(s) of ice in last 72h (+20)")
    if src["status"] == "frozen": s += 15; why.append("Last known status was frozen (+15)")
    s = min(s, 100)
    return {"level": "High" if s >= 60 else "Medium" if s >= 30 else "Low", "score": s, "trend": round(d, 1), "why": why,
            "confidence": "Low (demo weather)" if w["source"] == "demo" else "Medium (live weather, rule-based)",
            "model": "rule-based-v1", "updated": w["updated"], "verified_status": src["status"], "verified": src["verified"],
            "note": "Predicted risk only. Air temperature alone does not confirm frozen water."}

@app.get("/api/weather")
@login_required
def api_weather(): return jsonify(weather(request.args.get("loc", "Gilgit")))
@app.get("/api/risk/<int:sid>")
@login_required
def api_risk(sid):
    s = q("SELECT * FROM sources WHERE id=?", (sid,), one=True)
    return (jsonify(risk(s, weather(s["location"]))) if s else (jsonify(error="Not found"), 404))

# ---------- sources / reports ----------
@app.get("/api/me")
@login_required
def api_me(): return jsonify(me())
@app.put("/api/me")
@login_required
def api_me_put():
    run("UPDATE users SET name=? WHERE id=?", (request.json.get("name", "").strip()[:60] or me()["name"], session["uid"])); return jsonify(me())
@app.get("/api/sources")
@login_required
def api_sources(): return jsonify(q("SELECT * FROM sources ORDER BY name"))
@app.post("/api/sources")
@admin_required
def api_src_add():
    d = request.json
    i = run("INSERT INTO sources(name,location,lat,lng,type,status,temp,access,verified,updated,demo) VALUES(?,?,?,?,?,?,?,?,?,?,0)",
            (d["name"], d["location"], float(d["lat"]), float(d["lng"]), d.get("type", "pipe"), d.get("status", "unknown"), None, d.get("access", "accessible"), "verified", now()))
    return jsonify(id=i)
@app.put("/api/sources/<int:sid>")
@admin_required
def api_src_edit(sid):
    d = request.json
    for k in ("name", "location", "type", "status", "access"):
        if k in d: run(f"UPDATE sources SET {k}=?, updated=? WHERE id=?", (d[k], now(), sid))
    return jsonify(ok=True)
@app.delete("/api/sources/<int:sid>")
@admin_required
def api_src_del(sid):
    run("DELETE FROM sources WHERE id=?", (sid,)); run("DELETE FROM reports WHERE source_id=?", (sid,)); return jsonify(ok=True)
@app.post("/api/reports")
@login_required
def api_report():
    d = request.json; s = q("SELECT * FROM sources WHERE id=?", (d.get("source_id"),), one=True)
    if not s: return jsonify(error="Choose a water source"), 400
    if d.get("status") not in STATUSES - {"risk"} or d.get("access") not in ACCESS: return jsonify(error="Invalid status or access"), 400
    temp = float(d["temp"]) if str(d.get("temp", "")).strip() != "" else None
    adm = me()["role"] == "admin"; t = now()
    run("INSERT INTO reports(user_id,source_id,status,temp,access,lat,lng,note,created,state) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (session["uid"], s["id"], d["status"], temp, d["access"], d.get("lat") or None, d.get("lng") or None, (d.get("note") or "")[:500], t, "verified" if adm else "pending"))
    run("UPDATE sources SET status=?, temp=COALESCE(?,temp), access=?, updated=?, verified=? WHERE id=?", (d["status"], temp, d["access"], t, "verified" if adm else "community", s["id"]))
    return jsonify(ok=True)
@app.get("/api/reports")
@login_required
def api_reports():
    sql = "SELECT r.*, s.name sname, s.location, u.name uname FROM reports r JOIN sources s ON s.id=r.source_id JOIN users u ON u.id=r.user_id "
    if request.args.get("all") and me()["role"] == "admin": return jsonify(q(sql + "ORDER BY r.created DESC"))
    return jsonify(q(sql + "WHERE r.user_id=? ORDER BY r.created DESC", (session["uid"],)))
@app.post("/api/reports/<int:rid>/<act>")
@admin_required
def api_rep_act(rid, act):
    r = q("SELECT * FROM reports WHERE id=?", (rid,), one=True)
    if not r or act not in ("verify", "reject"): return jsonify(error="Bad request"), 400
    run("UPDATE reports SET state=? WHERE id=?", ("verified" if act == "verify" else "rejected", rid))
    run("UPDATE sources SET verified=? WHERE id=?", ("verified" if act == "verify" else "unverified", r["source_id"])); return jsonify(ok=True)
@app.route("/api/saved", methods=["GET", "POST", "DELETE"])
@login_required
def api_saved():
    u = session["uid"]
    if request.method == "POST": run("INSERT OR IGNORE INTO saved VALUES(?,?)", (u, request.json["id"]))
    if request.method == "DELETE": run("DELETE FROM saved WHERE user_id=? AND source_id=?", (u, request.json["id"]))
    return jsonify([x["source_id"] for x in q("SELECT source_id FROM saved WHERE user_id=?", (u,))])
@app.get("/api/users")
@admin_required
def api_users(): return jsonify(q("SELECT id,name,email,role FROM users"))
@app.delete("/api/users/<int:uid>")
@admin_required
def api_user_del(uid):
    if uid == session["uid"]: return jsonify(error="You cannot delete yourself"), 400
    run("DELETE FROM users WHERE id=?", (uid,)); return jsonify(ok=True)
@app.get("/api/notifications")
@login_required
def api_notifs():
    out = []
    for r in q("SELECT r.status,r.created,s.name,s.location FROM reports r JOIN sources s ON s.id=r.source_id WHERE r.created>? ORDER BY r.created DESC LIMIT 15", (ago(72),)):
        if r["status"] == "frozen": out.append({"kind": "frozen", "text": f"{r['name']} ({r['location']}) was reported frozen", "t": r["created"]})
        elif r["status"] == "available": out.append({"kind": "available", "text": f"{r['name']} ({r['location']}) was reported available", "t": r["created"]})
    for s in q("SELECT * FROM sources WHERE updated<?", (ago(48),)):
        out.append({"kind": "stale", "text": f"{s['name']} has no report for over 48 hours", "t": s["updated"]})
    for loc in LOCS:
        w = weather(loc)
        if w["current"]["temp"] <= -3: out.append({"kind": "risk", "text": f"High freezing risk in {loc} ({w['current']['temp']:.0f}°C, {w['source']} weather)", "t": w["updated"]})
    return jsonify(sorted(out, key=lambda x: x["t"], reverse=True))
@app.get("/api/analytics")
@login_required
def api_analytics():
    days = min(int(request.args.get("days", 7)), 60)
    by_day = {(datetime.utcnow() - timedelta(days=i)).strftime("%m-%d"): 0 for i in range(days - 1, -1, -1)}
    for r in q("SELECT created FROM reports WHERE created>?", (ago(24 * days),)):
        k = r["created"][5:10]
        if k in by_day: by_day[k] += 1
    return jsonify(by_day=by_day, by_status=q("SELECT status k, COUNT(*) n FROM sources GROUP BY status"),
                   by_location=q("SELECT location k, SUM(status='available') a, SUM(status='frozen') f, COUNT(*) n FROM sources GROUP BY location"),
                   top=q("SELECT s.location k, COUNT(*) n FROM reports r JOIN sources s ON s.id=r.source_id GROUP BY s.location ORDER BY n DESC LIMIT 5"),
                   report_states=q("SELECT state k, COUNT(*) n FROM reports GROUP BY state"))

init()
if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5000)
