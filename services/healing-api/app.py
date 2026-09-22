# -*- coding: utf-8 -*-
"""
心灵驿站 (Healing Station) 内容服务
=================================================
为 aimemory.cafe 官网「心灵驿站」频道提供内容能力：

  公开读取（无需鉴权，CDN/前端直接调用）
    GET  /                    服务信息
    GET  /health              健康检查
    GET  /meta                频道元信息（条数、分类、最后更新时间）
    GET  /posts               图文列表（分页 / 分类 / 关键词 / 时间过滤）
    GET  /posts/{id}          单篇详情
    GET  /feed.json           全量快照（供静态缓存 / 第三方聚合）
    GET  /images/{name}       已上传的配图
    GET  /docs                接口文档

  受保护写入（需 Authorization: Bearer <TOKEN>，供内容平台每日推送）
    POST   /posts             新增/更新单篇
    POST   /sync              批量同步（幂等 upsert，内容平台每日调用）
    DELETE /posts/{id}        下架单篇
    POST   /upload            上传配图，返回可直接引用的 URL
    GET    /sync-log          同步审计日志

运行：uvicorn app:app --host 127.0.0.1 --port 3010
存储：SQLite（data/healing.db）+ 本地图片（data/images）
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMAGE_DIR = os.path.join(DATA_DIR, "images")
DB_PATH = os.path.join(DATA_DIR, "healing.db")
SEED_PATH = os.path.join(BASE_DIR, "seed.json")

API_TOKEN = os.environ.get("HEALING_API_TOKEN", "").strip()
CST = timezone(timedelta(hours=8))  # 中国标准时间
SITE = "https://aimemory.cafe"

os.makedirs(IMAGE_DIR, exist_ok=True)

# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    summary      TEXT DEFAULT '',
    content      TEXT DEFAULT '',
    cover        TEXT DEFAULT '',
    images       TEXT DEFAULT '[]',
    category     TEXT DEFAULT '治愈',
    tags         TEXT DEFAULT '[]',
    author       TEXT DEFAULT '',
    source       TEXT DEFAULT '',
    source_url   TEXT DEFAULT '',
    published_at TEXT,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_pub  ON posts(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_cat  ON posts(category);

CREATE TABLE IF NOT EXISTS sync_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT,
    remote_ip  TEXT,
    source     TEXT,
    mode       TEXT,
    received   INTEGER,
    inserted   INTEGER,
    updated    INTEGER,
    skipped    INTEGER
);
"""


def now_iso() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
    seed_if_empty()
    ensure_token()


def ensure_token() -> None:
    """首次启动时生成访问令牌并落盘，便于内容平台取用。"""
    global API_TOKEN
    tok_file = os.path.join(DATA_DIR, "api_token.txt")
    if API_TOKEN:
        with open(tok_file, "w", encoding="utf-8") as f:
            f.write(API_TOKEN)
        return
    if os.path.exists(tok_file):
        API_TOKEN = open(tok_file, encoding="utf-8").read().strip()
    else:
        API_TOKEN = "amh_" + hashlib.sha256(os.urandom(32)).hexdigest()[:40]
        with open(tok_file, "w", encoding="utf-8") as f:
            f.write(API_TOKEN)
        os.chmod(tok_file, 0o600)
        print(f"[healing] 已生成 API 令牌 -> {tok_file}")


