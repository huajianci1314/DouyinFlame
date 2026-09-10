"""抖音网页版自动化内核。

设计要点：
- 登录态：首次弹窗扫码，检测 cookies 出现 sessionid 即认定登录成功，
  持久化 storage_state.json 供后续无头复用。
- 定位策略：抖音 DOM 类名混淆且常变，全部使用多级候选选择器 + 文本匹配兜底，
  不强依赖单一 class。
- 所有操作串行执行（全局锁），避免多个浏览器实例互相踢会话。
"""
import json
import os
import sys
import threading
import time
from typing import Callable, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# 打包成 exe 后 __file__ 指向临时解包目录，登录态必须落在 exe 同级可写目录
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(BASE_DIR, "user_data", "storage_state.json")

HOME_URL = "https://www.douyin.com/"

# 抖音已下线独立的 /message 页面，IM 改为首页右侧抽屉面板：
# 点击导航「消息」打开，会话列表节点为 [data-e2e="conversation-item"]

# 登录成功的信号：cookie 里出现 sessionid（抖音网页版的会话凭证）
LOGIN_COOKIES = {"sessionid", "sessionid_ss"}

# 聊天输入框候选定位（实测为唯一的 contenteditable 富文本编辑器）
INPUT_SELECTORS = [
    'div[contenteditable="true"][class*="editor"]',
    'div[contenteditable="true"]',
    'textarea[placeholder]',
]

# 登录弹窗（未登录时会出现）
LOGIN_MODAL_SELECTORS = [
    'div[class*="login-mask"]',
    'div[id*="login"] div[class*="qrcode"]',
    'div[class*="web-login"]',
]

browser_lock = threading.Lock()


def release_browser_lock() -> None:
    """看门狗强制放行：批处理线程卡死时由调度器调用，避免锁永久被占。"""
    try:
        browser_lock.release()
    except Exception:
        pass


def _launch(p, headless: bool):
    """启动 Chromium。优先使用默认 headless-shell 内核；若未安装则降级到
    完整 Chromium 的全新无头模式（channel="chromium"），两种安装形态都能跑。"""
    try:
        return p.chromium.launch(headless=headless)
    except Exception:
        return p.chromium.launch(headless=headless, channel="chromium")


def is_logged_in_on_disk() -> bool:
    if not os.path.exists(STATE_PATH):
        return False
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        names = {c.get("name") for c in state.get("cookies", [])}
        return bool(names & LOGIN_COOKIES)
    except Exception:
        return False


def login(headed: bool = True, timeout_sec: int = 180,
          progress: Optional[Callable[[str], None]] = None) -> dict:
    """弹出浏览器等待扫码登录，成功后持久化登录态。阻塞直至成功或超时。"""
    def say(msg: str) -> None:
        if progress:
            progress(msg)

    with browser_lock, sync_playwright() as p:
        browser = _launch(p, not headed)
        context = browser.new_context(
            viewport={"width": 1380, "height": 860},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        )
        page = context.new_page()
        say("打开抖音首页，等待扫码…")
        page.goto(HOME_URL, wait_until="domcontentloaded")

        deadline = time.time() + timeout_sec
        ok = False
        while time.time() < deadline:
            cookies = {c["name"] for c in context.cookies()}
            if cookies & LOGIN_COOKIES:
                ok = True
                break
            page.wait_for_timeout(2000)

        if ok:
            os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
            context.storage_state(path=STATE_PATH)
            say("登录成功，登录态已保存")
        else:
            say("等待扫码超时，登录未完成")

        browser.close()
        return {"ok": ok, "message": "登录成功" if ok else "扫码超时"}


def _looks_logged_out(page) -> bool:
    for sel in LOGIN_MODAL_SELECTORS:
        try:
            if page.locator(sel).first.is_visible(timeout=800):
                return True
        except Exception:
            continue
    return False


def _open_im_panel(page, say) -> bool:
    """打开首页右侧的 IM 抽屉面板，等待会话列表渲染。页面加载波动大，带重试。"""
    say("打开抖音首页…")
    page.goto(HOME_URL, wait_until="commit", timeout=45000)
    for attempt in range(3):
        page.wait_for_timeout(6000)
        try:
            page.get_by_text("消息", exact=True).first.click(timeout=8000)
        except Exception:
            continue
        try:
            page.wait_for_selector('[data-e2e="conversation-item"]', timeout=20000)
            return True
        except Exception:
            say(f"面板未打开，重试 {attempt + 1}/3")
    return False


