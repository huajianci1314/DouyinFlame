"""UIA 探针 v2：更宽松地枚举抖音所有顶层控件及其子树。"""
import sys
import time
import subprocess
import uiautomation as auto

OUT = r"D:/PythonProject/项目a/uia_dump.txt"
auto.SetGlobalSearchTimeout(2)


def get_douyin_pids():
    out = subprocess.run(["tasklist", "/FO", "CSV"], capture_output=True,
                         text=True, encoding="gbk", errors="ignore").stdout
    pids = []
    for line in out.splitlines():
        if "douyin" in line.lower():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                pids.append(int(parts[1]))
    return pids


def main():
    pids = set(get_douyin_pids())
    print("douyin pids:", len(pids))
    root = auto.GetRootControl()
    tops = list(root.GetChildren())
    print(f"root children: {len(tops)}")
    # 打印所有属于 douyin 的顶层控件（含无标题、非窗口）
    matched = []
    for c in tops:
        try:
            pid = c.ProcessId
        except Exception:
            continue
        if pid in pids:
            matched.append(c)
            try:
                print(f"  [{c.ControlTypeName}] name={c.Name!r} cls={c.ClassName} pid={pid} rect={c.BoundingRectangle}")
            except Exception:
                print("  [err reading]")
    print(f"matched top-level: {len(matched)}")

    lines = []

    def dump(ctrl, depth, max_depth, cap):
        if depth > max_depth or len(lines) >= cap:
            return
        try:
            name = (ctrl.Name or "").replace("\n", " ")
            line = ("  " * depth + ctrl.ControlTypeName
                    + (f" name={name!r}" if name else "")
                    + f" cls={ctrl.ClassName}"
                    + (f" autoId={ctrl.AutomationId}" if ctrl.AutomationId else ""))
            lines.append(line[:160])
        except Exception:
            return
        try:
            for c in ctrl.GetChildren():
                dump(c, depth + 1, max_depth, cap)
        except Exception:
            pass

    for c in matched:
        try:
            lines.append("=" * 60)
            lines.append(f"TOP {c.ControlTypeName} name={c.Name!r} cls={c.ClassName}")
        except Exception:
            pass
        dump(c, 0, 6, 500)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {len(lines)} lines -> {OUT}")


if __name__ == "__main__":
    main()
