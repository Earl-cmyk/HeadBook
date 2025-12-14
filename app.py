from flask import Flask, request, render_template, redirect, url_for, g, jsonify, session
import sqlite3
import os
import threading
import time
import uuid
from markupsafe import escape
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import markdown, bleach
from atlas_data import atlas_graph

ALLOWED_TAGS = [
    "h1","h2","h3","p","strong","em",
    "ul","li","hr","code","pre","blockquote"
]

def md_to_safe_html(md_text: str) -> str:
    return bleach.clean(
        markdown.markdown(
            md_text or "",
            extensions=["fenced_code", "tables"]
        ),
        tags=[
            "h1","h2","h3",
            "p","strong","em",
            "ul","ol","li",
            "hr",
            "code","pre","blockquote"
        ],
        strip=True
    )


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
DATABASE = os.environ.get("DATABASE_PATH", "feed.db")

# -------------------------
# OOP USER AUTHENTICATION
# -------------------------
class User:
    """User model with authentication support."""

    def __init__(self, id=None, username=None, email=None, oauth_provider=None, oauth_id=None):
        self.id = id
        self.username = username
        self.email = email
        self.oauth_provider = oauth_provider
        self.oauth_id = oauth_id

    @staticmethod
    def create_local(db, username, email, password):
        """Create a new local user with hashed password."""
        try:
            hashed_pwd = generate_password_hash(password)
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                (username, email, hashed_pwd)
            )
            db.commit()
            return User(id=cursor.lastrowid, username=username, email=email)
        except sqlite3.IntegrityError:
            return None  # User already exists

    @staticmethod
    def create_oauth(db, oauth_provider, oauth_id, username, email):
        """Create or get OAuth user."""
        cursor = db.cursor()
        # Check if OAuth user exists
        cursor.execute("SELECT id, username, email FROM users WHERE oauth_provider=? AND oauth_id=?",
                       (oauth_provider, oauth_id))
        row = cursor.fetchone()
        if row:
            return User(id=row[0], username=row[1], email=row[2], oauth_provider=oauth_provider, oauth_id=oauth_id)

        # Create new OAuth user
        try:
            cursor.execute(
                "INSERT INTO users (username, email, oauth_provider, oauth_id) VALUES (?, ?, ?, ?)",
                (username, email, oauth_provider, oauth_id)
            )
            db.commit()
            return User(id=cursor.lastrowid, username=username, email=email, oauth_provider=oauth_provider,
                        oauth_id=oauth_id)
        except sqlite3.IntegrityError:
            # Username or email conflict; use a unique variant
            unique_username = f"{oauth_provider}_{uuid.uuid4().hex[:8]}"
            cursor.execute(
                "INSERT INTO users (username, email, oauth_provider, oauth_id) VALUES (?, ?, ?, ?)",
                (unique_username, email, oauth_provider, oauth_id)
            )
            db.commit()
            return User(id=cursor.lastrowid, username=unique_username, email=email, oauth_provider=oauth_provider,
                        oauth_id=oauth_id)

    @staticmethod
    def authenticate(db, username, password):
        """Authenticate user by username and password."""
        cursor = db.cursor()
        cursor.execute("SELECT id, username, email, password FROM users WHERE username=?", (username,))
        row = cursor.fetchone()
        if row and row[3]:  # Check if password exists
            if check_password_hash(row[3], password):
                return User(id=row[0], username=row[1], email=row[2])
        return None

    @staticmethod
    def get_by_id(db, user_id):
        """Fetch user by ID."""
        cursor = db.cursor()
        cursor.execute("SELECT id, username, email, oauth_provider, oauth_id FROM users WHERE id=?", (user_id,))
        row = cursor.fetchone()
        if row:
            return User(id=row[0], username=row[1], email=row[2], oauth_provider=row[3], oauth_id=row[4])
        return None


class AuthManager:
    """Manage user sessions and authentication."""

    @staticmethod
    def login_user(user):
        """Store user in session."""
        session['user_id'] = user.id
        session['username'] = user.username

    @staticmethod
    def logout_user():
        """Clear user session."""
        session.pop('user_id', None)
        session.pop('username', None)

    @staticmethod
    def get_current_user(db):
        """Get current logged-in user from session."""
        user_id = session.get('user_id')
        if user_id:
            return User.get_by_id(db, user_id)
        return None

    @staticmethod
    def is_authenticated():
        """Check if user is logged in."""
        return 'user_id' in session


