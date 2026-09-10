"""FastAPI 服务：REST API + 静态控制台。启动：python -m uvicorn app.main:app --port 8765"""
import os
import sys
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from . import db, bot, scheduler

BASE_DIR = db.BASE_DIR
# PyInstaller onedir 把只读资源放进 _internal（sys._MEIPASS），
# 可写数据（数据库/登录态）由 db/bot 自行落到 exe 同级目录
if getattr(sys, "frozen", False):
    STATIC_DIR = os.path.join(sys._MEIPASS, "static")
else:
    STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="抖音续火控制台", docs_url=None, redoc_url=None)


class ContactIn(BaseModel):
    nickname: str = Field(min_length=1, description="抖音会话里显示的昵称")
    remark: str = ""
    message: str = Field(min_length=1, description="要发送的续火内容")
    send_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    enabled: bool = True


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    if scheduler.scheduler.running:
        scheduler.scheduler.shutdown(wait=False)


# ---------- 页面 ----------

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---------- 状态 ----------

@app.get("/api/state")
def state():
    data = db.export_state()
    data["jobs"] = scheduler.job_count()
    return data


# ---------- 联系人 ----------

@app.post("/api/contacts")
def create_contact(body: ContactIn):
    cid = db.add_contact(body.nickname, body.message, body.send_time, body.remark)
    if not body.enabled:
        db.update_contact(cid, enabled=0)
    scheduler.reschedule()
    db.add_log("INFO", f"新增联系人「{body.nickname}」，每日 {body.send_time} 续火")
    return {"ok": True, "id": cid}


@app.put("/api/contacts/{cid}")
def edit_contact(cid: int, body: ContactIn):
    if not db.get_contact(cid):
        raise HTTPException(404, "联系人不存在")
    db.update_contact(cid, nickname=body.nickname, remark=body.remark,
                      message=body.message, send_time=body.send_time,
                      enabled=1 if body.enabled else 0)
    scheduler.reschedule()
    return {"ok": True}


@app.delete("/api/contacts/{cid}")
def remove_contact(cid: int):
    contact = db.get_contact(cid)
    if not contact:
        raise HTTPException(404, "联系人不存在")
    db.delete_contact(cid)
    scheduler.reschedule()
    db.add_log("INFO", f"删除联系人「{contact['nickname']}」")
    return {"ok": True}


# ---------- 发送 ----------

def _async_send(cid: int, trigger: str) -> None:
    scheduler.enqueue_one(cid, trigger)


@app.post("/api/contacts/{cid}/send")
def send_one(cid: int):
    if not db.get_contact(cid):
        raise HTTPException(404, "联系人不存在")
    return scheduler.enqueue_one(cid, "手动")


@app.post("/api/send_all")
def send_all():
    return scheduler.enqueue_all("手动全部")


# ---------- 登录 ----------

@app.post("/api/login")
def start_login():
    if bot.is_logged_in_on_disk():
        return {"ok": False, "message": "已有有效登录态。如需换号请先清除登录"}

    def worker():
        try:
            bot.login(headed=True, progress=lambda m: db.add_log("INFO", m, "登录"))
        except Exception as e:
            db.add_log("ERROR", f"登录流程异常：{e}", "登录")

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "message": "浏览器窗口已弹出，请用抖音 App 扫码，成功后窗口自动关闭"}


@app.delete("/api/login")
def clear_login():
    import os
    if os.path.exists(bot.STATE_PATH):
        os.remove(bot.STATE_PATH)
    db.add_log("WARN", "登录态已清除")
    return {"ok": True}


# ---------- 火花抓取与导入 ----------

SCRAPE: dict = {"status": "idle", "items": [], "error": None}


@app.post("/api/scrape_flames")
def scrape_flames():
    if SCRAPE["status"] == "running":
        return {"ok": False, "message": "抓取已在进行中"}
    SCRAPE.update(status="running", items=[], error=None)

    def worker():
        try:
            result = bot.list_flames(
                progress=lambda m: db.add_log("INFO", m, "抓取火花"))
            SCRAPE["status"] = "done" if result["ok"] else "error"
            SCRAPE["items"] = result.get("items", [])
            SCRAPE["error"] = None if result["ok"] else result.get("message")
            if result["ok"]:
                flames = sum(1 for it in SCRAPE["items"] if it["state"] == "active")
                reburns = sum(1 for it in SCRAPE["items"] if it["state"] == "reburn")
                # 同步已有联系人的火花状态（昵称匹配）
                synced = sum(
                    1 for it in SCRAPE["items"]
                    if db.set_flame(it["nickname"], it["flame_days"],
                                    it["state"], it["reburn"])
                )
                db.add_log("INFO", f"抓取完成：{len(SCRAPE['items'])} 个会话，"
                                   f"{flames} 个燃烧中，{reburns} 个重燃中，"
                                   f"已同步 {synced} 个联系人", "抓取火花")
            else:
                db.add_log("ERROR", result.get("message", ""), "抓取火花")
        except Exception as e:
            SCRAPE["status"] = "error"
            SCRAPE["error"] = str(e)
            db.add_log("ERROR", f"抓取异常：{e}", "抓取火花")

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "message": "开始抓取会话火花，请留意日志"}


@app.get("/api/scrape_flames")
def scrape_status():
    return SCRAPE


class ImportItem(BaseModel):
    nickname: str
    flame_days: int = 0
    state: str = ""
    reburn: str = ""


class ImportIn(BaseModel):
    items: list[ImportItem]
    message: str = Field(min_length=1)
    send_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


@app.post("/api/import_flames")
def import_flames(body: ImportIn):
    existing = {c["nickname"] for c in db.list_contacts()}
    added, skipped = [], 0
    for it in body.items:
        if it.nickname in existing:
            skipped += 1
            continue
        if it.state == "reburn":
            remark = f"重燃中{it.reburn}"
        elif it.flame_days:
            remark = f"火花{it.flame_days}天"
        else:
            remark = ""
        db.add_contact(it.nickname, body.message, body.send_time, remark)
        db.set_flame(it.nickname, it.flame_days, it.state, it.reburn)
        existing.add(it.nickname)
        added.append(it.nickname)
    scheduler.reschedule()
    db.add_log("INFO", f"导入完成：新增 {len(added)} 个联系人"
                       f"{'，跳过已存在 ' + str(skipped) if skipped else ''}")
    return {"ok": True, "added": len(added), "skipped": skipped}


# ---------- 日志 ----------

@app.get("/api/logs")
def logs(after_id: int = 0, limit: int = 100):
    return db.list_logs(limit=limit, after_id=after_id)


@app.delete("/api/logs")
def clear_logs():
    db.clear_logs()
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