def seed_if_empty() -> None:
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
    if n or not os.path.exists(SEED_PATH):
        return
    with open(SEED_PATH, encoding="utf-8") as f:
        items = json.load(f)
    with db() as conn:
        for it in items:
            conn.execute(
                """INSERT OR IGNORE INTO posts
                   (id,title,summary,content,cover,images,category,tags,author,source,
                    source_url,published_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    it["id"], it["title"], it.get("summary", ""), it.get("content", ""),
                    it.get("cover", ""), json.dumps(it.get("images", []), ensure_ascii=False),
                    it.get("category", "治愈"),
                    json.dumps(it.get("tags", []), ensure_ascii=False),
                    it.get("author", ""), it.get("source", "金润瀚宇"),
                    it.get("source_url", ""), it.get("published_at", now_iso()),
                    now_iso(), now_iso(),
                ),
            )
    print(f"[healing] 已导入初始内容 {len(items)} 篇")


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def row_to_post(r: sqlite3.Row, with_content: bool = True) -> Dict[str, Any]:
    d = dict(r)
    for k in ("images", "tags"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except Exception:
            d[k] = []
    if not with_content:
        d.pop("content", None)
    return d


def make_id(item: "PostIn") -> str:
    raw = f"{item.source or ''}|{item.title}|{item.published_at or ''}"
    return "hp_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


def safe_name(name: str) -> str:
    name = os.path.basename(name or "image")
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"[^A-Za-z0-9._-]", "", stem)[:60] or "image"
    ext = ext.lower() if ext.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif") else ".jpg"
    return f"{int(time.time() * 1000)}_{stem}{ext}"


async def require_token(authorization: Optional[str] = Header(default=None)) -> str:
    if not API_TOKEN:
        raise HTTPException(503, "服务未配置访问令牌")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "缺少 Authorization: Bearer <TOKEN>")
    if not _consteq(authorization[7:].strip(), API_TOKEN):
        raise HTTPException(403, "令牌无效")
    return "ok"


def _consteq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a, b):
        r |= ord(x) ^ ord(y)
    return r == 0


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------
class PostIn(BaseModel):
    id: Optional[str] = None
    title: str = Field(..., min_length=1, max_length=200)
    summary: Optional[str] = ""
    content: Optional[str] = ""
    cover: Optional[str] = ""
    images: Optional[List[str]] = []
    category: Optional[str] = "治愈"
    tags: Optional[List[str]] = []
    author: Optional[str] = ""
    source: Optional[str] = ""
    source_url: Optional[str] = ""
    published_at: Optional[str] = None


class SyncIn(BaseModel):
    mode: str = Field("upsert", description="upsert=按 id 覆盖更新；replace_source=先清空同一 source 再写入")
    source: Optional[str] = ""
    posts: List[PostIn] = []


# --------------------------------------------------------------------------
# 应用
# --------------------------------------------------------------------------
# nginx 会剥掉 /api/healing 前缀再转发，这里声明 root_path，
# 使 /docs 与 openapi.json 生成正确的对外地址（https://aimemory.cafe/api/healing/...）
ROOT_PATH = os.environ.get("ROOT_PATH", "/api/healing")

app = FastAPI(
    title="心灵驿站 · 内容 API",
    description="AI回忆录官网「心灵驿站」频道内容接口。支持内容平台每日推送更新。",
    version="1.0.0",
    root_path=ROOT_PATH,
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# 注意：nginx 以 location /api/healing/ -> http://127.0.0.1:3010/ 反代（带尾斜杠即去掉前缀），
# 因此服务自身的路由挂在根路径上，对外访问时自然带上 /api/healing 前缀。
api = APIRouter()


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/", include_in_schema=False)
def root() -> Dict[str, Any]:
    return {
        "service": "AIMemory 心灵驿站 内容 API",
        "version": "1.0.0",
        "docs": f"{SITE}/api/healing/docs",
        "endpoints": {
            "公开": ["GET /api/healing/meta", "GET /api/healing/posts",
                     "GET /api/healing/posts/{id}", "GET /api/healing/feed.json"],
            "写入(需令牌)": ["POST /api/healing/posts", "POST /api/healing/sync",
                             "DELETE /api/healing/posts/{id}", "POST /api/healing/upload"],
        },
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
    return {"status": "ok", "posts": n, "time": now_iso()}


@app.get("/meta")
def meta() -> Dict[str, Any]:
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
        rows = conn.execute(
            "SELECT category, COUNT(*) c FROM posts GROUP BY category ORDER BY c DESC"
        ).fetchall()
        last = conn.execute("SELECT MAX(updated_at) m FROM posts").fetchone()["m"]
        latest = conn.execute(
            "SELECT MAX(published_at) m FROM posts").fetchone()["m"]
    return {
        "channel": "心灵驿站",
        "description": "每天一点温柔，陪你走过想念的时刻。",
        "total": total,
        "categories": [{"name": r["category"], "count": r["c"]} for r in rows],
        "last_updated": last,
        "latest_published_at": latest,
        "auto_update": {
            "supported": True,
            "push_endpoint": f"{SITE}/api/healing/sync",
            "auth": "Authorization: Bearer <TOKEN>",
            "frequency": "每日（建议 06:00-08:00 或 20:00-21:00）",
        },
    }


@app.get("/posts")
def list_posts(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[str] = Query(None, description="只返回该时间之后发布的（ISO8601）"),
    with_content: bool = Query(False, description="是否返回正文"),
) -> Dict[str, Any]:
    where, args = [], []
    if category and category not in ("全部", "all"):
        where.append("category = ?")
        args.append(category)
    if since:
        where.append("published_at >= ?")
        args.append(since)
    if q:
        where.append("(title LIKE ? OR summary LIKE ? OR content LIKE ?)")
        args += [f"%{q}%"] * 3
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM posts {clause}", args).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM posts {clause} ORDER BY published_at DESC, created_at DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [row_to_post(r, with_content) for r in rows],
    }


@app.get("/posts/{pid}")
def get_post(pid: str) -> Dict[str, Any]:
    with db() as conn:
        r = conn.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404, "内容不存在")
    return row_to_post(r)


@app.get("/feed.json")
def feed(limit: int = Query(60, ge=1, le=300)) -> JSONResponse:
    """全量快照，方便静态缓存或第三方聚合。"""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM posts ORDER BY published_at DESC LIMIT ?", (limit,)
        ).fetchall()
        last = conn.execute("SELECT MAX(updated_at) m FROM posts").fetchone()["m"]
    return JSONResponse(
        {
            "channel": "心灵驿站",
            "site": SITE,
            "updated_at": last,
            "count": len(rows),
            "items": [row_to_post(r) for r in rows],
        },
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.post("/posts", dependencies=[Depends(require_token)])
def upsert_post(item: PostIn) -> Dict[str, Any]:
    pid = item.id or make_id(item)
    ts = now_iso()
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM posts WHERE id = ?", (pid,)).fetchone()
        conn.execute(
            """INSERT INTO posts
               (id,title,summary,content,cover,images,category,tags,author,source,
                source_url,published_at,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, summary=excluded.summary, content=excluded.content,
                 cover=excluded.cover, images=excluded.images, category=excluded.category,
                 tags=excluded.tags, author=excluded.author, source=excluded.source,
                 source_url=excluded.source_url, published_at=excluded.published_at,
                 updated_at=excluded.updated_at""",
            (
                pid, item.title, item.summary or "", item.content or "", item.cover or "",
                json.dumps(item.images or [], ensure_ascii=False),
                item.category or "治愈",
                json.dumps(item.tags or [], ensure_ascii=False),
                item.author or "", item.source or "外部内容平台", item.source_url or "",
                item.published_at or ts, ts, ts,
            ),
        )
    return {"ok": True, "id": pid, "action": "updated" if exists else "inserted"}


@app.post("/sync", dependencies=[Depends(require_token)])
def sync(payload: SyncIn, request_ip: str = Header(default="", alias="X-Real-IP")) -> Dict[str, Any]:
    """
    内容平台每日推送入口。幂等：同一 id / 同一 source+title 重复推送只会更新，不会重复。
    mode=upsert         按 id 覆盖更新（默认）
    mode=replace_source 先删除该 source 的全部旧内容，再写入本批（适合"今日份"整体替换）
    """
    ts = now_iso()
    inserted = updated = skipped = 0
    src = payload.source or "外部内容平台"

    with db() as conn:
        if payload.mode == "replace_source":
            conn.execute("DELETE FROM posts WHERE source = ?", (src,))

        for item in payload.posts:
            if not item.title.strip():
                skipped += 1
                continue
            pid = item.id or make_id(item)
            existed = conn.execute("SELECT 1 FROM posts WHERE id = ?", (pid,)).fetchone()
            conn.execute(
                """INSERT INTO posts
                   (id,title,summary,content,cover,images,category,tags,author,source,
                    source_url,published_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, summary=excluded.summary, content=excluded.content,
                     cover=excluded.cover, images=excluded.images, category=excluded.category,
                     tags=excluded.tags, author=excluded.author,
                     source_url=excluded.source_url, published_at=excluded.published_at,
                     updated_at=excluded.updated_at""",
                (
                    pid, item.title.strip(), item.summary or "", item.content or "",
                    item.cover or "", json.dumps(item.images or [], ensure_ascii=False),
                    item.category or "治愈",
                    json.dumps(item.tags or [], ensure_ascii=False),
                    item.author or src, src, item.source_url or "",
                    item.published_at or ts, ts, ts,
                ),
            )
            if existed:
                updated += 1
            else:
                inserted += 1

        conn.execute(
            """INSERT INTO sync_log (at,remote_ip,source,mode,received,inserted,updated,skipped)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ts, request_ip, src, payload.mode, len(payload.posts), inserted, updated, skipped),
        )
        total = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]

    return {
        "ok": True,
        "received": len(payload.posts),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "total": total,
        "at": ts,
    }


@app.delete("/posts/{pid}", dependencies=[Depends(require_token)])
def delete_post(pid: str) -> Dict[str, Any]:
    with db() as conn:
        cur = conn.execute("DELETE FROM posts WHERE id = ?", (pid,))
    if cur.rowcount == 0:
        raise HTTPException(404, "内容不存在")
    return {"ok": True, "deleted": pid}


@app.post("/upload", dependencies=[Depends(require_token)])
async def upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "单张图片请小于 8MB")
    name = safe_name(file.filename or "image.jpg")
    path = os.path.join(IMAGE_DIR, name)
    with open(path, "wb") as f:
        f.write(raw)
    return {"ok": True, "url": f"{SITE}/api/healing/images/{name}", "name": name, "size": len(raw)}


@app.get("/images/{name}", include_in_schema=False)
def get_image(name: str):
    path = os.path.join(IMAGE_DIR, os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(404, "图片不存在")
    return FileResponse(path)


@app.get("/sync-log", dependencies=[Depends(require_token)])
def sync_log(limit: int = Query(30, ge=1, le=200)) -> Dict[str, Any]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


app.include_router(api)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=3010)
