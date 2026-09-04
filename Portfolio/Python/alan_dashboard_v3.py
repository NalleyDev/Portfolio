from flask import Flask, render_template_string, redirect, url_for, request, session, jsonify
import paramiko
import requests
import time
import uuid
import os
import json
import sqlite3
from contextlib import contextmanager
from threading import Lock, Thread
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

try:
    import globalnoc.wsc
    HAS_GNOC = True
except ImportError:
    HAS_GNOC = False

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'CHANGE_ME_SET_FLASK_SECRET_KEY')

GN_HOST  = os.getenv('GN_HOST', 'https://CHANGE_ME.example.com/cds2/')
GN_USER  = os.getenv('GN_USER', 'CHANGE_ME@EXAMPLE.COM')
GN_PW    = os.getenv('GN_PW', '')
GN_REALM = os.getenv('GN_REALM', 'https://idp.example.com/idp/profile/SAML2/SOAP/ECP')
NODE_TYPES     = [t for t in os.getenv('NODE_TYPES', '').split(',') if t]
NODE_TAG_TYPE  = os.getenv('NODE_TAG_TYPE',  'CHANGE_ME_TAG_NAME')
NODE_TAG_VALUE = os.getenv('NODE_TAG_VALUE', 'Yes')

USERS_FILE = os.getenv('USERS_FILE', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.json'))
DB_FILE    = os.getenv('DB_FILE',    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'patch_dashboard.db'))
DEFAULT_ADMIN_USER = os.getenv('ADMIN_USER', 'admin')
DEFAULT_ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'changeme')

SSH_CONNECT_TIMEOUT = 5
SSH_COMMAND_TIMEOUT = 30
CACHE_TTL      = int(os.getenv('CACHE_TTL',      300))
HOST_CACHE_TTL = int(os.getenv('HOST_CACHE_TTL', 600))

_patch_jobs = {}
_patch_jobs_lock = Lock()

_users_lock = Lock()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