def _find_and_open_conversation(page, nickname: str) -> bool:
    """在 IM 抽屉的会话列表中定位并打开目标会话。"""
    try:
        target = page.locator('[data-e2e="conversation-item"]').filter(has_text=nickname).first
        if target.count() > 0:
            target.click(timeout=5000)
            page.wait_for_timeout(2500)
            return True
    except Exception:
        pass

    # 列表当前页没有时，用面板内置搜索
    try:
        search_box = page.locator('input[placeholder*="搜索"]').first
        if search_box.count() > 0 and search_box.is_visible(timeout=1000):
            search_box.click()
            search_box.fill(nickname)
            page.wait_for_timeout(2000)
            result = page.locator('[data-e2e="conversation-item"]').filter(has_text=nickname).first
            if result.count() == 0:
                result = page.get_by_text(nickname, exact=True).first
            result.click(timeout=5000)
            page.wait_for_timeout(2500)
            return True
    except Exception:
        pass
    return False


def _type_and_send(page, text: str) -> None:
    """点击输入框后用真实键盘事件逐字输入，再回车发送。

    抖音 IM 的输入框是自研富文本编辑器，locator.fill() 写入的内容
    不会同步进编辑器内部状态，表现为"输入框有字但发出去是空的"，
    所以必须走 keyboard.type 触发真实按键事件。
    """
    box = None
    for sel in INPUT_SELECTORS:
        try:
            cand = page.locator(sel).last
            if cand.is_visible(timeout=1000):
                box = cand
                break
        except Exception:
            continue
    if box is None:
        raise RuntimeError("未找到聊天输入框，页面结构可能已更新")

    box.click(timeout=5000)
    page.wait_for_timeout(300)
    page.keyboard.type(text, delay=30)
    page.wait_for_timeout(500)

    # 发送靠回车（IM 面板没有发送按钮）
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)

    # editor-kit 编辑器空置时也会残留零宽字符 \u200b，必须剔除后再判断，
    # 否则发送成功会被误判为失败
    try:
        remains = (box.text_content(timeout=2000) or "").replace("\u200b", "").strip()
    except Exception:
        remains = ""
    if remains:
        raise RuntimeError("回车未触发发送，消息留在输入框")


def _norm(s: str) -> str:
    """规范化标题文本：剔除零宽字符与首尾空白。
    抖音标题节点常混入 \\u200b，直接正则锚定会失败。"""
    return (s or "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").strip()


def _find_item_by_name(page, nickname: str):
    """在会话列表可视区按标题精确匹配（规范化后）找目标条目。
    显式遍历比正则锚定抗零宽字符，又避免子串误点。"""
    items = page.locator('[data-e2e="conversation-item"]')
    n = items.count()
    want = _norm(nickname)
    for i in range(n):
        try:
            t = items.nth(i).locator('div[class$="Itemtitle"]').first.inner_text(timeout=500)
        except Exception:
            continue
        if _norm(t) == want:
            return items.nth(i)
    return None


def _conv_list_box(page):
    """会话列表的滚动容器。

    关键：聊天打开后，聊天区的消息列表可能也带 conversationConversationList
    类名，直接 .first 会误选消息列表导致滚错对象。用 has= 过滤，只取
    真正包含会话条目的那个容器。
    """
    return page.locator(
        'div[class*="conversationConversationList"]',
        has=page.locator('[data-e2e="conversation-item"]'),
    ).first


def _editor_visible(page) -> bool:
    """聊天输入框是否可见，用于确认聊天视图真的打开了。"""
    try:
        return page.locator('div[contenteditable="true"]').last.is_visible(timeout=800)
    except Exception:
        return False