def login_required(f):
    """Decorator to require login."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not AuthManager.is_authenticated():
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)

    return decorated_function


def get_current_user_context():
    """Get current user for template context."""
    db = get_db()
    user = AuthManager.get_current_user(db)
    return user


# -------------------------
# SIMPLE NODE / STRUCTURES
# -------------------------
class Node:
    def __init__(self, data):
        self.node = data
        self.left = None
        self.right = None


class Stack:
    def __init__(self):
        self.head = None
        self.length = 0

    def push(self, data):
        n = Node(data)
        n.left = self.head
        self.head = n
        self.length += 1

    def to_list(self):
        items = []
        cur = self.head
        while cur:
            items.append(cur.node)
            cur = cur.left
        return items


class QueueLinked:
    """Linked-list based queue used only locally in some actions."""

    def __init__(self):
        self.head = None
        self.tail = None
        self.length = 0

    def enqueue(self, data):
        n = Node(data)
        if not self.head:
            self.head = n
            self.tail = n
        else:
            self.tail.right = n
            self.tail = n
        self.length += 1

    def dequeue(self):
        if self.length == 0:
            return None
        n = self.head
        self.head = self.head.right
        self.length -= 1
        return n.node


class BST:
    def __init__(self):
        self.root = None

    def insert(self, data):
        # data expected as string
        new = Node(data)
        if not self.root:
            self.root = new
            return

        cur = self.root
        while True:
            if data < cur.node:
                if cur.left:
                    cur = cur.left
                else:
                    cur.left = new
                    return
            else:
                if cur.right:
                    cur = cur.right
                else:
                    cur.right = new
                    return

    def dfs_search(self, word):
        if not word:
            return []
        results = []
        w = word.lower()

        def walk(node):
            if not node:
                return
            try:
                if w in node.node.lower():
                    results.append(node.node)
            except Exception:
                pass
            walk(node.left)
            walk(node.right)

        walk(self.root)
        return results

import heapq

class SimpleGraph:
    def __init__(self):
        self.edges = {}  # u -> v -> (minutes, meters)

    def add_edge(self, u, v, minutes, meters):
        self.edges.setdefault(u, {})[v] = (minutes, meters)
        self.edges.setdefault(v, {})[u] = (minutes, meters)

    def shortest_path(self, src, dst):
        import heapq

        pq = [(0, 0, src, [])]  # (time, distance, node, path)
        seen = set()

        while pq:
            t, d, u, path = heapq.heappop(pq)
            if u in seen:
                continue

            path = path + [u]

            if u == dst:
                return path, t, d

            seen.add(u)

            for v, (tm, dm) in self.edges.get(u, {}).items():
                heapq.heappush(pq, (t + tm, d + dm, v, path))

        return [], 0, 0


# =========================
# CREATE GRAPH
# =========================
graph = SimpleGraph()

# =========================
# MRT-3 (North ↔ South)
# =========================
graph.add_edge("North Ave", "Quezon Ave", 2, 1800)
graph.add_edge("Quezon Ave", "GMA Kamuning", 2, 1600)
graph.add_edge("GMA Kamuning", "Cubao MRT", 2, 1200)
graph.add_edge("Cubao MRT", "Santolan Annapolis", 2, 1300)
graph.add_edge("Santolan Annapolis", "Ortigas", 2, 2000)
graph.add_edge("Ortigas", "Shaw Blvd", 2, 800)
graph.add_edge("Shaw Blvd", "Boni", 2, 900)
graph.add_edge("Boni", "Guadalupe", 2, 1000)
graph.add_edge("Guadalupe", "Buendia MRT", 2, 2200)
graph.add_edge("Buendia MRT", "Ayala", 2, 900)
graph.add_edge("Ayala", "Magallanes", 2, 1200)
graph.add_edge("Magallanes", "Taft Ave MRT", 2, 800)

# =========================
# LRT-1 (North ↔ South)
# =========================
graph.add_edge("Fernando Poe Jr", "Balintawak", 2, 2000)
graph.add_edge("Balintawak", "Monumento", 2, 1200)
graph.add_edge("Monumento", "5th Ave", 2, 900)
graph.add_edge("5th Ave", "R. Papa", 2, 1000)
graph.add_edge("R. Papa", "Abad Santos", 2, 900)
graph.add_edge("Abad Santos", "Blumentritt LRT1", 2, 1100)
graph.add_edge("Blumentritt LRT1", "Tayuman", 2, 700)
graph.add_edge("Tayuman", "Bambang", 2, 600)
graph.add_edge("Bambang", "Doroteo Jose", 2, 600)
graph.add_edge("Doroteo Jose", "Carriedo", 2, 700)
graph.add_edge("Carriedo", "Central Terminal", 2, 900)
graph.add_edge("Central Terminal", "UN Ave", 2, 1000)
graph.add_edge("UN Ave", "Pedro Gil", 2, 800)
graph.add_edge("Pedro Gil", "Quirino", 2, 800)
graph.add_edge("Quirino", "Vito Cruz", 2, 900)
graph.add_edge("Vito Cruz", "Gil Puyat LRT1", 2, 1100)
graph.add_edge("Gil Puyat LRT1", "Libertad", 2, 800)
graph.add_edge("Libertad", "EDSA LRT1", 2, 900)
graph.add_edge("EDSA LRT1", "Baclaran", 2, 700)
graph.add_edge("Baclaran", "Redemptorist", 2, 900)
graph.add_edge("Redemptorist", "MIA Road", 2, 1100)
graph.add_edge("MIA Road", "Asia World", 2, 1200)

# =========================
# LRT-2 (West ↔ East)
# =========================
graph.add_edge("Recto", "Legarda", 2, 1300)
graph.add_edge("Legarda", "Pureza", 2, 1200)
graph.add_edge("Pureza", "V. Mapa", 2, 1400)
graph.add_edge("V. Mapa", "J. Ruiz", 2, 1000)
graph.add_edge("J. Ruiz", "Gilmore", 2, 900)
graph.add_edge("Gilmore", "Betty Go-Belmonte", 2, 1000)
graph.add_edge("Betty Go-Belmonte", "Cubao LRT2", 2, 800)
graph.add_edge("Cubao LRT2", "Anonas", 2, 900)
graph.add_edge("Anonas", "Katipunan", 2, 1100)
graph.add_edge("Katipunan", "Santolan", 2, 1200)
graph.add_edge("Santolan", "Marikina-Pasig", 3, 2500)
graph.add_edge("Marikina-Pasig", "Antipolo", 3, 2800)

def build_svg(path=None):
    path = path or []
    out = []
    import math
    import random

    # Get all unique stations from edges
    stations = set()
    for u in graph.edges:
        stations.add(u)
        for v in graph.edges[u]:
            stations.add(v)
    stations = sorted(stations)
    n = len(stations)

    if n == 0:
        return ""

    # =========================
    # FORCE-DIRECTED LAYOUT
    # =========================
    pos = {}
    width, height = 1200, 800
    k = math.sqrt((width * height) / n)  # Optimal distance between nodes
    
    # Initialize positions randomly
    for station in stations:
        pos[station] = {
            'x': random.uniform(0, width),
            'y': random.uniform(0, height),
            'vx': 0,
            'vy': 0
        }

    # Simple force-directed layout
    def repulse():
        for i, u in enumerate(stations):
            for v in stations[i+1:]:
                dx = pos[u]['x'] - pos[v]['x']
                dy = pos[u]['y'] - pos[v]['y']
                d = max(0.1, math.sqrt(dx*dx + dy*dy))
                f = (k * k) / (d * d)
                fx = f * dx / d
                fy = f * dy / d
                pos[u]['vx'] += fx
                pos[u]['vy'] += fy
                pos[v]['vx'] -= fx
                pos[v]['vy'] -= fy

    def attract():
        for u in graph.edges:
            for v in graph.edges[u]:
                dx = pos[v]['x'] - pos[u]['x']
                dy = pos[v]['y'] - pos[u]['y']
                d = max(0.1, math.sqrt(dx*dx + dy*dy))
                f = (d * d) / k
                fx = f * dx / d
                fy = f * dy / d
                pos[u]['vx'] += fx
                pos[u]['vy'] += fy
                pos[v]['vx'] -= fx
                pos[v]['vy'] -= fy

    # Run force-directed layout
    for _ in range(100):  # Number of iterations
        repulse()
        attract()
        
        # Update positions
        for station in stations:
            # Apply velocity
            pos[station]['x'] += pos[station]['vx'] * 0.1
            pos[station]['y'] += pos[station]['vy'] * 0.1
            # Dampening
            pos[station]['vx'] *= 0.9
            pos[station]['vy'] *= 0.9
            # Boundary conditions
            pos[station]['x'] = max(50, min(width - 50, pos[station]['x']))
            pos[station]['y'] = max(50, min(height - 50, pos[station]['y']))

    # =========================
    # EDGES
    # =========================
    out.append('<g id="edges">')
    for u in graph.edges:
        x1, y1 = pos[u]['x'], pos[u]['y']
        for v in graph.edges[u]:
            x2, y2 = pos[v]['x'], pos[v]['y']
            # Determine line color based on line type
            line_class = "line-mrt3" if "MRT" in u or "MRT" in v else "line-lrt1" if "LRT1" in u or "LRT1" in v else "line-lrt2"
            out.append(
                f'''
                <line x1="{x1}" y1="{y1}"
                      x2="{x2}" y2="{y2}"
                      class="{line_class}"
                      stroke-width="4"
                      stroke-linecap="round"
                      stroke-opacity="0.7"/>
                '''
            )
    out.append('</g>')

    # =========================
    # ROUTE HIGHLIGHT
    # =========================
    if len(path) > 1:
        out.append('<g id="route">')
        for i in range(len(path)-1):
            a, b = path[i], path[i+1]
            x1, y1 = pos[a]['x'], pos[a]['y']
            x2, y2 = pos[b]['x'], pos[b]['y']
            out.append(
                f'''
                <line x1="{x1}" y1="{y1}"
                      x2="{x2}" y2="{y2}"
                      stroke="#10b981"
                      stroke-width="6"
                      stroke-linecap="round"
                      stroke-dasharray="8,4"
                      stroke-linejoin="round"/>
                '''
            )
        out.append('</g>')

    # =========================
    # NODES
    # =========================
    out.append('<g id="nodes">')
    for name in stations:
        x, y = pos[name]['x'], pos[name]['y']
        is_path = name in path
        cls = "station" + (" locked" if is_path else "")
        
        # Add station marker
        out.append(
            f'''
            <g class="node" transform="translate({x}, {y})">
                <circle cx="0" cy="0" r="8"
                        class="{cls}"
                        data-station="{name}"
                        style="cursor: pointer;"/>
                <text x="12" y="4"
                      class="station-label"
                      data-station="{name}">
                    {name}
                </text>
            </g>
            '''
        )
    out.append('</g>')

    # =========================
    # FINAL SVG
    # =========================
    svg = f'''
    <svg width="100%" height="100%" viewBox="0 0 {width} {height}"
         xmlns="http://www.w3.org/2000/svg"
         style="background: #f8f9fa;"
         id="rail-map">
        <defs>
            <style>
                .station {{
                    fill: #6c757d;
                    stroke: white;
                    stroke-width: 2;
                    transition: all 0.2s;
                }}
                .station:hover {{
                    fill: #0d6efd;
                    transform: scale(1.5);
                }}
                .station.locked {{
                    fill: #10b981;
                    stroke: white;
                    stroke-width: 2;
                    filter: drop-shadow(0 0 4px rgba(16, 185, 129, 0.5));
                }}
                .station-label {{
                    font-size: 12px;
                    font-weight: 500;
                    fill: #495057;
                    text-shadow: 0 0 3px white, 0 0 3px white, 0 0 3px white;
                    pointer-events: none;
                    transition: all 0.2s;
                }}
                .station.locked + .station-label {{
                    fill: #10b981;
                    font-weight: 600;
                }}
                .line-mrt3 {{ stroke: #FFD700; }}  /* Gold for MRT-3 */
                .line-lrt1 {{ stroke: #FF0000; }}  /* Red for LRT-1 */
                .line-lrt2 {{ stroke: #6F2DA8; }}  /* Purple for LRT-2 */
            </style>
        </defs>
        <g transform="translate(0,0)">
            {"".join(out)}
        </g>
        <g id="controls">
            <rect x="20" y="20" width="180" height="100" rx="8" fill="white" stroke="#e2e8f0" stroke-width="1"/>
            <text x="30" y="45" font-size="12" font-weight="600">Line Legend</text>
            <g transform="translate(30, 65)">
                <line x1="0" y1="0" x2="20" y2="0" class="line-mrt3" stroke-width="4"/>
                <text x="30" y="4" font-size="11">MRT-3</text>
            </g>
            <g transform="translate(30, 85)">
                <line x1="0" y1="0" x2="20" y2="0" class="line-lrt1" stroke-width="4"/>
                <text x="30" y="4" font-size="11">LRT-1</text>
            </g>
            <g transform="translate(30, 105)">
                <line x1="0" y1="0" x2="20" y2="0" class="line-lrt2" stroke-width="4"/>
                <text x="30" y="4" font-size="11">LRT-2</text>
            </g>
        </g>
    </svg>
    '''
    return svg

# -------------------------
# DATABASE HELPERS
# -------------------------
def get_db():
    if "db" not in g:
        dir_name = os.path.dirname(DATABASE)
        if dir_name:  # only create folder if a directory exists
            os.makedirs(dir_name, exist_ok=True)
        db = sqlite3.connect(DATABASE, timeout=30, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA synchronous=NORMAL;")
        db.execute("PRAGMA foreign_keys=ON;")
        g.db = db
    return g.db



@app.teardown_appcontext
def close_db(error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    dir_name = os.path.dirname(DATABASE)
    if dir_name:  # only create folder if there is a directory
        os.makedirs(dir_name, exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    # PRAGMAS
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA foreign_keys=ON;")

    # -------------------------
    # BASE SCHEMA
    # -------------------------
    if os.path.exists("schema.sql"):
        with open("schema.sql") as f:
            cur.executescript(f.read())
    else:
        # Fallback minimal schema
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            password TEXT,
            oauth_provider TEXT,
            oauth_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT NOT NULL,
            caption TEXT NOT NULL,
            post_type TEXT NOT NULL DEFAULT 'text',
            up INTEGER NOT NULL DEFAULT 0,
            down INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER,
            comment TEXT NOT NULL,
            parent_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES posts(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """)

    # -------------------------
    # MIGRATIONS
    # -------------------------

    # comments.parent_id
    cur.execute("PRAGMA table_info(comments)")
    cols = [r[1] for r in cur.fetchall()]
    if "parent_id" not in cols:
        try:
            cur.execute("ALTER TABLE comments ADD COLUMN parent_id INTEGER")
        except Exception:
            pass

    # attachments
    cur.execute("""
    CREATE TABLE IF NOT EXISTS attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        path TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
    );
    """)

    # posts.user_id
    cur.execute("PRAGMA table_info(posts)")
    post_cols = [r[1] for r in cur.fetchall()]
    if "user_id" not in post_cols:
        try:
            cur.execute("ALTER TABLE posts ADD COLUMN user_id INTEGER")
        except Exception:
            pass

    # -------------------------
    # INDEXES
    # -------------------------
    cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_user_id ON posts(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_title ON posts(title)")

    conn.commit()
    conn.close()

# -------------------------
# FEED / SEARCH LOGIC
# -------------------------
def get_feed_stack():
    db = get_db()

    rows = db.execute("""
        SELECT p.*, u.username
        FROM posts p
        LEFT JOIN users u ON p.user_id = u.id
        ORDER BY p.id DESC
    """).fetchall()

    stack = Stack()

    for r in rows:
        caption_md = r["caption"] or ""

        post = {
            "id": r["id"],
            "user_id": r["user_id"],
            "title": r["title"],
            # keep markdown ONLY if you need editing later
            "caption": caption_md,
            # ALWAYS use this for display
            "caption_html": md_to_safe_html(caption_md),
            "author": r["username"] or "Anonymous",
            "post_type": r["post_type"],
            "up": r["up"] or 0,
            "down": r["down"] or 0,
        }

        # latest comment
        latest = db.execute(
            "SELECT comment, created_at FROM comments WHERE post_id=? ORDER BY id DESC LIMIT 1",
            (r["id"],)
        ).fetchone()

        post["latest_comment"] = latest["comment"] if latest else None
        post["latest_comment_time"] = latest["created_at"] if latest else None

        # attachments
        atts = db.execute(
            "SELECT id, filename, path FROM attachments WHERE post_id=? ORDER BY id ASC",
            (r["id"],)
        ).fetchall()

        post["attachments"] = [
            {
                "id": a["id"],
                "filename": a["filename"],
                "url": a["path"]
            } for a in atts
        ]

        stack.push(post)

    return stack.to_list()


def perform_bst_search(keyword):
    posts = get_feed_stack()
    bst = BST()
    for post in posts:
        title = post.get("title", "") or ""
        bst.insert(title)
    return bst.dfs_search(keyword)


# -------------------------
# ROUTES
# -------------------------
@app.route("/register", methods=["GET", "POST"])
def register_page():
    """User registration page."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not username or not email or not password:
            return render_template("register.html", error="All fields are required.")

        if password != confirm_password:
            return render_template("register.html", error="Passwords do not match.")

        db = get_db()
        user = User.create_local(db, username, email, password)

        if not user:
            return render_template("register.html", error="Username or email already exists.")

        # Auto-login after registration
        AuthManager.login_user(user)
        return redirect(url_for("lectures"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """User login page."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            return render_template("login.html", error="Username and password required.")

        db = get_db()
        user = User.authenticate(db, username, password)

        if not user:
            return render_template("login.html", error="Invalid username or password.")

        AuthManager.login_user(user)
        return redirect(url_for("lectures"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    """Logout the current user."""
    AuthManager.logout_user()
    return redirect(url_for("login_page"))


@app.route("/oauth/github", methods=["GET"])
def oauth_github_login():
    """Initiate GitHub OAuth flow (simplified for demo)."""
    # In production, use a proper OAuth library (authlib, requests-oauthlib)
    # For now, create a demo OAuth user
    db = get_db()
    github_id = request.args.get("code", "github_demo")
    user = User.create_oauth(db, "github", github_id, f"github_{github_id[:8]}", f"{github_id}@github.local")
    AuthManager.login_user(user)
    return redirect(url_for("lectures"))


@app.route("/oauth/google", methods=["GET"])
def oauth_google_login():
    """Initiate Google OAuth flow (simplified for demo)."""
    # In production, use a proper OAuth library (authlib, requests-oauthlib)
    # For now, create a demo OAuth user
    db = get_db()
    google_id = request.args.get("code", "google_demo")
    user = User.create_oauth(db, "google", google_id, f"google_{google_id[:8]}", f"{google_id}@google.local")
    AuthManager.login_user(user)
    return redirect(url_for("lectures"))


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        keyword = request.form.get("search", "") or ""

        db = get_db()
        sql_results = db.execute("""
            SELECT id, title, caption 
            FROM posts
            WHERE title LIKE ? OR caption LIKE ?
            ORDER BY id DESC
        """, (f"%{keyword}%", f"%{keyword}%")).fetchall()

        results = []
        for r in sql_results:
            cid = r["id"]
            title = r["title"] or ""
            caption = r["caption"] or ""
            max_value = caption if caption.strip() else "None"

            # related_count: naive heuristic using first word of title
            related_count = 0
            if title.strip():
                first_word = title.strip().split()[0]
                q = db.execute(
                    "SELECT COUNT(*) as c FROM posts WHERE (title LIKE ? OR caption LIKE ?) AND id != ?",
                    (f"%{first_word}%", f"%{first_word}%", cid)
                ).fetchone()
                related_count = q["c"] if q is not None else 0

            results.append({
                "id": cid,
                "title": title,
                "caption": caption,
                "max_value": max_value,
                "related_count": related_count
            })
        # attach latest comment for each result (if present)
        for item in results:
            try:
                latest = db.execute("SELECT comment FROM comments WHERE post_id=? ORDER BY id DESC LIMIT 1",
                                    (item['id'],)).fetchone()
                item['latest_comment'] = latest['comment'] if latest else None
            except Exception:
                item['latest_comment'] = None

        return jsonify(results)
    # default homepage load
    posts = get_feed_stack()
    return render_template("index.html", posts=posts, current_user=get_current_user_context())


@app.route("/search_posts")
def search_posts():
    q = request.args.get("q", "").strip()
    db = get_db()

    rows = db.execute("""
        SELECT id, title, caption
        FROM posts
        WHERE title LIKE ? OR caption LIKE ?
        ORDER BY id DESC
    """, (f"%{q}%", f"%{q}%")).fetchall()

    results = []

    for r in rows:
        caption_md = r["caption"] or ""

        results.append({
            "id": r["id"],
            "title": r["title"] or "",
            "caption_html": md_to_safe_html(caption_md),  # 
            "related_count": 0
        })

    # attach latest comment
    for item in results:
        latest = db.execute(
            "SELECT comment FROM comments WHERE post_id=? ORDER BY id DESC LIMIT 1",
            (item["id"],)
        ).fetchone()
        item["latest_comment"] = latest["comment"] if latest else None

    return jsonify(results)



@app.route("/lectures", methods=["GET", "POST"])
def lectures():
    if request.method == "POST":
        db = get_db()
        db.execute("""
            INSERT INTO posts(title, caption, author, post_type, up, down)
            VALUES (?, ?, ?, ?, 0, 0)
        """, (
            request.form.get("title"),
            request.form.get("caption"),
            request.form.get("author", "Anonymous"),
            request.form.get("post_type", "regular")
        ))
        db.commit()
        return redirect(url_for("lectures"))

    db_posts = get_feed_stack()

    interactive_posts = [
        {
            "id": -1,
            "title": "Queue Interactive Demo",
            "caption": "Real-time enqueue/dequeue visualization.",
            "up": 0,
            "down": 0
        },
        {
            "id": -2,
            "title": "Stack Interactive Demo",
            "caption": "Push/pop to see LIFO behavior.",
            "up": 0,
            "down": 0
        },
        {
            "id": -3,
            "title": "Tree Interactive Demo",
            "caption": "Add nodes to grow a general tree.",
            "up": 0,
            "down": 0
        },
        {
            "id": -4,
            "title": "Binary Tree Interactive Demo",
            "caption": "Insert left/right nodes manually.",
            "up": 0,
            "down": 0
        },
        {
            "id": -5,
            "title": "Binary Search Tree Interactive Demo",
            "caption": "Automatic BST insertion.",
            "up": 0,
            "down": 0
        }
    ]

    final_posts = interactive_posts + db_posts
    # Enrich regular posts with two helper fields:
    # - max_value: show the post's caption (or 'None')
    # - related_count: number of other posts that share a keyword from this title
    db = get_db()
    for post in final_posts:
        try:
            if post.get("id", 0) > 0:
                # max_value: use caption or 'None'
                post["max_value"] = post.get("caption") or "None"

                # related_count: use the first word of the title to find related posts
                title = (post.get("title") or "").strip()
                if title:
                    first_word = title.split()[0]
                    q = db.execute(
                        "SELECT COUNT(*) as c FROM posts WHERE (title LIKE ? OR caption LIKE ?) AND id != ?",
                        (f"%{first_word}%", f"%{first_word}%", post["id"])
                    ).fetchone()
                    post["related_count"] = q["c"] if q is not None else 0
                else:
                    post["related_count"] = 0
            else:
                # interactive placeholders: show N/A
                post["max_value"] = "N/A"
                post["related_count"] = 0
        except Exception:
            post["max_value"] = post.get("caption") or "None"
            post["related_count"] = 0

    return render_template("lectures.html", posts=final_posts, current_user=get_current_user_context())


def get_caption_from_db(id):
    # Connect to your database
    import sqlite3
    conn = sqlite3.connect('feed.db')  # replace with your DB file
    cursor = conn.cursor()

    # Fetch caption
    cursor.execute("SELECT caption FROM captions WHERE id = ?", (id,))
    row = cursor.fetchone()
    conn.close()

    return row[0] if row else None


@app.route("/lecture/<int:id>")
def lecture(id):
    caption_md = get_caption_from_db(id)

    caption_html = bleach.clean(
        markdown.markdown(
            caption_md,
            extensions=["fenced_code", "tables"]
        ),
        tags=[
            "h1","h2","h3",
            "p","strong","em",
            "ul","ol","li",
            "hr",
            "code","pre","blockquote"
        ],
        strip=True
    )

    return render_template(
        "lecture.html",
        caption_html=caption_html
    )


@app.route("/create_post", methods=["POST"])
def create_post():
    if not AuthManager.is_authenticated():
        return jsonify({"ok": False, "error": "login_required"}), 401

    db = get_db()
    user = AuthManager.get_current_user(db)
    if not user:
        return jsonify({"ok": False, "error": "user_not_found"}), 401

    title = request.form.get("title")
    caption = request.form.get("caption")
    post_type = request.form.get("post_type", "regular")

    cur = db.cursor()
    cur.execute("""
        INSERT INTO posts(user_id, title, caption, post_type, up, down)
        VALUES (?, ?, ?, ?, 0, 0)
    """, (user.id, title, caption, post_type))
    db.commit()
    post_id = cur.lastrowid

    # handle uploaded attachments (form field 'attachments', multiple allowed)
    try:
        files = request.files.getlist('attachments') if request.files else []
    except Exception:
        files = []

    if files:
        upload_dir = os.path.join('static', 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        for f in files:
            if not f or f.filename == '':
                continue
            safe_name = f"{uuid.uuid4().hex}_{escape_text(f.filename)}"
            dest = os.path.join(upload_dir, safe_name)
            try:
                f.save(dest)
                db.execute("INSERT INTO attachments (post_id, filename, path) VALUES (?, ?, ?)",
                           (post_id, f.filename, dest))
            except Exception:
                pass
        db.commit()

    return redirect(url_for("lectures"))


@app.route('/comments/add', methods=['POST'])
def comments_add():
    db = get_db()
    # support both form and JSON
    post_id = request.form.get('post_id') or request.json.get('post_id')
    comment = request.form.get('comment') or request.json.get('comment')
    parent_id = request.form.get('parent_id') or request.json.get('parent_id')
    author = request.form.get('author') or request.json.get('author') or 'Anonymous'

    try:
        post_id_int = int(post_id)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid post id'})

    if not comment:
        return jsonify({'ok': False, 'error': 'empty comment'})

    # Keep whitespace as-is (do not strip)
    db.execute("INSERT INTO comments (post_id, user_id, comment, parent_id) VALUES (?, NULL, ?, ?)",
               (post_id_int, comment, parent_id))
    db.commit()

    # return latest comment for convenience
    row = db.execute("SELECT id, comment, created_at FROM comments WHERE post_id=? ORDER BY id DESC LIMIT 1",
                     (post_id_int,)).fetchone()
    return jsonify({'ok': True, 'comment': dict(row) if row else None})


@app.route('/posts/<int:post_id>/comments')
def comments_for_post(post_id):
    db = get_db()
    rows = db.execute(
        "SELECT id, post_id, user_id, comment, parent_id, created_at FROM comments WHERE post_id=? ORDER BY id ASC",
        (post_id,)).fetchall()
    results = [dict(r) for r in rows]
    return jsonify(results)


@app.route("/vote/<int:id>/<string:way>", methods=["POST"])
def vote(id, way):
    db = get_db()
    if way == "up":
        db.execute("UPDATE posts SET up = up + 1 WHERE id=?", (id,))
    else:
        db.execute("UPDATE posts SET down = down + 1 WHERE id=?", (id,))
    db.commit()
    row = db.execute("SELECT up, down FROM posts WHERE id=?", (id,)).fetchone()
    if row:
        return jsonify({"ok": True, "up": row[0], "down": row[1]})
    return jsonify({"ok": False}), 404


# schedule a cancellable delete from the UI (5 second delay)
def perform_delete(post_id):
    """Perform deletion using a fresh DB connection (safe from background threads)."""
    try:
        conn = sqlite3.connect(DATABASE)
        conn.execute("DELETE FROM posts WHERE id=?", (post_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass
    finally:
        try:
            pending_deletes.pop(post_id, None)
        except Exception:
            pass


@app.route("/delete/<int:id>", methods=["POST"])
def schedule_delete(id):
    global pending_deletes

    if not AuthManager.is_authenticated():
        return jsonify({"ok": False, "error": "login_required"}), 401

    db = get_db()
    current_user = AuthManager.get_current_user(db)

    # Check post ownership
    post = db.execute("SELECT user_id FROM posts WHERE id=?", (id,)).fetchone()
    if not post or post[0] != current_user.id:
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    if id in pending_deletes:
        return jsonify({"ok": True, "pending": True})

    t = threading.Timer(5.0, perform_delete, args=(id,))
    pending_deletes[id] = t
    t.start()
    return jsonify({"ok": True, "scheduled": True})


@app.route('/delete/cancel/<int:id>', methods=['POST'])
def cancel_delete(id):
    global pending_deletes
    t = pending_deletes.pop(id, None)
    if t:
        try:
            t.cancel()
        except Exception:
            pass
        return jsonify({"ok": True, "cancelled": True})
    return jsonify({"ok": False, "error": "not_pending"}), 404


# Edit should accept the same form fields used by your modal (title, caption)
@app.route("/edit/<int:id>", methods=["POST"])
def edit(id):
    if not AuthManager.is_authenticated():
        return jsonify({"ok": False, "error": "login_required"}), 401

    db = get_db()
    current_user = AuthManager.get_current_user(db)

    # Check post ownership
    post = db.execute("SELECT user_id FROM posts WHERE id=?", (id,)).fetchone()
    if not post or post[0] != current_user.id:
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    # Your modal sets the form to post title, caption (no author field)
    title = request.form.get("title")
    caption = request.form.get("caption")

    # Only update fields that were provided
    if title is not None:
        db.execute("UPDATE posts SET title=? WHERE id=?", (title, id))
    if caption is not None:
        db.execute("UPDATE posts SET caption=? WHERE id=?", (caption, id))
    db.commit()
    return redirect(url_for("lectures"))


@app.route("/collaborators")
def collaborators_page():
    return render_template("collaborators.html")


# ----------------------
# In-memory storage
# ----------------------
queue = []
stack = []
tree_root = None
tree_roots = []
bst_root = None
bt_roots = []
# Graph in-memory: vertices list and edges weight map
graph_vertices = []  # list of dicts: {id, label}
graph_edges = {}  # map (u_id, v_id) -> weight (int)
# edge weights used for trees/BT as well
edge_weights = {}
# pending deletions store: post_id -> threading.Timer
pending_deletes = {}
# pending detached subtrees store: token -> (type, node)
pending_subtrees = {}


# ----------------------
# Tree / BST classes
# ----------------------
class TreeNode:
    def __init__(self, val):
        self.val = val
        self.left = None
        self.right = None
        # support n-ary children for general Tree demo
        self.children = []
        try:
            self.id = str(uuid.uuid4())
        except Exception:
            self.id = None


# ----------------------
# Helpers
# ----------------------
def escape_text(text):
    if text is None:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


# ----------------------
# SVG renderers
# ----------------------
def render_queue_svg():
    width = max(300, 120 * max(1, len(queue)))
    height = 120
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    for i, val in enumerate(queue):
        x = 20 + i * 120
        parts.append(f'<rect x="{x}" y="30" width="100" height="60" rx="8" fill="#4cc9ff" stroke="#fff"/>')
        parts.append(
            f'<text x="{x + 50}" y="65" font-size="18" text-anchor="middle" fill="#000">{escape_text(val)}</text>')
    parts.append('</svg>')
    return "".join(parts)


def render_stack_svg():
    width = 200
    height = max(120, 80 * len(stack) + 20)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    for i, val in enumerate(reversed(stack)):
        y = 20 + i * 80
        parts.append(f'<rect x="40" y="{y}" width="120" height="60" rx="8" fill="#90f1a9" stroke="#fff"/>')
        parts.append(
            f'<text x="100" y="{y + 36}" font-size="18" text-anchor="middle" fill="#000">{escape_text(val)}</text>')
    parts.append('</svg>')
    return "".join(parts)


def render_generic_tree_svg(root):
    if not root:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 200" width="800" height="200"></svg>'

    width, height = 1000, 600
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']

    def traverse(node, x, y, level, span=200):
        if not node:
            return
        # determine children (support both children list and legacy left/right)
        childs = []
        if getattr(node, 'children', None):
            childs = [c for c in node.children if c]
        else:
            if node.left: childs.append(node.left)
            if node.right: childs.append(node.right)

        n = len(childs)
        gap = span // max(1, n)
        start_x = x - (gap * (n - 1)) / 2

        for i, ch in enumerate(childs):
            cx = int(start_x + i * gap)
            cy = y + 100
            # edge weight lookup
            w = edge_weights.get((getattr(node, 'id', ''), getattr(ch, 'id', '')), 1)
            parts.append(f'<line x1="{x}" y1="{y}" x2="{cx}" y2="{cy}" stroke="#fff" stroke-width="{1 + (w - 1)}"/>')
            if w > 1:
                mx = (x + cx) // 2
                my = (y + cy) // 2
                parts.append(f'<text x="{mx}" y="{my}" font-size="14" text-anchor="middle" fill="#fff">{w}</text>')
            traverse(ch, cx, cy, level + 1, max(60, span // 2))

        # include data-id and data-val attributes so client-side can bind click handlers
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="25" fill="#f8c537" stroke="#fff" data-id="{getattr(node, "id", "")}" data-val="{escape_text(node.val)}"/>')
        parts.append(
            f'<text x="{x}" y="{y + 5}" font-size="18" text-anchor="middle" fill="#000">{escape_text(node.val)}</text>')

    traverse(root, width // 2, 60, 1, 400)
    parts.append('</svg>')
    return "".join(parts)


def render_tree_forest_svg(roots):
    # render multiple general trees stacked vertically
    if not roots:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 200" width="1000" height="200"></svg>'
    width = 1000
    per_h = 260
    total_h = per_h * len(roots)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{total_h}" viewBox="0 0 {width} {total_h}">']

    def traverse(node, x, y, level, span=200):
        if not node:
            return
        childs = []
        if getattr(node, 'children', None):
            childs = [c for c in node.children if c]
        else:
            if node.left: childs.append(node.left)
            if node.right: childs.append(node.right)

        n = len(childs)
        gap = span // max(1, n)
        start_x = x - (gap * (n - 1)) / 2

        for i, ch in enumerate(childs):
            cx = int(start_x + i * gap)
            cy = y + 100
            parts.append(f'<line x1="{x}" y1="{y}" x2="{cx}" y2="{cy}" stroke="#fff"/>')
            traverse(ch, cx, cy, level + 1, max(60, span // 2))

        parts.append(
            f'<circle cx="{x}" cy="{y}" r="25" fill="#f8c537" stroke="#fff" data-id="{getattr(node, "id", "")}" data-val="{escape_text(node.val)}"/>')
        parts.append(
            f'<text x="{x}" y="{y + 5}" font-size="20" text-anchor="middle" fill="#000">{escape_text(node.val)}</text>')

    for i, root in enumerate(roots):
        y0 = 40 + i * per_h
        traverse(root, width // 2, y0, 1)

    parts.append('</svg>')
    return ''.join(parts)


# ----------------------
# BST helpers
# ----------------------
def bst_insert(node, val):
    if not node:
        return TreeNode(val)
    # prevent duplicate values: if equal, do nothing
    if val == node.val:
        return node
    if val < node.val:
        node.left = bst_insert(node.left, val)
    else:
        node.right = bst_insert(node.right, val)
    return node


# ----------------------
# MANUAL BINARY TREE
# ----------------------
bt_root = None


def render_binary_tree_svg(root):
    if not root:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 400" width="1000" height="400"></svg>'

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="500" viewBox="0 0 1000 500">']

    def walk(node, x, y, spread):
        if not node:
            return

        if node.left:
            lx = x - spread
            ly = y + 100
            w = edge_weights.get((getattr(node, 'id', ''), getattr(node.left, 'id', '')), 1)
            parts.append(f'<line x1="{x}" y1="{y}" x2="{lx}" y2="{ly}" stroke="white" stroke-width="{1 + (w - 1)}"/>')
            if w > 1:
                parts.append(
                    f'<text x="{(x + lx) // 2}" y="{(y + ly) // 2}" font-size="14" text-anchor="middle" fill="#fff">{w}</text>')
            walk(node.left, lx, ly, spread // 2)

        if node.right:
            rx = x + spread
            ry = y + 100
            w = edge_weights.get((getattr(node, 'id', ''), getattr(node.right, 'id', '')), 1)
            parts.append(f'<line x1="{x}" y1="{y}" x2="{rx}" y2="{ry}" stroke="white" stroke-width="{1 + (w - 1)}"/>')
            if w > 1:
                parts.append(
                    f'<text x="{(x + rx) // 2}" y="{(y + ry) // 2}" font-size="14" text-anchor="middle" fill="#fff">{w}</text>')
            walk(node.right, rx, ry, spread // 2)

        parts.append(
            f'<circle cx="{x}" cy="{y}" r="25" fill="#ff6b6b" stroke="white" data-id="{node.id}" data-val="{escape_text(node.val)}"/>')
        parts.append(
            f'<text x="{x}" y="{y + 6}" text-anchor="middle" font-size="18" fill="black">{escape_text(node.val)}</text>')

    walk(root, 500, 50, 200)
    parts.append('</svg>')
    return "".join(parts)


def render_bt_forest_svg(roots):
    # render multiple binary trees stacked vertically
    if not roots:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 200" width="1000" height="200"></svg>'
    width = 1000
    per_h = 300
    total_h = per_h * len(roots)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{total_h}" viewBox="0 0 {width} {total_h}">']

    def walk(node, x, y, spread):
        if not node:
            return
        if node.left:
            lx = x - spread
            ly = y + 100
            w = edge_weights.get((getattr(node, 'id', ''), getattr(node.left, 'id', '')), 1)
            parts.append(f'<line x1="{x}" y1="{y}" x2="{lx}" y2="{ly}" stroke="white" stroke-width="{1 + (w - 1)}"/>')
            if w > 1:
                parts.append(
                    f'<text x="{(x + lx) // 2}" y="{(y + ly) // 2}" font-size="14" text-anchor="middle" fill="#fff">{w}</text>')
            walk(node.left, lx, ly, spread // 2)
        if node.right:
            rx = x + spread
            ry = y + 100
            w = edge_weights.get((getattr(node, 'id', ''), getattr(node.right, 'id', '')), 1)
            parts.append(f'<line x1="{x}" y1="{y}" x2="{rx}" y2="{ry}" stroke="white" stroke-width="{1 + (w - 1)}"/>')
            if w > 1:
                parts.append(
                    f'<text x="{(x + rx) // 2}" y="{(y + ry) // 2}" font-size="14" text-anchor="middle" fill="#fff">{w}</text>')
            walk(node.right, rx, ry, spread // 2)
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="25" fill="#ff6b6b" stroke="white" data-id="{getattr(node, "id", "")}" data-val="{escape_text(node.val)}"/>')
        parts.append(
            f'<text x="{x}" y="{y + 6}" text-anchor="middle" font-size="18" fill="black">{escape_text(node.val)}</text>')

    for i, root in enumerate(roots):
        y0 = 50 + i * per_h
        walk(root, width // 2, y0, 200)

    parts.append('</svg>')
    return ''.join(parts)


@app.route("/bt/add-left", methods=["POST"])
def bt_add_left():
    global bt_root, bt_roots
    data = request.get_json(silent=True) or {}
    val = (data.get("value") or request.form.get("value") or "").strip()
    parent = data.get("parent") or request.form.get("parent")
    if not val:
        return jsonify({"ok": False})

    if not bt_roots:
        node = TreeNode(val)
        bt_roots.append(node)
        bt_root = bt_roots[0]
        return jsonify({"ok": True, "svg": render_bt_forest_svg(bt_roots)})

    def find_bfs_all(roots, v):
        for r in roots:
            q = [r]
            while q:
                n = q.pop(0)
                if not n:
                    continue
                try:
                    if getattr(n, 'id', None) == v or str(n.val) == str(v):
                        return n
                except Exception:
                    pass
                if n.left: q.append(n.left)
                if n.right: q.append(n.right)
        return None

    if parent:
        p = find_bfs_all(bt_roots, parent)
        if p:
            if not p.right:
                p.right = TreeNode(val)
            else:
                q = [p.right]
                placed = False
                while q and not placed:
                    n = q.pop(0)
                    if not n.left:
                        n.left = TreeNode(val);
                        placed = True;
                        break
                    if not n.right:
                        n.right = TreeNode(val);
                        placed = True;
                        break
                    q.extend([n.left, n.right])
        else:
            bt_roots.append(TreeNode(val))
    else:
        first = bt_roots[0]
        if not first.left:
            first.left = TreeNode(val)
        else:
            bt_roots.append(TreeNode(val))

    return jsonify({"ok": True, "svg": render_bt_forest_svg(bt_roots)})


@app.route("/bt/add-right", methods=["POST"])
def bt_add_right():
    global bt_root, bt_roots
    data = request.get_json(silent=True) or {}
    val = (data.get("value") or request.form.get("value") or "").strip()
    parent = data.get("parent") or request.form.get("parent")
    if not val:
        return jsonify({"ok": False})
    if not bt_roots:
        # create first root
        node = TreeNode(val)
        bt_roots.append(node)
        bt_root = bt_roots[0]
        return jsonify({"ok": True, "svg": render_bt_forest_svg(bt_roots)})

    # helper: search across all roots by id or value
    def find_bfs_all(roots, v):
        for r in roots:
            q = [r]
            while q:
                n = q.pop(0)
                if not n:
                    continue
                try:
                    if getattr(n, 'id', None) == v or str(n.val) == str(v):
                        return n
                except Exception:
                    pass
                if n.left: q.append(n.left)
                if n.right: q.append(n.right)
        return None

    if parent:
        p = find_bfs_all(bt_roots, parent)
        if p:
            if not p.left:
                p.left = TreeNode(val)
            else:
                q = [p.left]
                placed = False
                while q and not placed:
                    n = q.pop(0)
                    if not n.left:
                        n.left = TreeNode(val);
                        placed = True;
                        break
                    if not n.right:
                        n.right = TreeNode(val);
                        placed = True;
                        break
                    q.extend([n.left, n.right])
        else:
            # parent not found -> create new root
            bt_roots.append(TreeNode(val))
    else:
        # no parent -> attempt to insert under first root's right if empty, else create new root
        first = bt_roots[0]
        if not first.right:
            first.right = TreeNode(val)
        else:
            bt_roots.append(TreeNode(val))
    return jsonify({"ok": True, "svg": render_bt_forest_svg(bt_roots)})


@app.route("/bt/reset", methods=["POST"])
def bt_reset():
    global bt_root
    global bt_roots
    bt_roots.clear()
    return jsonify({"ok": True, "svg": render_bt_forest_svg(bt_roots)})


@app.route("/bt/add-root", methods=["POST"])
def bt_add_root():
    global bt_roots
    data = request.get_json(silent=True) or {}
    val = (data.get("value") or request.form.get("value") or "").strip()
    if not val:
        return jsonify({"ok": False})
    node = TreeNode(val)
    bt_roots.append(node)
    return jsonify({"ok": True, "svg": render_bt_forest_svg(bt_roots)})


def bst_search(node, val):
    if not node:
        return False
    if node.val == val:
        return True
    elif val < node.val:
        return bst_search(node.left, val)
    else:
        return bst_search(node.right, val)


def bst_find_max(node):
    if not node:
        return None
    while node.right:
        node = node.right
    return node.val


def bst_height(node):
    if not node:
        return 0
    return 1 + max(bst_height(node.left), bst_height(node.right))


def bst_delete(node, val):
    if not node:
        return None

    if val < node.val:
        node.left = bst_delete(node.left, val)
    elif val > node.val:
        node.right = bst_delete(node.right, val)
    else:
        # Case 1: No child
        if not node.left and not node.right:
            return None

        # Case 2: One child
        if not node.left:
            return node.right
        if not node.right:
            return node.left

        # Case 3: Two children
        temp = bst_find_max(node.left)
        node.val = temp
        node.left = bst_delete(node.left, temp)

    return node


def bst_detach(node, val):
    """Detach the node with value `val` from the tree and return (new_tree_root, detached_node).
    If not found, (node, None) is returned."""
    if not node:
        return node, None

    if val < node.val:
        detached = node
        return node, detached
        if not node.left and not node.right:
            return None, detached

        if not node.left:
            return node.right, detached
        if not node.right:
            return node.left, detached

        # two children: replace with max from left
        temp_val = bst_find_max(node.left)
        node.val = temp_val
        node.left = bst_delete(node.left, temp_val)
        return node, detached

        # two children: replace with max from left
        temp_val = bst_find_max(node.left)
        node.val = temp_val
        node.left = bst_delete(node.left, temp_val)
        return node, detached


# ----------------------
# Routes
# ----------------------
# Queue endpoints
@app.route("/queue/enqueue", methods=["POST"])
def queue_enqueue():
    val = request.json.get("value", "").strip()
    if not val:
        return jsonify({"ok": False})
    queue.append(val)
    return jsonify({"ok": True, "svg": render_queue_svg()})


@app.route("/queue/dequeue", methods=["POST"])
def queue_dequeue():
    if queue:
        queue.pop(0)
    return jsonify({"ok": True, "svg": render_queue_svg()})


# Stack endpoints
@app.route("/stack/push", methods=["POST"])
def stack_push():
    val = request.json.get("value", "").strip()
    if not val:
        return jsonify({"ok": False})
    stack.append(val)
    return jsonify({"ok": True, "svg": render_stack_svg()})


@app.route("/stack/pop", methods=["POST"])
def stack_pop():
    if stack:
        stack.pop()
    return jsonify({"ok": True, "svg": render_stack_svg()})


# Generic tree endpoints
@app.route("/tree/insert", methods=["POST"])
def tree_insert_route():
    global tree_root, tree_roots
    val = request.json.get("value", "").strip()
    parent = request.json.get("parent")
    if not val:
        return jsonify({"ok": False})

    new_node = TreeNode(val)
    # If there are no roots yet, create a new root
    if not tree_roots:
        tree_roots.append(new_node)
        tree_root = tree_roots[0]
        return jsonify({"ok": True, "svg": render_tree_forest_svg(tree_roots)})

    def find_bfs_all(roots, v):
        for r in roots:
            q = [r]
            while q:
                n = q.pop(0)
                if n and (getattr(n, 'id', None) == v or str(n.val) == str(v)):
                    return n
                if n:
                    if n.left: q.append(n.left)
                    if n.right: q.append(n.right)
        return None

    if parent:
        pnode = find_bfs_all(tree_roots, parent)
        if pnode:
            if not pnode.left:
                pnode.left = new_node
            elif not pnode.right:
                pnode.right = new_node
            else:
                q = [pnode.left, pnode.right]
                placed = False
                while q and not placed:
                    n = q.pop(0)
                    if not n.left:
                        n.left = new_node;
                        placed = True;
                        break
                    if not n.right:
                        n.right = new_node;
                        placed = True;
                        break
                    q.extend([n.left, n.right])
        else:
            # parent not found -> create a new root
            tree_roots.append(new_node)
    else:
        # no parent -> create a new root (support multiple trees)
        tree_roots.append(new_node)

    return jsonify({"ok": True, "svg": render_tree_forest_svg(tree_roots)})


# BST endpoints
@app.route("/bst/insert", methods=["POST"])
def bst_insert_route():
    global bst_root
    val = request.json.get("value", "").strip()
    if not val:
        return jsonify({"ok": False})
    try:
        num = int(val)
    except:
        return jsonify({"ok": False, "error": "numeric only"})
    bst_root = bst_insert(bst_root, num)
    return jsonify({"ok": True, "svg": render_generic_tree_svg(bst_root)})


@app.route("/bst/search", methods=["POST"])
def bst_search_route():
    global bst_root
    val = request.json.get("value")
    try:
        num = int(val)
    except:
        return jsonify({"ok": False})

    found = bst_search(bst_root, num)
    return jsonify({"ok": True, "found": found})


@app.route("/bst/max", methods=["GET"])
def bst_max_route():
    global bst_root
    m = bst_find_max(bst_root)
    return jsonify({"ok": True, "max": m})


@app.route("/bst/height", methods=["GET"])
def bst_height_route():
    global bst_root
    h = bst_height(bst_root)
    return jsonify({"ok": True, "height": h})


@app.route("/bst/delete", methods=["POST"])
def bst_delete_route():
    global bst_root
    val = request.json.get("value")
    try:
        num = int(val)
    except:
        return jsonify({"ok": False})

    bst_root, detached = bst_detach(bst_root, num)
    response = {"ok": True, "svg": render_generic_tree_svg(bst_root)}
    if detached:
        # store detached subtree server-side so it can be reattached by token
        token = uuid.uuid4().hex
        pending_subtrees[token] = ('bst', detached)
        response["detached_svg"] = render_generic_tree_svg(detached)
        response["detached_root"] = detached.val
        response["token"] = token
    return jsonify(response)


# ----------------------
# Graph endpoints
# ----------------------
@app.route('/graph/svg')
def graph_svg():
    return jsonify({"ok": True, "svg": render_graph_svg()})


def render_graph_svg():
    # simple circular layout
    if not graph_vertices:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 400" width="800" height="400"></svg>'
    width = 800
    height = 400
    cx = width // 2
    cy = height // 2
    r = min(cx, cy) - 80
    n = len(graph_vertices)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    coords = {}
    for i, v in enumerate(graph_vertices):
        angle = 2 * 3.14159 * i / max(1, n)
        x = int(cx + r * (0.9 * __import__('math').cos(angle)))
        y = int(cy + r * (0.9 * __import__('math').sin(angle)))
        coords[v['id']] = (x, y)

    # draw edges
    for (u, v), w in graph_edges.items():
        if u not in coords or v not in coords: continue
        x1, y1 = coords[u]
        x2, y2 = coords[v]
        parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#fff" stroke-width="{1 + (w - 1)}"/>')
        if w > 1:
            parts.append(
                f'<text x="{(x1 + x2) // 2}" y="{(y1 + y2) // 2}" font-size="14" text-anchor="middle" fill="#fff">{w}</text>')

    # draw vertices
    for v in graph_vertices:
        x, y = coords[v['id']]
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="20" fill="#7bd389" stroke="#fff" data-id="{v["id"]}" data-val="{escape_text(v.get("label", ""))}"/>')
        parts.append(
            f'<text x="{x}" y="{y + 6}" text-anchor="middle" font-size="14" fill="#000">{escape_text(v.get("label", ""))}</text>')

    parts.append('</svg>')
    return ''.join(parts)


@app.route('/graph/add-vertex', methods=['POST'])
def graph_add_vertex():
    label = (request.json.get('label') or '').strip()
    if not label:
        return jsonify({'ok': False})
    vid = uuid.uuid4().hex
    graph_vertices.append({'id': vid, 'label': label})
    return jsonify({'ok': True, 'svg': render_graph_svg(), 'id': vid})


@app.route('/graph/delete-vertex', methods=['POST'])
def graph_delete_vertex():
    vid = request.json.get('id')
    if not vid: return jsonify({'ok': False})
    global graph_vertices, graph_edges
    graph_vertices[:] = [v for v in graph_vertices if v['id'] != vid]
    # remove edges touching vid
    keys = [k for k in graph_edges.keys()]
    for k in keys:
        if vid in k:
            graph_edges.pop(k, None)
    return jsonify({'ok': True, 'svg': render_graph_svg()})


@app.route('/graph/add-edge', methods=['POST'])
def graph_add_edge():
    u = request.json.get('u')
    v = request.json.get('v')
    directed = request.json.get('directed', False)
    if not u or not v: return jsonify({'ok': False})
    # collapse parallel edges by increasing weight
    graph_edges[(u, v)] = graph_edges.get((u, v), 0) + 1
    if not directed:
        graph_edges[(v, u)] = graph_edges.get((v, u), 0) + 1
    return jsonify({'ok': True, 'svg': render_graph_svg()})


@app.route('/graph/set-weight', methods=['POST'])
def graph_set_weight():
    u = request.json.get('u')
    v = request.json.get('v')
    w = int(request.json.get('weight') or 1)
    if not u or not v: return jsonify({'ok': False})
    graph_edges[(u, v)] = w
    return jsonify({'ok': True, 'svg': render_graph_svg()})


@app.route('/graph/reset', methods=['POST'])
def graph_reset():
    graph_vertices.clear()
    graph_edges.clear()
    return jsonify({'ok': True, 'svg': render_graph_svg()})


@app.route('/tree/delete', methods=['POST'])
def tree_delete_route():
    global tree_roots
    node_id = request.json.get('id')
    if not node_id:
        return jsonify({'ok': False})

    def detach_by_id(root, target_id):
        if not root:
            return root, None
        if getattr(root, 'id', None) == target_id:
            return None, root
        if root.left:
            new_left, detached = detach_by_id(root.left, target_id)
            root.left = new_left
            if detached:
                return root, detached
        if root.right:
            new_right, detached = detach_by_id(root.right, target_id)
            root.right = new_right
            if detached:
                return root, detached
        return root, None

    detached = None
    new_roots = []
    for r in tree_roots:
        nr, d = detach_by_id(r, node_id)
        if d and getattr(r, 'id', None) == node_id:
            # root itself was detached; promote nothing (children handled by client)
            detached = d
            # skip adding this root
            continue
        if d:
            detached = d
        new_roots.append(nr)

    tree_roots[:] = [r for r in new_roots if r]
    # promote detached children to be new roots (each becomes its own tree)
    if detached:
        # for n-ary children use .children; fallback to left/right
        kids = []
        if getattr(detached, 'children', None):
            kids = [c for c in detached.children if c]
        else:
            if detached.left: kids.append(detached.left)
            if detached.right: kids.append(detached.right)

        for k in kids:
            tree_roots.append(k)

    resp = {'ok': True, 'svg': render_tree_forest_svg(tree_roots)}
    if detached:
        token = uuid.uuid4().hex
        pending_subtrees[token] = ('tree', detached)
        resp['detached_svg'] = render_tree_forest_svg([detached])
        resp['detached_root_id'] = getattr(detached, 'id', None)
        resp['token'] = token
    return jsonify(resp)


@app.route('/tree/reset', methods=['POST'])
def tree_reset():
    global tree_roots
    tree_roots.clear()
    return jsonify({"ok": True, "svg": render_tree_forest_svg(tree_roots)})


@app.route('/bt/delete', methods=['POST'])
def bt_delete_route():
    global bt_roots
    node_id = request.json.get('id')
    if not node_id:
        return jsonify({'ok': False})

    def detach_by_id(root, target_id):
        if not root:
            return root, None
        if getattr(root, 'id', None) == target_id:
            return None, root
        if root.left:
            new_left, detached = detach_by_id(root.left, target_id)
            root.left = new_left
            if detached:
                return root, detached
        if root.right:
            new_right, detached = detach_by_id(root.right, target_id)
            root.right = new_right
            if detached:
                return root, detached
        return root, None

    detached = None
    new_roots = []
    for r in bt_roots:
        nr, d = detach_by_id(r, node_id)
        if d and getattr(r, 'id', None) == node_id:
            detached = d
            continue
        if d:
            detached = d
        new_roots.append(nr)

    bt_roots[:] = [r for r in new_roots if r]
    # promote detached children to be roots
    if detached:
        kids = []
        if getattr(detached, 'children', None):
            kids = [c for c in detached.children if c]
        else:
            if detached.left: kids.append(detached.left)
            if detached.right: kids.append(detached.right)
        for k in kids:
            bt_roots.append(k)

    resp = {'ok': True, 'svg': render_bt_forest_svg(bt_roots)}
    if detached:
        token = uuid.uuid4().hex
        pending_subtrees[token] = ('bt', detached)
        resp['detached_svg'] = render_bt_forest_svg([detached])
        resp['detached_root_id'] = getattr(detached, 'id', None)
        resp['token'] = token
    return jsonify(resp)


@app.route('/reattach/<token>', methods=['POST'])
def reattach_subtree(token):
    global tree_root, bt_root, bst_root
    payload = request.get_json() or {}
    parent = payload.get('parent')

    item = pending_subtrees.pop(token, None)
    if not item:
        return jsonify({'ok': False, 'error': 'token_not_found'}), 404

    typ, node = item

    # helper to find node by id
    def find_by_id(root, tid):
        q = [root]
        while q:
            n = q.pop(0)
            if not n:
                continue
            if getattr(n, 'id', None) == tid:
                return n
            if getattr(n, 'left', None): q.append(n.left)
            if getattr(n, 'right', None): q.append(n.right)
        return None

    if typ == 'bst':
        # reinsert all values from detached subtree into bst_root
        def collect_vals(n, out):
            if not n: return
            out.append(n.val)
            collect_vals(n.left, out)
            collect_vals(n.right, out)

        vals = []
        collect_vals(node, vals)
        for v in vals:
            bst_root = bst_insert(bst_root, v)
        return jsonify({'ok': True, 'svg': render_generic_tree_svg(bst_root)})

    if typ == 'tree':
        # find parent across all tree roots
        def find_in_roots(roots, tid):
            for r in roots:
                q = [r]
                while q:
                    n = q.pop(0)
                    if not n:
                        continue
                    if getattr(n, 'id', None) == tid or str(n.val) == str(tid):
                        return n
                    # traverse both n.children (n-ary) and legacy left/right
                    if getattr(n, 'children', None):
                        q.extend([c for c in n.children if c])
                    else:
                        if n.left: q.append(n.left)
                        if n.right: q.append(n.right)
            return None

        if parent:
            p = find_in_roots(tree_roots, parent)
            if not p:
                return jsonify({'ok': False, 'error': 'parent_not_found'}), 404
            # attach under n-ary children if supported
            if getattr(p, 'children', None) is not None:
                p.children.append(node)
            else:
                if not p.left:
                    p.left = node
                elif not p.right:
                    p.right = node
                else:
                    q = [p.left, p.right]
                    placed = False
                    while q and not placed:
                        n = q.pop(0)
                        if not n.left:
                            n.left = node;
                            placed = True;
                            break
                        if not n.right:
                            n.right = node;
                            placed = True;
                            break
                        q.extend([n.left, n.right])
            # ensure edge_weights entries exist for any edges in the reattached subtree
            try:
                def collect_edges(n):
                    res = []
                    if not n: return res
                    if getattr(n, 'children', None):
                        for c in n.children:
                            res.append((getattr(n, 'id', ''), getattr(c, 'id', '')))
                            res.extend(collect_edges(c))
                    else:
                        if n.left:
                            res.append((getattr(n, 'id', ''), getattr(n.left, 'id', '')))
                            res.extend(collect_edges(n.left))
                        if n.right:
                            res.append((getattr(n, 'id', ''), getattr(n.right, 'id', '')))
                            res.extend(collect_edges(n.right))
                    return res

                for u, v in collect_edges(node):
                    if (u, v) not in edge_weights:
                        edge_weights[(u, v)] = 1
            except Exception:
                pass
            return jsonify({'ok': True, 'svg': render_tree_forest_svg(tree_roots)})
        else:
            # no parent -> create a new root (support multiple trees)
            tree_roots.append(node)
            return jsonify({'ok': True, 'svg': render_tree_forest_svg(tree_roots)})

    if typ == 'bt':
        def find_in_roots(roots, tid):
            for r in roots:
                q = [r]
                while q:
                    n = q.pop(0)
                    if not n:
                        continue
                    if getattr(n, 'id', None) == tid or str(n.val) == str(tid):
                        return n
                    if getattr(n, 'children', None):
                        q.extend([c for c in n.children if c])
                    else:
                        if n.left: q.append(n.left)
                        if n.right: q.append(n.right)
            return None

        if parent:
            p = find_in_roots(bt_roots, parent)
            if not p:
                return jsonify({'ok': False, 'error': 'parent_not_found'}), 404
            # attach respecting n-ary children if present
            if getattr(p, 'children', None) is not None:
                p.children.append(node)
            else:
                if not p.left:
                    p.left = node
                elif not p.right:
                    p.right = node
                else:
                    q = [p.left, p.right]
                    placed = False
                    while q and not placed:
                        n = q.pop(0)
                        if not n.left:
                            n.left = node;
                            placed = True;
                            break
                        if not n.right:
                            n.right = node;
                            placed = True;
                            break
                        q.extend([n.left, n.right])
            # if the detached subtree contains children, transfer any edge_weights entries into graph edge_weights keyed by ids
            try:
                def collect_edges(n):
                    res = []
                    if not n: return res
                    if getattr(n, 'children', None):
                        for c in n.children:
                            res.append((getattr(n, 'id', ''), getattr(c, 'id', '')))
                            res.extend(collect_edges(c))
                    else:
                        if n.left:
                            res.append((getattr(n, 'id', ''), getattr(n.left, 'id', '')))
                            res.extend(collect_edges(n.left))
                        if n.right:
                            res.append((getattr(n, 'id', ''), getattr(n.right, 'id', '')))
                            res.extend(collect_edges(n.right))
                    return res

                for u, v in collect_edges(node):
                    if (u, v) not in edge_weights:
                        edge_weights[(u, v)] = 1
            except Exception:
                pass
            return jsonify({'ok': True, 'svg': render_bt_forest_svg(bt_roots)})
        else:
            # no parent -> add as new root
            bt_roots.append(node)
            return jsonify({'ok': True, 'svg': render_bt_forest_svg(bt_roots)})

    return jsonify({'ok': False, 'error': 'unknown_type'}), 400

@app.route("/atlas")
def atlas():
    return render_template("atlas.html")

@app.route("/atlas/svg")
def atlas_svg():
    # Get the base SVG content (without any highlighted path)
    try:
        return atlas_graph.render_svg()
    except Exception as e:
        print(f"Error rendering SVG: {str(e)}")
        return "<svg width='1200' height='600' xmlns='http://www.w3.org/2000/svg'><text x='20' y='30' fill='red'>Error loading map. Please check server logs.</text></svg>"

@app.route("/atlas/route", methods=["POST"])
def atlas_route():
    data = request.json
    src, dst = data["src"], data["dst"]
    path, total_min, total_m = atlas_graph.shortest_path(src, dst)
    return jsonify({
        "path": path,
        "minutes": total_min,
        "meters": total_m,
        "svg": atlas_graph.render_svg(path)
    })

@app.route('/eleccirc')
def eleccirc():
    """Electrical circuit designer page."""
    return render_template('eleccirc.html')

# RUN
if __name__ == "__main__":
    init_db()
    app.run(debug=True)