@contextmanager
def _db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS update_cache (
                host        TEXT NOT NULL,
                username    TEXT NOT NULL,
                timestamp   REAL NOT NULL,
                has_updates INTEGER,
                update_list TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (host, username)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS pkgmgr_cache (
                host   TEXT PRIMARY KEY,
                pkgmgr TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS manual_hosts (
                host TEXT PRIMARY KEY,
                name TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS api_host_cache (
                host      TEXT PRIMARY KEY,
                name      TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        ''')

_DETECT_PKGMGR = (
    'if command -v dnf &>/dev/null; then echo dnf; '
    'elif command -v yum &>/dev/null; then echo yum; '
    'elif command -v apt-get &>/dev/null; then echo apt; '
    'else echo unknown; fi'
)
_CHECK_CMD = {
    'dnf': 'sudo -S dnf check-update',
    'yum': 'sudo -S yum check-update',
    'apt': 'sudo -S apt-get update -qq 2>/dev/null; apt list --upgradable 2>/dev/null | grep -v "^Listing"',
}
_UPGRADE_CMD = {
    'dnf': 'sudo -S dnf -y upgrade',
    'yum': 'sudo -S yum -y update',
    'apt': 'sudo -S apt-get -y upgrade',
}


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE) as f:
        content = f.read().strip()
    if not content:
        return {}
    return json.loads(content)


def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)


def init_users():
    with _users_lock:
        users = load_users()
        if not users:
            users[DEFAULT_ADMIN_USER] = {
                'password_hash': generate_password_hash(DEFAULT_ADMIN_PASSWORD),
                'is_admin': True,
            }
            save_users(users)
            print(f"[init] Created default admin user '{DEFAULT_ADMIN_USER}'. Change the password after first login.")


def verify_user(username, password):
    with _users_lock:
        users = load_users()
    user = users.get(username)
    if not user:
        return False, False
    ok = check_password_hash(user['password_hash'], password)
    return ok, user.get('is_admin', False)


# ---------------------------------------------------------------------------
# Auth decorators
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('app_user'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def app_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('app_user'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('app_user'):
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            return 'Forbidden', 403
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Host / SSH helpers (unchanged)
# ---------------------------------------------------------------------------

def get_srv_hosts():
    if not HAS_GNOC:
        return {}
    now = time.time()
    with _db() as conn:
        rows = conn.execute('SELECT host, name, timestamp FROM api_host_cache').fetchall()
    if rows and (now - rows[0]['timestamp']) < HOST_CACHE_TTL:
        return {r['host']: r['name'] for r in rows}
    params = [('method', 'get_nodes')]
    for t in NODE_TYPES:
        params.append(('node_role_id', t))
    try:
        r = requests.get(
                GN_HOST + 'node.cgi',
                params=params,
                auth=globalnoc.wsc.ECP(GN_USER, GN_PW, GN_REALM),
                timeout=15
            )
        if r.status_code != 200:
            print(f"API Error: {r.status_code}")
            return {r['host']: r['name'] for r in rows}
        parsed = r.json()
        if 'error' in parsed:
            print(f"GRNOC API error: {parsed['error_text']}")
            return {r['host']: r['name'] for r in rows}
        nodes = parsed.get('results', [])
        print(f"[API] {len(nodes)} total nodes returned")
        hosts = {}
        for node in nodes:
            if node.get('status') == 'decom' or not node.get('management_address'):
                continue
            kvps = node.get('node_kvps') or []
            match = any(k.get('node_kvp_name') == NODE_TAG_TYPE and k.get('node_kvp_value') == NODE_TAG_VALUE for k in kvps)
            if not match and kvps:
                print(f"[API] node {node.get('name')} kvps: {kvps[:2]}")
            if match:
                hosts[node['management_address']] = node.get('name') or ''
        print(f"[API] {len(hosts)} nodes matched tag {NODE_TAG_TYPE}={NODE_TAG_VALUE}")
        with _db() as conn:
            conn.execute('DELETE FROM api_host_cache')
            conn.executemany(
                'INSERT INTO api_host_cache (host, name, timestamp) VALUES (?,?,?)',
                [(h, n, now) for h, n in hosts.items()]
            )
        return hosts
    except Exception as e:
        print(f"API Exception: {e}")
        return {r['host']: r['name'] for r in rows}


def get_hosts():
    api_hosts = get_srv_hosts()
    with _db() as conn:
        rows = conn.execute('SELECT host, name FROM manual_hosts').fetchall()
    manual_hosts = {r['host']: r['name'] for r in rows}
    combined_names = {**manual_hosts, **api_hosts}
    combined_hosts = list(api_hosts.keys()) + [h for h in manual_hosts if h not in api_hosts]
    return combined_hosts, combined_names


def check_updates(host, username, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=username, password=password, timeout=SSH_CONNECT_TIMEOUT)

        with _db() as conn:
            row = conn.execute('SELECT pkgmgr FROM pkgmgr_cache WHERE host=?', (host,)).fetchone()
            pkgmgr = row['pkgmgr'] if row else None
        if not pkgmgr:
            _, out, _ = client.exec_command(_DETECT_PKGMGR)
            pkgmgr = out.read().decode().strip()
            if pkgmgr not in ('dnf', 'yum', 'apt'):
                pkgmgr = 'dnf'
            with _db() as conn:
                conn.execute('INSERT OR REPLACE INTO pkgmgr_cache (host, pkgmgr) VALUES (?,?)', (host, pkgmgr))

        stdin, stdout, stderr = client.exec_command(_CHECK_CMD[pkgmgr], timeout=SSH_COMMAND_TIMEOUT)
        stdin.write(password + '\n')
        stdin.flush()
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode()

        if pkgmgr in ('dnf', 'yum'):
            if exit_status == 0:
                return False, ""
            elif exit_status == 100:
                return True, output.strip()
            else:
                return None, f"Error: {pkgmgr} exited with code {exit_status}"
        else:
            if exit_status != 0:
                return None, f"Error: apt exited with code {exit_status}"
            upgradable = [l for l in output.splitlines() if l and not l.startswith('Listing')]
            return (True, '\n'.join(upgradable)) if upgradable else (False, "")
    except Exception as e:
        return None, f"Error: {e}"
    finally:
        client.close()


def check_updates_cached(host, username, password):
    now = time.time()
    with _db() as conn:
        row = conn.execute(
            'SELECT timestamp, has_updates, update_list FROM update_cache WHERE host=? AND username=?',
            (host, username)
        ).fetchone()
    if row and (now - row['timestamp']) < CACHE_TTL:
        return (None if row['has_updates'] is None else bool(row['has_updates'])), row['update_list']
    result = check_updates(host, username, password)
    has_updates, update_list = result
    with _db() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO update_cache (host, username, timestamp, has_updates, update_list) VALUES (?,?,?,?,?)',
            (host, username, now, None if has_updates is None else int(has_updates), update_list or '')
        )
    return result


def invalidate_cache(host, username):
    with _db() as conn:
        conn.execute('DELETE FROM update_cache WHERE host=? AND username=?', (host, username))


def ssh_run_command(host, username, password, command):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=username, password=password)
        stdin, stdout, stderr = client.exec_command(command, timeout=120)
        stdin.write(password + '\n')
        stdin.flush()
        output = stdout.read().decode()
        error = stderr.read().decode()
        exit_status = stdout.channel.recv_exit_status()
    except Exception as e:
        output = ""
        error = str(e)
        exit_status = -1
    finally:
        client.close()
    return output, error, exit_status


# ---------------------------------------------------------------------------
# Routes — SSH / patching
# ---------------------------------------------------------------------------

@app.route("/host-status/<path:host>")
@login_required
def host_status(host):
    username = session.get("username")
    password = session.get("password")
    if not username or not password:
        return jsonify({"status": "no_creds"})
    status, update_list = check_updates_cached(host, username, password)
    with _db() as conn:
        row = conn.execute('SELECT pkgmgr FROM pkgmgr_cache WHERE host=?', (host,)).fetchone()
    pkgmgr = row['pkgmgr'] if row else ''
    return jsonify({"status": status, "updates": update_list, "pkgmgr": pkgmgr})


@app.route("/add-host", methods=["POST"])
@login_required
def add_host():
    new_host = request.form.get("new_host", "").strip()
    new_name = request.form.get("new_name", "").strip()
    if new_host:
        with _db() as conn:
            conn.execute('INSERT OR IGNORE INTO manual_hosts (host, name) VALUES (?,?)', (new_host, new_name or new_host))
    return redirect(url_for("dashboard"))


@app.route("/remove-host/<path:host>")
@login_required
def remove_host(host):
    with _db() as conn:
        conn.execute('DELETE FROM manual_hosts WHERE host=?', (host,))
    invalidate_cache(host, session.get("username"))
    return redirect(url_for("dashboard"))


@app.route("/refresh-host/<path:host>")
@login_required
def refresh_host(host):
    invalidate_cache(host, session.get("username"))
    return jsonify({"ok": True})


@app.route("/refresh-all")
@login_required
def refresh_all():
    with _db() as conn:
        conn.execute('DELETE FROM update_cache')
    return redirect(url_for("dashboard"))


@app.route("/refresh-all-cache")
@login_required
def refresh_all_cache():
    with _db() as conn:
        conn.execute('DELETE FROM update_cache')
    return jsonify({"ok": True})


@app.route("/refresh-api-cache")
@login_required
def refresh_api_cache():
    with _db() as conn:
        conn.execute('DELETE FROM api_host_cache')
    return jsonify({"ok": True})


@app.route("/patch-host/<path:host>", methods=["POST"])
@login_required
def patch_host(host):
    username = session.get("username")
    password = session.get("password")
    if not username or not password:
        return jsonify({"error": "No SSH credentials set."}), 400

    job_id = str(uuid.uuid4())
    with _patch_jobs_lock:
        _patch_jobs[job_id] = {"status": "running", "output": "", "error": ""}

    def run_patch():
        with _db() as conn:
            row = conn.execute('SELECT pkgmgr FROM pkgmgr_cache WHERE host=?', (host,)).fetchone()
        pkgmgr = row['pkgmgr'] if row else 'dnf'
        output, error, exit_status = ssh_run_command(host, username, password, _UPGRADE_CMD[pkgmgr])
        invalidate_cache(host, username)
        with _patch_jobs_lock:
            _patch_jobs[job_id] = {
                "status": "done" if exit_status == 0 else "error",
                "output": output,
                "error": error,
            }

    Thread(target=run_patch, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/patch-status/<job_id>")
@login_required
def patch_status(job_id):
    with _patch_jobs_lock:
        job = _patch_jobs.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ok, is_admin = verify_user(username, password)
        if ok:
            session["app_user"] = username
            session["is_admin"] = is_admin
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."
    return render_template_string(LOGIN_TEMPLATE, error=error)


@app.route("/ssh-creds", methods=["GET", "POST"])
@app_login_required
def ssh_creds():
    error = None
    if request.method == "POST":
        ssh_user = request.form.get("ssh_username", "").strip()
        ssh_pass = request.form.get("ssh_password", "")
        if ssh_user and ssh_pass:
            session["username"] = ssh_user
            session["password"] = ssh_pass
            return redirect(url_for("dashboard"))
        error = "Both SSH username and password are required."
    return render_template_string(SSH_CREDS_TEMPLATE, error=error, app_user=session.get("app_user"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Routes — Admin
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin():
    with _users_lock:
        users = load_users()
    return render_template_string(ADMIN_TEMPLATE, users=users, app_user=session.get("app_user"))


@app.route("/admin/add-user", methods=["POST"])
@admin_required
def admin_add_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    is_admin = request.form.get("is_admin") == "1"
    error = None
    if not username or not password:
        error = "Username and password are required."
    else:
        with _users_lock:
            users = load_users()
            if username in users:
                error = f"User '{username}' already exists."
            else:
                users[username] = {
                    'password_hash': generate_password_hash(password),
                    'is_admin': is_admin,
                }
                save_users(users)
    if error:
        with _users_lock:
            users = load_users()
        return render_template_string(ADMIN_TEMPLATE, users=users, app_user=session.get("app_user"), error=error)
    return redirect(url_for("admin"))


@app.route("/admin/remove-user/<username>")
@admin_required
def admin_remove_user(username):
    if username == session.get("app_user"):
        with _users_lock:
            users = load_users()
        return render_template_string(ADMIN_TEMPLATE, users=users, app_user=session.get("app_user"),
                                      error="You cannot remove your own account.")
    with _users_lock:
        users = load_users()
        users.pop(username, None)
        save_users(users)
    return redirect(url_for("admin"))


@app.route("/admin/reset-password/<username>", methods=["POST"])
@admin_required
def admin_reset_password(username):
    new_password = request.form.get("new_password", "")
    error = None
    if not new_password:
        error = "New password cannot be empty."
    else:
        with _users_lock:
            users = load_users()
            if username not in users:
                error = f"User '{username}' not found."
            else:
                users[username]['password_hash'] = generate_password_hash(new_password)
                save_users(users)
    if error:
        with _users_lock:
            users = load_users()
        return render_template_string(ADMIN_TEMPLATE, users=users, app_user=session.get("app_user"), error=error)
    return redirect(url_for("admin"))


@app.route("/admin/toggle-admin/<username>")
@admin_required
def admin_toggle_admin(username):
    if username == session.get("app_user"):
        with _users_lock:
            users = load_users()
        return render_template_string(ADMIN_TEMPLATE, users=users, app_user=session.get("app_user"),
                                      error="You cannot change your own admin status.")
    with _users_lock:
        users = load_users()
        if username in users:
            users[username]['is_admin'] = not users[username].get('is_admin', False)
            save_users(users)
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Routes — Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    hosts, host_names = get_hosts()
    has_ssh = bool(session.get("username") and session.get("password"))
    return render_template_string(DASHBOARD_TEMPLATE, hosts=hosts, host_names=host_names,
                                  app_user=session.get("app_user"),
                                  is_admin=session.get("is_admin", False),
                                  has_ssh=has_ssh)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_BASE_STYLE = """
<style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 20px; background: #0d1117; color: #c9d1d9; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 30px; max-width: 420px; margin: 80px auto; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }
    h2 { margin-top: 0; margin-bottom: 20px; color: #e6edf3; }
    label { display: block; margin-bottom: 4px; font-size: 0.9em; font-weight: 600; color: #8b949e; }
    input[type=text], input[type=password] { width: 100%; padding: 9px 12px; border: 1px solid #30363d; border-radius: 6px; font-size: 1em; margin-bottom: 14px; background: #0d1117; color: #c9d1d9; }
    input[type=text]:focus, input[type=password]:focus { outline: none; border-color: #58a6ff; }
    button[type=submit] { width: 100%; padding: 10px; background: #238636; color: #fff; border: none; border-radius: 6px; font-size: 1em; cursor: pointer; }
    button[type=submit]:hover { background: #2ea043; }
    .error { background: #3d1a1a; color: #f85149; border: 1px solid #6e2c2c; padding: 10px 14px; border-radius: 6px; margin-bottom: 14px; font-size: 0.9em; }
    .muted { font-size: 0.85em; color: #8b949e; margin-top: 14px; text-align: center; }
    a { color: #58a6ff; }
</style>
"""

LOGIN_TEMPLATE = """
<html>
<head><title>Login — ALAN Dash</title>""" + _BASE_STYLE + """</head>
<body>
    <div class="card">
        <h2>ALAN Dash — Login</h2>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="post">
            <label>Username</label>
            <input type="text" name="username" autofocus required>
            <label>Password</label>
            <input type="password" name="password" required>
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>
"""

SSH_CREDS_TEMPLATE = """
<html>
<head><title>SSH Credentials — ALAN Dash</title>""" + _BASE_STYLE + """</head>
<body>
    <div class="card">
        <h2>SSH Credentials</h2>
        <p style="color:#555;font-size:0.95em;margin-top:-10px;margin-bottom:18px;">
            Signed in as <strong>{{ app_user }}</strong>.<br>
            Enter the SSH credentials used to connect to managed hosts.
        </p>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="post">
            <label>SSH Username</label>
            <input type="text" name="ssh_username" autofocus required>
            <label>SSH Password</label>
            <input type="password" name="ssh_password" required>
            <button type="submit">Continue to Dashboard</button>
        </form>
        <p class="muted"><a href="{{ url_for('logout') }}">Sign out</a></p>
    </div>
</body>
</html>
"""

ADMIN_TEMPLATE = """
<html>
<head>
<title>Admin — ALAN Dash</title>
<style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 30px; background: #0d1117; color: #c9d1d9; }
    h1 { margin-bottom: 4px; color: #e6edf3; }
    .subnav { margin-bottom: 24px; font-size: 0.9em; }
    .subnav a { margin-right: 16px; color: #58a6ff; text-decoration: none; }
    table { border-collapse: collapse; width: 100%; max-width: 700px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
    th { background: #21262d; text-align: left; padding: 10px 14px; font-size: 0.85em; color: #8b949e; border-bottom: 1px solid #30363d; }
    td { padding: 10px 14px; border-top: 1px solid #21262d; font-size: 0.95em; vertical-align: middle; color: #c9d1d9; }
    .badge { font-size: 0.75em; padding: 2px 8px; border-radius: 20px; font-weight: 600; }
    .badge-admin { background: #1f3a5f; color: #58a6ff; }
    .badge-user  { background: #21262d; color: #8b949e; }
    .btn { padding: 5px 10px; border: none; border-radius: 5px; cursor: pointer; font-size: 0.85em; }
    .btn-danger  { background: #3d1a1a; color: #f85149; }
    .btn-neutral { background: #21262d; color: #c9d1d9; }
    .btn-primary { background: #238636; color: #fff; }
    .btn:hover { filter: brightness(1.2); }
    .add-form { margin-top: 28px; max-width: 700px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px 24px; }
    .add-form h3 { margin-top: 0; color: #e6edf3; }
    .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
    .row input[type=text], .row input[type=password] { flex: 1; min-width: 140px; padding: 8px 10px; border: 1px solid #30363d; border-radius: 6px; font-size: 0.95em; background: #0d1117; color: #c9d1d9; }
    .row input:focus { outline: none; border-color: #58a6ff; }
    .row label.chk { display: flex; align-items: center; gap: 6px; font-size: 0.9em; white-space: nowrap; color: #8b949e; }
    td input[type=password] { padding: 4px 8px; border: 1px solid #30363d; border-radius: 5px; font-size: 0.85em; width: 140px; background: #0d1117; color: #c9d1d9; }
    .error { background: #3d1a1a; color: #f85149; border: 1px solid #6e2c2c; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; font-size: 0.9em; }
</style>
</head>
<body>
    <h1>User Management</h1>
    <div class="subnav">
        <a href="{{ url_for('dashboard') }}">← Dashboard</a>
        <a href="{{ url_for('logout') }}">Logout</a>
    </div>

    {% if error %}<div class="error" style="max-width:700px;">{{ error }}</div>{% endif %}

    <table>
        <thead>
            <tr><th>Username</th><th>Role</th><th>Actions</th></tr>
        </thead>
        <tbody>
        {% for username, info in users.items() %}
            <tr>
                <td>{{ username }}{% if username == app_user %} <em style="color:#888;font-size:0.8em;">(you)</em>{% endif %}</td>
                <td>
                    <span class="badge {% if info.is_admin %}badge-admin{% else %}badge-user{% endif %}">
                        {{ 'Admin' if info.is_admin else 'User' }}
                    </span>
                </td>
                <td style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
                    <form method="post" action="{{ url_for('admin_reset_password', username=username) }}" style="display:flex;gap:6px;align-items:center;">
                        <input type="password" name="new_password" placeholder="New password" style="padding:4px 8px;border:1px solid #ccc;border-radius:5px;font-size:0.85em;width:140px;">
                        <button class="btn btn-neutral" type="submit">Reset</button>
                    </form>
                    {% if username != app_user %}
                    <a href="{{ url_for('admin_toggle_admin', username=username) }}">
                        <button class="btn btn-neutral" type="button">{{ 'Demote' if info.is_admin else 'Make Admin' }}</button>
                    </a>
                    <a href="{{ url_for('admin_remove_user', username=username) }}"
                       onclick="return confirm('Remove user {{ username }}?')">
                        <button class="btn btn-danger" type="button">Remove</button>
                    </a>
                    {% endif %}
                </td>
            </tr>
        {% endfor %}
        </tbody>
    </table>

    <div class="add-form">
        <h3>Add User</h3>
        <form method="post" action="{{ url_for('admin_add_user') }}">
            <div class="row">
                <input type="text" name="username" placeholder="Username" required>
                <input type="password" name="password" placeholder="Password" required>
                <label class="chk"><input type="checkbox" name="is_admin" value="1"> Admin</label>
                <button class="btn btn-primary" type="submit">Add</button>
            </div>
        </form>
    </div>
</body>
</html>
"""

DASHBOARD_TEMPLATE = """
<html>
<head>
    <title>Patch Status Dashboard</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 20px; background: #0d1117; color: #c9d1d9; }
        h1 { margin-bottom: 10px; color: #e6edf3; }
        .toolbar { margin-bottom: 20px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
        .toolbar a { color: #58a6ff; text-decoration: none; font-size: 0.9em; }
        .toolbar a:hover { text-decoration: underline; }
        .toolbar .divider { color: #30363d; }
        .toolbar .whoami { font-size: 0.9em; color: #8b949e; }
        .toolbar button { background: #238636; color: #fff; border: none; border-radius: 6px; padding: 5px 14px; font-size: 0.9em; cursor: pointer; }
        .toolbar button:hover { background: #2ea043; }
        .toolbar button:disabled { background: #21262d; color: #484f58; cursor: default; }
        .host { font-size: 1em; margin: 8px; padding: 14px 16px; border-radius: 10px; display: inline-block; width: 360px; vertical-align: top; border: 1px solid #30363d; background: #161b22; }
        .green  { background: #0d2818; border-color: #238636; color: #3fb950; }
        .red    { background: #2d1515; border-color: #6e2c2c; color: #f85149; }
        .gray   { background: #161b22; border-color: #30363d; color: #484f58; }
        .loading { background: #111d2e; border-color: #1f3a5f; color: #58a6ff; }
        .host-name { font-size: 1.1em; font-weight: 600; color: #e6edf3; }
        .host-meta { font-size: 0.8em; color: #8b949e; margin-top: 2px; font-family: ui-monospace, monospace; }
        .host-os { color: #58a6ff; }
        .updates-list { max-height: 200px; overflow: auto; background: #0d1117; border: 1px solid #30363d; border-radius: 5px; padding: 6px; margin-top: 6px; }
        .updates-list ul { padding-left: 16px; margin: 0; }
        .updates-list li { font-family: ui-monospace, monospace; font-size: 0.82em; color: #c9d1d9; padding: 1px 0; }
        .pkg-name { font-weight: 600; color: #e6edf3; }
        .pkg-rest { color: #8b949e; }
        .host-actions { margin-top: 10px; }
        .host-actions a { font-size: 0.8em; margin-right: 10px; cursor: pointer; color: #58a6ff; }
        .patch-btn { background: #b45309; color: #fff; border: none; border-radius: 5px; padding: 4px 12px; font-size: 0.85em; cursor: pointer; margin-bottom: 4px; }
        .patch-btn:hover { background: #d97706; }
        .patch-btn:disabled { background: #21262d; color: #484f58; cursor: default; }
        .stats { background: #161b22; border: 1px solid #30363d; padding: 10px 16px; border-radius: 6px; display: inline-block; margin-bottom: 16px; font-size: 0.9em; }
        .add-form { margin-bottom: 20px; margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; }
        .add-form input { padding: 7px 10px; border: 1px solid #30363d; border-radius: 6px; font-size: 0.9em; background: #0d1117; color: #c9d1d9; }
        .add-form input:focus { outline: none; border-color: #58a6ff; }
        .add-form button { padding: 7px 14px; background: #21262d; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; font-size: 0.9em; cursor: pointer; }
        .add-form button:hover { background: #30363d; }
        .progress-wrap { margin-top: 8px; background: #21262d; border-radius: 4px; height: 6px; overflow: hidden; display: none; }
        .progress-bar { height: 100%; background: #238636; border-radius: 4px; animation: indeterminate 1.2s linear infinite; }
        @keyframes indeterminate { 0% { width: 20%; margin-left: -20%; } 100% { width: 20%; margin-left: 100%; } }
        .patch-result { margin-top: 6px; font-size: 0.82em; display: none; }
        .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid #1f3a5f; border-top-color: #58a6ff; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 6px; }
        @keyframes spin { to { transform: rotate(360deg); } }
        #host-grid { margin-top: 4px; }
    </style>
</head>
<body>
    <h1>ALAN Dash</h1>

    <div class="toolbar">
        <span class="whoami">Signed in as <strong style="color:#c9d1d9;">{{ app_user }}</strong></span>
        <span class="divider">|</span>
        {% if is_admin %}<a href="{{ url_for('admin') }}">⚙ Admin</a>{% endif %}
        <a href="{{ url_for('ssh_creds') }}">🔑 SSH Creds</a>
        <a href="{{ url_for('refresh_all') }}">↺ Refresh All</a>
        <a href="{{ url_for('logout') }}">Logout</a>
        <button id="patch-all-btn" onclick="patchAll()" disabled>⚡ Patch All</button>
    </div>

    {% if not has_ssh %}
    <div style="background:#2d2a00;border:1px solid #6e5a00;color:#e3b341;padding:10px 16px;border-radius:6px;margin-bottom:16px;font-size:0.9em;">
        SSH credentials not set — host checks are disabled.
        <a href="{{ url_for('ssh_creds') }}" style="color:#e3b341;font-weight:600;margin-left:8px;">Set SSH credentials →</a>
    </div>
    {% endif %}

    <div id="global-progress-wrap" style="display:none; margin-bottom:16px;">
        <div style="margin-bottom:6px;font-size:0.85em;color:#8b949e;" id="global-progress-label">Patching 0 / 0 hosts...</div>
        <div style="background:#21262d;border-radius:4px;height:8px;overflow:hidden;width:100%;max-width:480px;">
            <div id="global-progress-bar" style="height:100%;background:#238636;border-radius:4px;width:0%;transition:width 0.3s ease;"></div>
        </div>
    </div>

    <div class="stats" id="stats">
        <strong id="stat-total" style="color:#e6edf3;">{{ hosts|length }}</strong> hosts &nbsp;|&nbsp;
        <span style="color:#3fb950"><span id="stat-ok">0</span> up to date</span> &nbsp;|&nbsp;
        <span style="color:#f85149"><span id="stat-needs">0</span> need updates</span> &nbsp;|&nbsp;
        <span style="color:#484f58"><span id="stat-err">0</span> errors</span> &nbsp;|&nbsp;
        <span style="color:#58a6ff"><span id="stat-pending">{{ hosts|length }}</span> checking...</span>
        <span id="stat-nocreds-wrap" style="display:none"> &nbsp;|&nbsp; <span style="color:#e3b341"><span id="stat-nocreds">0</span> need SSH creds</span></span>
    </div>

    <form method="post" action="{{ url_for('add_host') }}" class="add-form">
        <input type="text" name="new_host" placeholder="Host IP" required>
        <input type="text" name="new_name" placeholder="Name (optional)">
        <button type="submit">+ Add Host</button>
    </form>

    <div id="host-grid">
    {% for h in hosts %}
        <div class="host loading" id="card-{{ loop.index }}" data-host="{{ h }}">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <div>
                    <span class="host-name">{{ host_names.get(h, h) }}</span>
                    {% if host_names.get(h) %}
                    <div class="host-meta"><span class="host-ip">{{ h }}</span><span class="host-os"></span></div>
                    {% else %}
                    <div class="host-meta"><span class="host-ip"></span><span class="host-os"></span></div>
                    {% endif %}
                </div>
                <a href="/remove-host/{{ h }}" style="color:#f85149;text-decoration:none;font-size:1.1em;line-height:1;" title="Remove">&times;</a>
            </div>
            <div class="host-body" style="margin-top:8px;"><span class="spinner"></span> Checking...</div>
            <div class="progress-wrap"><div class="progress-bar"></div></div>
            <div class="patch-result"></div>
            <div class="host-actions">
                <a onclick="refreshHost('{{ h }}', this)">↺ Refresh</a>
            </div>
        </div>
    {% endfor %}
    </div>

    <script>
    const hosts = {{ hosts | tojson }};
    const hostNames = {{ host_names | tojson }};
    const OS_NAMES = { dnf: 'RHEL/Fedora', yum: 'CentOS', apt: 'Debian/Ubuntu', unknown: 'Unknown' };
    let statOk = 0, statNeeds = 0, statErr = 0, statPending = hosts.length, statNoCreds = 0;

    function updateStats() {
        document.getElementById('stat-ok').textContent = statOk;
        document.getElementById('stat-needs').textContent = statNeeds;
        document.getElementById('stat-err').textContent = statErr;
        document.getElementById('stat-pending').textContent = statPending;
        document.getElementById('stat-nocreds').textContent = statNoCreds;
        document.getElementById('stat-nocreds-wrap').style.display = statNoCreds > 0 ? '' : 'none';
        const btn = document.getElementById('patch-all-btn');
        if (btn) btn.disabled = statNeeds === 0 || statPending > 0;
    }

    function renderHostBody(card, data) {
        const body = card.querySelector('.host-body');
        const status = data.status;
        card.className = 'host ' + (status === 'no_creds' ? 'gray' : status === null ? 'gray' : status ? 'red' : 'green');

        // OS label next to IP
        const osEl = card.querySelector('.host-os');
        if (osEl && data.pkgmgr) {
            const label = OS_NAMES[data.pkgmgr] || data.pkgmgr;
            osEl.textContent = ' (' + label + ')';
        }

        if (status === 'no_creds') {
            body.innerHTML = '<em style="color:#e3b341;">SSH credentials required — <a href="/ssh-creds" style="color:#e3b341;">set them here</a></em>';
        } else if (status === null) {
            body.innerHTML = '<em>Connection error</em><br><pre style="font-size:0.8em;">' + escHtml(data.updates) + '</pre>';
        } else if (status === true) {
            const lines = data.updates.split('\\n').filter(l => l && !l.startsWith('Last metadata') && !l.startsWith('Obsoleting'));
            const items = lines.map(l => {
                const parts = l.split(/\\s+/);
                return '<li><span class="pkg-name">' + escHtml(parts[0]) + '</span> <span class="pkg-rest">' + escHtml(parts.slice(1).join(' ')) + '</span></li>';
            }).join('');
            body.innerHTML = 'Needs updates<br><button class="patch-btn">Patch Now</button><div class="updates-list"><ul>' + items + '</ul></div>';
            card.querySelector('.patch-btn').addEventListener('click', () => patchHost(card.dataset.host, card));
        } else {
            body.innerHTML = '✓ Up to date';
        }
    }

    function escHtml(str) {
        return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function sortGrid() {
        const grid = document.getElementById('host-grid');
        const cards = Array.from(grid.querySelectorAll('.host'));
        const order = {'red': 0, 'green': 1, 'gray': 2, 'loading': 3};
        cards.sort((a, b) => {
            const ac = [...a.classList].find(c => order[c] !== undefined) || 'loading';
            const bc = [...b.classList].find(c => order[c] !== undefined) || 'loading';
            return (order[ac] ?? 3) - (order[bc] ?? 3);
        });
        cards.forEach(c => grid.appendChild(c));
    }

    function checkHost(host, cardEl) {
        fetch('/host-status/' + host)
            .then(r => r.json())
            .then(data => {
                statPending--;
                if (data.status === 'no_creds') statNoCreds++;
                else if (data.status === null) statErr++;
                else if (data.status) statNeeds++;
                else statOk++;
                updateStats();
                renderHostBody(cardEl, data);
                sortGrid();
            })
            .catch(() => {
                statPending--;
                statErr++;
                updateStats();
                cardEl.className = 'host gray';
                cardEl.querySelector('.host-body').innerHTML = '<em>Request failed</em>';
                sortGrid();
            });
    }

    function refreshHost(host, linkEl) {
        const card = linkEl.closest('.host');
        card.className = 'host loading';
        card.querySelector('.host-body').innerHTML = '<span class="spinner"></span> Checking...';

        const prev = card.dataset.prevStatus;
        if (prev === 'no_creds') statNoCreds--;
        else if (prev === 'null') statErr--;
        else if (prev === 'true') statNeeds--;
        else if (prev === 'false') statOk--;
        statPending++;
        updateStats();

        fetch('/refresh-host/' + host).then(() => checkHost(host, card));
    }

    let patchAllTotal = 0, patchAllDone = 0;

    function patchAll() {
        const redCards = Array.from(document.querySelectorAll('.host.red'));
        if (!redCards.length) return;
        patchAllTotal = redCards.length;
        patchAllDone = 0;

        document.getElementById('patch-all-btn').disabled = true;
        const wrap = document.getElementById('global-progress-wrap');
        const bar = document.getElementById('global-progress-bar');
        const label = document.getElementById('global-progress-label');
        wrap.style.display = 'block';
        bar.style.width = '0%';
        label.textContent = 'Patching 0 / ' + patchAllTotal + ' hosts...';

        redCards.forEach(card => patchHost(card.dataset.host, card, onPatchAllDone));
    }

    function onPatchAllDone() {
        patchAllDone++;
        const bar = document.getElementById('global-progress-bar');
        const label = document.getElementById('global-progress-label');
        const pct = Math.round((patchAllDone / patchAllTotal) * 100);
        bar.style.width = pct + '%';
        if (patchAllDone >= patchAllTotal) {
            label.textContent = 'Patch All complete — ' + patchAllTotal + ' host(s) processed.';
            setTimeout(updateStats, 3000);
        } else {
            label.textContent = 'Patching ' + patchAllDone + ' / ' + patchAllTotal + ' hosts...';
        }
    }

    function patchHost(host, card, onDone) {
        const bar = card.querySelector('.progress-wrap');
        const result = card.querySelector('.patch-result');
        const btn = card.querySelector('.patch-btn');
        if (btn) btn.disabled = true;
        bar.style.display = 'block';
        result.style.display = 'none';

        fetch('/patch-host/' + host, {method: 'POST'})
            .then(r => r.json())
            .then(data => {
                if (data.error) { patchFinished(card, bar, result, btn, false, 'Error: ' + data.error, onDone); return; }
                pollJob(data.job_id, card, bar, result, btn, onDone);
            });
    }

    function pollJob(jobId, card, bar, result, btn, onDone) {
        fetch('/patch-status/' + jobId)
            .then(r => r.json())
            .then(data => {
                if (data.status === 'running') {
                    setTimeout(() => pollJob(jobId, card, bar, result, btn, onDone), 2000);
                } else {
                    const ok = data.status === 'done';
                    patchFinished(card, bar, result, btn, ok, ok ? 'Patched — refreshing...' : 'Patch failed. ' + (data.error || ''), onDone);
                }
            });
    }

    function patchFinished(card, bar, result, btn, ok, msg, onDone) {
        bar.style.display = 'none';
        result.style.display = 'block';
        result.style.color = ok ? 'green' : 'red';
        result.textContent = msg;
        if (btn) btn.disabled = false;
        if (ok) setTimeout(() => { fetch('/refresh-host/' + card.dataset.host).then(() => checkHost(card.dataset.host, card)); }, 1500);
        if (onDone) onDone();
    }

    function checkAllHosts() {
        statOk = 0; statNeeds = 0; statErr = 0; statNoCreds = 0;
        statPending = hosts.length;
        document.querySelectorAll('.host[data-host]').forEach(card => {
            card.className = 'host loading';
            card.querySelector('.host-body').innerHTML = '<span class="spinner"></span> Checking...';
            checkHost(card.dataset.host, card);
        });
    }

    // Kick off all checks on page load, then refresh API host list every 30 seconds
    checkAllHosts();
    setInterval(() => {
        fetch('/refresh-api-cache').then(() => location.reload());
    }, 30000);
    </script>
</body>
</html>
"""


init_db()
init_users()

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8443, ssl_context='adhoc')