def _find_in_list(page, nickname: str, list_box) -> bool:
    """回到列表顶部，逐屏滚动查找目标。

    关键：抖音会话条目的名字要约 1 秒才渲染，之前读出的是 UID 占位符。
    必须等名字渲染完再比对，否则会漏掉就在眼前的目标（实测 600ms 必漏）。
    """
    try:
        list_box.evaluate("el => el.scrollTop = 0")
    except Exception:
        pass
    page.wait_for_timeout(1500)
    last_top, stuck = None, 0
    for _ in range(30):
        try:
            target = _find_item_by_name(page, nickname)
            if target is not None:
                target.click(timeout=5000)
                page.wait_for_timeout(2500)
                # 点到不等于打开：必须验证聊天视图真的出现了。
                # 点中但聊天没弹出时继续找，交给搜索兜底，
                # 避免消息误发进上一个人的会话
                if _editor_visible(page):
                    return True
        except Exception:
            pass
        try:
            top = list_box.evaluate(
                "el => { el.scrollTop += el.clientHeight * 0.75; return el.scrollTop; }")
        except Exception:
            break
        page.wait_for_timeout(1500)
        # 滚动连续两轮原地不动：已到底或容器失效，再扫只是空转
        # （此前失败案例单个联系人空转约 69 秒，就是这里没有止损）
        if last_top is not None and abs(top - last_top) < 2:
            stuck += 1
            if stuck >= 2:
                break
        else:
            stuck = 0
        last_top = top
    return False


def _search_and_open(page, nickname: str) -> bool:
    """用消息面板自带的搜索框定位会话。

    注意 placeholder 必须精确等于「搜索」：首页还有个「搜索你感兴趣的内容」，
    模糊匹配会错点到首页搜索框去搜抖音内容。
    """
    for sel in ['input[placeholder="搜索"]', 'input[placeholder*="搜索"]']:
        try:
            box = page.locator(sel).last
            if box.count() > 0 and box.is_visible(timeout=800):
                box.click()
                box.fill(nickname)
                page.wait_for_timeout(2200)
                result = _find_item_by_name(page, nickname)
                if result is None:
                    result = page.get_by_text(nickname, exact=True).first
                result.click(timeout=5000)
                page.wait_for_timeout(2500)
                # 同样必须验证聊天真的打开：get_by_text 兜底可能点到
                # 不可交互的文本节点，直接返回 True 会把消息发进上一个会话
                if _editor_visible(page):
                    return True
                raise RuntimeError("搜索结果点击后聊天未打开")
        except Exception:
            continue
    return False


def _open_session(page, say):
    """从零进入「IM 面板已打开、会话列表就绪」的状态。"""
    say("打开抖音首页…")
    page.goto(HOME_URL, wait_until="commit", timeout=45000)
    page.wait_for_timeout(3000)
    if _looks_logged_out(page):
        raise RuntimeError("登录态已失效，请重新扫码登录")
    for attempt in range(5):
        page.wait_for_timeout(4000)
        try:
            page.get_by_text("消息", exact=True).first.click(timeout=8000)
        except Exception:
            continue
        try:
            page.wait_for_selector('[data-e2e="conversation-item"]', timeout=15000)
            return
        except Exception:
            say(f"面板未打开，重试 {attempt + 1}/5")
    raise RuntimeError("消息面板未能打开")


