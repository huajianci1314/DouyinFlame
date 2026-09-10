"""每日定时调度：cron 触发入队，单工人线程串行消费，浏览器会话内批量发送。"""
import queue
import threading
import time
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from . import db, bot

# 线程池要装得下同时触发的全部任务（并发导入的联系人时间相同）
scheduler = BackgroundScheduler(
    timezone="Asia/Shanghai", executors={"default": ThreadPoolExecutor(32)}
)

# 发送队列 + 单工人：所有发送合并进一次浏览器会话，快且稳
_queue: "queue.Queue[tuple[int, str]]" = queue.Queue()
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()


def _enqueue(cid: int, trigger: str) -> None:
    _queue.put((cid, trigger))
    _ensure_worker()


def _ensure_worker() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker, daemon=True)
            _worker_thread.start()


def _worker() -> None:
    while True:
        try:
            _process_batch()
        except Exception as e:
            # 工人线程绝不静默死亡
            try:
                db.add_log("ERROR", f"发送队列异常：{e}")
            except Exception:
                pass


def _process_batch() -> None:
    while True:
        batch = [_queue.get()]
        # 把堆积的任务一次取空，合并成一批
        while True:
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break

        today = time.strftime("%Y-%m-%d")
        tasks, skip_msgs = [], []
        seen_cids = set()
        for cid, trigger in batch:
            if cid in seen_cids:
                continue
            seen_cids.add(cid)
            contact = db.get_contact(cid)
            if not contact or not contact["enabled"]:
                continue
            # 定时/补发触发当天已发过则不重复发（手动触发不受限）。
            # 补发也要查：开机补发入队后 cron misfire 又补一枪的竞态，
            # 两批先后到达时靠 last_sent 拦住第二次
            if trigger in ("定时", "补发") and contact["last_sent"] == today:
                skip_msgs.append((contact["nickname"], "今天已续过火，跳过"))
                continue
            tasks.append({"cid": cid, "nickname": contact["nickname"],
                          "text": contact["message"], "trigger": trigger})

        for nickname, msg in skip_msgs:
            db.add_log("INFO", msg, nickname)
        if not tasks:
            continue

        trig_by_cid = {t["cid"]: t["trigger"] for t in tasks}
        results = _run_batch_with_watchdog(tasks)
        for r in results:
            db.mark_sent(r["cid"], r["ok"])
            level = "INFO" if r["ok"] else "ERROR"
            db.add_log(level, f"[{trig_by_cid.get(r['cid'], '定时')}] {r['message']}",
                       r["nickname"])


def _run_batch_with_watchdog(tasks: list[dict], timeout: int | None = None) -> list[dict]:
    """跑一批发送，看门狗兜底：浏览器崩溃导致驱动卡死时强制中止本批，
    释放浏览器锁，保证队列后续任务不受牵连。

    超时随批量人数伸缩：每人重开干净面板后单人均摊约 40-60s，
    固定 900s 会让 15 人以上批次被误杀。
    """
    if timeout is None:
        timeout = min(1800, max(900, 150 * len(tasks) + 300))
    holder: dict = {}

    def run():
        try:
            with bot.browser_lock:
                holder["r"] = bot.send_messages(
                    tasks,
                    progress=lambda nickname, m: db.add_log("INFO", m, nickname),
                )
        except Exception as e:
            holder["r"] = [{"cid": t["cid"], "nickname": t["nickname"], "ok": False,
                            "message": f"批量会话异常：{e}"} for t in tasks]

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        db.add_log("ERROR", f"批量发送超过 {timeout}s 未完成，强制中止本批")
        bot.release_browser_lock()
        return [{"cid": t["cid"], "nickname": t["nickname"], "ok": False,
                 "message": "批量发送超时被中止，稍后自动重试下一轮"} for t in tasks]
    return holder.get("r", [{"cid": t["cid"], "nickname": t["nickname"], "ok": False,
                             "message": "批量会话无结果"} for t in tasks])


def _job(cid: int) -> None:
    _enqueue(cid, "定时")


def _sync_jobs() -> None:
    """让调度器与数据库中的联系人保持一致。"""
    for job in scheduler.get_jobs():
        if job.id.startswith("contact_"):
            job.remove()
    for c in db.enabled_contacts():
        hour, minute = map(int, c["send_time"].split(":"))
        scheduler.add_job(
            _job, "cron", args=[c["id"]], id=f"contact_{c['id']}",
            hour=hour, minute=minute, misfire_grace_time=3600,
        )


def start() -> None:
    scheduler.start()
    _sync_jobs()
    db.add_log("INFO", "调度器已启动，定时任务已恢复")
    catch_up_missed()


def _minutes_of_day(hm: str) -> int:
    """HH:MM 转当天分钟数。字符串直接比较对 '9:30' 这类不补零的
    写法会出错（'9' > '1'），必须转数值。"""
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def catch_up_missed() -> dict:
    """启动补发：计划时间已过但今天尚未发送的启用联系人自动入队。

    覆盖关机错过 cron 的场景（APScheduler misfire 宽限只有 1 小时）。
    判定权威是 contacts.last_sent，比解析日志可靠；send_time 未到的
    交给正常 cron，提前发反而打乱节奏。"""
    today = time.strftime("%Y-%m-%d")
    now_min = int(time.strftime("%H")) * 60 + int(time.strftime("%M"))
    missed = [c for c in db.enabled_contacts()
              if c["last_sent"] != today
              and _minutes_of_day(c["send_time"]) <= now_min]
    for c in missed:
        _enqueue(c["id"], "补发")
    if missed:
        names = "、".join(c["nickname"] for c in missed)
        db.add_log("INFO", f"启动补发：{len(missed)} 个联系人今日计划已过未发送，"
                           f"已自动入队（{names}）")
    return {"ok": True, "missed": [c["nickname"] for c in missed]}


def reschedule() -> None:
    if scheduler.running:
        _sync_jobs()


def enqueue_one(cid: int, trigger: str = "手动") -> dict:
    contact = db.get_contact(cid)
    if not contact:
        return {"ok": False, "message": "联系人不存在"}
    _enqueue(cid, trigger)
    return {"ok": True, "message": f"「{contact['nickname']}」已加入发送队列"}


def enqueue_all(trigger: str = "手动全部") -> dict:
    n = 0
    for c in db.enabled_contacts():
        _enqueue(c["id"], trigger)
        n += 1
    return {"ok": True, "message": f"{n} 个联系人已加入发送队列"}


def job_count() -> int:
    return sum(1 for j in scheduler.get_jobs() if j.id.startswith("contact_"))