def send_messages(tasks: list[dict], progress=None) -> list[dict]:
    """批量发送：每个联系人都从「整页打开抖音首页 + 重开消息面板」开始。

    策略决策（2026-09-09 用户拍板）：续完一个火花就重载面板。
    此前共享面板状态的快路径在打开聊天后状态持续劣化，恢复逻辑越堆
    越复杂且失败耗时不可控；每人一次干净面板换确定性，单人均摊约 20s。
    注意：本函数不持有 browser_lock，由调用方（调度器看门狗）管理，
    以便卡死时能被外部强制中止。
    """
    def say(nickname, msg):
        if progress:
            try:
                progress(nickname, msg)
            except Exception:
                pass

    results = []
    with sync_playwright() as p:
        browser = None
        try:
            say("", "启动浏览器…")
            browser = _launch(p, headless=True)
            context = browser.new_context(
                storage_state=STATE_PATH,
                viewport={"width": 1380, "height": 860},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            )
            page = context.new_page()
            # 所有定位操作统一 8 秒上限，保证任务必然结束、锁必然释放
            page.set_default_timeout(8000)

            pending = list(tasks)
            while pending:
                t = pending.pop(0)
                nickname, cid = t["nickname"], t["cid"]
                try:
                    # 每人一次干净面板：续完上一个就整页重开，状态确定性优先
                    say(nickname, "打开抖音首页与消息面板…")
                    _open_session(page, lambda m: say(nickname, m))

                    found = False
                    for attempt in range(2):
                        # 列表容器现查，与当前 DOM 对齐
                        list_box = _conv_list_box(page)
                        say(nickname, "查找会话…")
                        if (_find_in_list(page, nickname, list_box)
                                or _search_and_open(page, nickname)):
                            found = True
                            break
                        if attempt == 0:
                            say(nickname, "查找失败，重开面板后重试…")
                            try:
                                _open_session(page, lambda m: say(nickname, m))
                            except Exception:
                                break
                    if not found:
                        results.append({"cid": cid, "nickname": nickname, "ok": False,
                                        "message": f"会话列表中未找到「{nickname}」，"
                                                   "请确认昵称与显示一致且有过聊天"})
                        continue

                    say(nickname, "输入并发送…")
                    _type_and_send(page, t["text"])
                    # _type_and_send 保证输入框已清空，这是送达的主证据：
                    # 键盘键入 + 回车后内容不留在编辑器（截图多次佐证）。
                    # 气泡增量只作附加确认——聊天区是虚拟渲染，新气泡入列时
                    # 旧气泡可能滚出渲染窗口导致计数不变，不能当失败依据。
                    try:
                        after_cnt = page.get_by_text(t["text"], exact=True).count()
                        confirmed = after_cnt > 0
                    except Exception:
                        confirmed = False
                    if confirmed:
                        msg = f"已向「{nickname}」发送：{t['text']}"
                    else:
                        msg = (f"已向「{nickname}」执行发送（输入框已清空，气泡未在"
                               "渲染窗口内捕获；如对方未收到请人工核实一次）")
                    say(nickname, "发送完成")
                    results.append({"cid": cid, "nickname": nickname,
                                    "ok": True, "message": msg})
                except Exception as e:
                    results.append({"cid": cid, "nickname": nickname, "ok": False,
                                    "message": f"发送失败：{e}"})
                    # 登录态失效对剩余任务是确定性失败，立即终止不再逐人空转
                    if "登录态已失效" in str(e):
                        for rest in pending:
                            results.append({"cid": rest["cid"], "nickname": rest["nickname"],
                                            "ok": False, "message": f"发送失败：{e}"})
                        pending.clear()
                page.wait_for_timeout(1500)
        except Exception as e:
            done = {r["cid"] for r in results}
            for t in tasks:
                if t["cid"] not in done:
                    results.append({"cid": t["cid"], "nickname": t["nickname"],
                                    "ok": False, "message": f"批量会话异常：{e}"})
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
    return results


def _parse_conversation_item(item) -> dict:
    """解析单个会话条目 Locator -> {nickname, flame_days, state, reburn}。

    state: active=火花燃烧中(天数) / reburn=重燃中(宽限倒计时) / none=无火花。
    正常火花读结构化节点 commonStreaknormalText，不受文本排版影响；
    重燃状态文本形如「重燃中 2/3」，2/3 为重燃进度（需连续互聊 3 天恢复）。
    """
    import re
    out = {"nickname": "", "flame_days": 0, "state": "none", "reburn": ""}
    # 昵称：取标题叶子节点（class 恰好以 Itemtitle 结尾），文本首行兜底。
    # 注意不能用 class*=Itemtitle，那会先匹配到 titleWrapper 容器，
    # 把「昵称 + 火花 + 时间」整段当成昵称
    try:
        name = item.locator('div[class$="Itemtitle"]').first.inner_text(timeout=1500).strip()
    except Exception:
        name = ""
    if not name:
        try:
            name = (item.inner_text(timeout=1500) or "").split("\n")[0].strip()
        except Exception:
            name = ""
    # 昵称合法性：单行、长度合理、不能是状态文案本身
    # （虚拟列表渲染滞后的行会缺名字，首行可能直接是「重燃中 2/3」这类状态文本）
    if not name or "\n" in name or len(name) > 30 or name.startswith("重燃"):
        out["nickname"] = ""
        return out
    out["nickname"] = name

    # 正常燃烧的火花天数
    try:
        t = item.locator('div[class*="StreaknormalText"]').first.inner_text(timeout=800)
        m = re.search(r"\d+", t)
        if m:
            out["flame_days"] = int(m.group())
            out["state"] = "active"
    except Exception:
        pass

    # 熄火宽限期，两种文案：
    #   「重燃中 2/3」   —— 重燃进度（连续互聊 3 天恢复）
    #   「6 天后消失」   —— 火花即将熄灭的倒计时
    try:
        full = item.inner_text(timeout=800) or ""
    except Exception:
        full = ""
    m = re.search(r"重燃中\s*(\d+)\s*/\s*(\d+)", full)
    if m:
        out["state"] = "reburn"
        out["reburn"] = f"{m.group(1)}/{m.group(2)}"
        out["flame_days"] = 0
    else:
        m = re.search(r"(\d+)\s*天后消失", full)
        if m:
            out["state"] = "reburn"
            out["reburn"] = f"{m.group(1)}天后消失"
            out["flame_days"] = 0
    return out


def list_flames(progress=None) -> dict:
    """滚动读取 IM 抽屉里的全部会话，返回每个联系人的火花天数。"""
    def say(m):
        if progress:
            try:
                progress(m)
            except Exception:
                pass

    if not is_logged_in_on_disk():
        return {"ok": False, "message": "尚未登录，请先在控制台完成扫码登录"}

    with browser_lock, sync_playwright() as p:
        browser = None
        try:
            say("启动浏览器…")
            browser = _launch(p, headless=True)
            context = browser.new_context(
                storage_state=STATE_PATH,
                viewport={"width": 1380, "height": 860},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            )
            page = context.new_page()
            page.set_default_timeout(8000)

            if not _open_im_panel(page, say):
                return {"ok": False, "message": "消息面板未能打开，页面加载异常，稍后重试"}

            seen: dict[str, dict] = {}
            seen_raw: set[str] = set()
            list_box = _conv_list_box(page)

            def scan_visible() -> None:
                """把当前可见的会话条目合并进 seen。

                性能关键：先批量拉取全部条目文本（一次 CDP 往返），
                只对没见过的新条目做慢速结构化解析，否则 156+ 条会话
                每轮全量解析要十几分钟。
                """
                items = page.locator('[data-e2e="conversation-item"]')
                n = items.count()
                texts = items.all_inner_texts() if n else []
                for i in range(n):
                    raw = texts[i] if i < len(texts) else ""
                    if not raw or raw in seen_raw:
                        continue
                    seen_raw.add(raw)
                    try:
                        rec = _parse_conversation_item(items.nth(i))
                    except Exception:
                        continue
                    name = rec["nickname"]
                    if not name or (name.isdigit() and len(name) > 10):
                        continue
                    old = seen.get(name)
                    rank = {"none": 0, "reburn": 1, "active": 2}
                    if old is None:
                        seen[name] = rec
                    elif rank.get(rec["state"], 0) > rank.get(old["state"], 0):
                        seen[name] = rec
                    elif rec["state"] == old["state"]:
                        old["flame_days"] = max(old["flame_days"], rec["flame_days"])

            # 以滚动位置是否还在变化作为终止条件（156+ 个会话），
            # 名字增量会因渲染滞后而误判"到底了"
            last_top, stale_rounds = -1.0, 0
            rounds = 0
            while stale_rounds < 3 and rounds < 45:
                rounds += 1
                scan_visible()
                say(f"已读取 {len(seen)} 个会话…")
                try:
                    top = list_box.evaluate(
                        "el => { el.scrollTop += el.clientHeight * 0.8; return el.scrollTop; }")
                except Exception:
                    break
                page.wait_for_timeout(900)
                if abs(top - last_top) < 2:
                    stale_rounds += 1
                else:
                    stale_rounds = 0
                last_top = top

            # 二次复核：滚回顶部重扫一遍。虚拟列表快速滚动时部分行只渲染出
            # UID 占位（纯数字长串），名字渲染完成后重扫即可纠正
            try:
                list_box.evaluate("el => el.scrollTop = 0")
            except Exception:
                pass
            page.wait_for_timeout(2500)
            scan_visible()

            say(f"读取完成，共 {len(seen)} 个会话")
            return {"ok": True, "items": list(seen.values())}
        except Exception as e:
            return {"ok": False, "message": f"抓取失败：{e}"}
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
