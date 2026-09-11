"""Read-only file inventory. Outputs one Markdown page as JSON for apply_patch.

Never reads credentials, image pixels or private database payloads. Third-party
roles are inferred from package/path/type; they are not a source audit.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOTS = {
    "development": Path("C:/Users/ASUS/Desktop/delta force/delta-loot-assistant"),
    "installed": Path("C:/Users/ASUS/Desktop/delta force/app/DeltaLootAssistant"),
    "data": Path("C:/Users/ASUS/AppData/Local/DeltaLootAssistant"),
}
PAGE_SIZE = 500
OWN = {
    "AGENTS.md": "项目说明及后续开发约束；详细状态、原理和操作请读主文档",
    "README.md": "启动、使用、依赖与当前限制说明",
    ".gitignore": "告诉 Git 忽略虚拟环境、数据库、构建产物和缓存",
    "pyproject.toml": "Python 依赖范围、安装入口、pytest 与 Ruff 配置",
    "start.cmd": "切换到源码目录，使用 .venv 的 pythonw 启动桌面应用",
    "build.ps1": "默认离线检查并构建到外层 app；显式参数才安装依赖，缓存位于 _archive",
    "smoke.sqlite3": "项目内旧冒烟运行数据库文件；非当前 paths.py 指定的真实价格库",
    "__main__.py": "Python 模块启动入口，进入桌面事件循环",
    "__init__.py": "标识项目 Python 包；主包还导出领域模型和版本号",
    "app.py": "创建 Qt 应用、控制器、窗口，注册和卸载全局热键",
    "models.py": "物品定义、实例、容器、价格包、位置和求解结果等数据结构",
    "paths.py": "统一决定资源、真实数据库、Token、模板和报告所在目录",
    "capture.py": "使用 mss 读取活动显示器画面并检查分辨率",
    "hotkeys.py": "使用 Windows RegisterHotKey 接收 F8/F9；不生成游戏按键",
    "secrets.py": "使用 Windows DPAPI 在当前用户身份下加解密 Token",
    "orzice.py": "授权接口访问、物品 ID 关联、SQLite 目录快照和 24 小时间隔保护",
    "catalog.py": "加载目录，按名称或别名搜索，归一化 OCR 文本并模糊匹配",
    "icon_cache.py": "按接口图标 URL 下载缓存，建立物品 ID 到本地 PNG 的索引",
    "pricing.py": "JSON/CSV 价格包导入、版本回滚、数量/耐久/子物品估价",
    "recognition.py": "区域校准、RapidOCR、格子定位、图标特征、数量读取及候选生成",
    "scan_session.py": "多截图会话、重叠去重、逐项确认和扫描完成状态",
    "value_ranking.py": "计算参考价、目录占格、单格价值及替换建议的阻断条件",
    "controller.py": "连接截图、识别、数据库、纠错和估价/演示求解流程",
    "optimizer.py": "CP-SAT 放置候选和约束优化原型；真实截图尚未启用完整优化",
    "action_planner.py": "从目标放置生成拿取、丢弃、拆装、合并和临时空间提示原型",
    "screenshot_report.py": "离线读取截图及价格库，生成待核对 Markdown 估价报告",
    "main_window.py": "桌面侧窗、导入截图、物品表、候选裁剪、价格设置和结果展示",
    "calibration.py": "Qt 区域坐标编辑和截图预览；尚需完善格距/布局自动校准",
    "layout_preview.py": "绘制演示优化结果的容器格子及目标物品位置",
    "app_config.json": "1080p、热键、匹配阈值和求解时间上限等演示配置",
    "sample_catalog.json": "仓库自带演示物品和容器，非从授权接口取得的全物品目录",
    "sample_price_pack.json": "仓库自带虚构演示价格，非真实市场快照",
    "entrypoint.py": "PyInstaller 启动入口及隔离数据、禁用网络的本地 OCR/界面冒烟检查",
    "delta_loot_assistant.spec": "PyInstaller 打包文件与依赖收集配置",
    "installer.iss": "Inno Setup 安装位置、文件、快捷方式和卸载器配置",
    "conftest.py": "测试共用的虚拟物品、容器、价格库夹具，不调用真实接口",
    "test_action_planner.py": "检验无空格循环交换时的临时空间提示",
    "test_catalog.py": "检验名称模糊匹配及纯数字不是物品名",
    "test_controller.py": "检验控制器装配关系及确认裁剪保存",
    "test_icon_cache.py": "假下载器检验共享 URL 只下载一次及索引生成",
    "test_optimizer.py": "检验装箱、隔断、锁定、配件、合并、容器替换及简单暴力对照",
    "test_orzice.py": "假客户端检验快照、ID/尺寸映射和重复请求保护",
    "test_pricing.py": "检验价格读写、回滚、数量、耐久及父子物品不重复计价",
    "test_recognition.py": "合成图和假 OCR 检验分辨率、校准、数量及人工确认流程",
    "test_scan_session.py": "检验重复帧、相同物品不同位置、滚动重叠和原位确认",
    "test_value_ranking.py": "检验单格排序、锁定、装配子物品和缺价阻断",
    "describe_file_inventory.py": (
        "只读列举文件并按路径分类，向标准输出生成清单页供 apply_patch 写入"
    ),
    "assistant.sqlite3": "真实物品目录和版本化价格快照；SQLite 二进制数据库",
    "layout_1080p.json": "用户本地储物区域坐标、格距和行列配置",
    "orzice-token.dpapi": "Windows 当前用户 DPAPI 加密凭证；清单未读取其内容",
    "icon-index.json": "物品 ID 到本地 PNG 相对路径的对应关系，不是图像识别模型",
    "screenshot-review.md": "当前真实截图的候选与参考估价草稿，不能直接作为丢弃依据",
    "unins000.dat": "Inno Setup 的已安装文件及卸载记录",
    "unins000.exe": "Inno Setup 卸载程序；本次没有运行",
    "DeltaLootAssistant.exe": "已有 PyInstaller 打包主程序；不会自动包含最新源码修改",
}
LIBRARIES = {
    "numpy": "NumPy 数组与数值运算", "pandas": "Pandas 表格数据处理",
    "pyside6": "PySide6/Qt 桌面界面", "shiboken6": "Qt 的 Python/C++ 绑定支持",
    "cv2": "OpenCV 图像运算", "onnxruntime": "ONNX 本地模型推理",
    "ortools": "OR-Tools 约束优化", "rapidocr": "RapidOCR 中文文字识别",
    "requests": "Requests HTTP 客户端", "urllib3": "HTTP 连接与传输支持",
    "certifi": "HTTPS CA 根证书", "charset_normalizer": "HTTP 文本编码识别",
    "idna": "国际化域名编码", "pil": "Pillow 图像读写",
    "pytest": "pytest 自动测试", "_pytest": "pytest 测试引擎",
    "ruff": "Ruff Python 静态检查", "pyinstaller": "PyInstaller 打包工具",
    "pip": "Python 依赖安装管理", "setuptools": "Python 包构建支持",
    "google": "Google Python 支持包（含 Protocol Buffers）",
    "tzdata": "时区规则数据", "dateutil": "日期时间解析", "pytz": "时区处理",
    "mss": "桌面截图", "pyclipper": "OCR 多边形裁切",
    "shapely": "OCR 几何运算", "yaml": "YAML 配置读取",
    "omegaconf": "分层模型配置", "antlr4": "配置语法解析",
    "tqdm": "下载/处理进度显示", "colorlog": "日志颜色输出",
    "colorama": "Windows 终端颜色支持", "sympy": "符号数学",
    "packaging": "Python 包版本规则", "pluggy": "测试插件调度",
}


def list_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["rg", "--files", "--hidden", "--no-ignore", str(root)],
        capture_output=True, encoding="utf-8", check=True,
    )
    return sorted(
        (Path(line) for line in result.stdout.splitlines()
         if "/docs/file-inventory/" not in line.replace("\\", "/")),
        key=lambda p: p.as_posix().casefold(),
    )


def describe(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    low = relative.casefold()
    name = path.name
    suffix = path.suffix.lower()
    if "/.git/" in "/" + low:
        return "Git 元数据/默认钩子样例；记录仓库配置和版本状态，非业务逻辑"
    if "/.pytest_cache/" in "/" + low:
        return "pytest 缓存/说明；保存用例索引和上次执行状态，可由测试重新生成"
    if "/.ruff_cache/" in "/" + low:
        return "Ruff 检查缓存/说明；用于加速后续静态检查"
    if "provider-icons/" in low and suffix == ".png":
        return "授权目录 URL 对应的缓存物品图标；物品 ID 映射见 icon-index.json"
    if suffix == ".dpapi":
        return OWN["orzice-token.dpapi"]
    if "build/" in low and "site-packages/" not in low:
        return {
            ".toc": "PyInstaller 构建清单，记录归档依赖与产物路径",
            ".html": "PyInstaller 依赖交叉引用报告",
            ".txt": "PyInstaller 构建警告/诊断文本",
            ".pkg": "PyInstaller 中间归档，供 EXE 构建使用",
            ".pyz": "打包的 Python 字节码模块归档",
            ".zip": "打包构建的 Python 基础库归档",
        }.get(suffix, "PyInstaller 自动生成的构建中间文件；非手写业务代码")
    dependency = any(marker in low for marker in (".venv/", ".venv-test/", "_internal/"))
    if name in OWN and not dependency:
        return OWN[name]
    if "_internal/resources/" in low:
        return "随旧安装包分发的资源副本：" + OWN.get(name, "静态演示配置或资源")
    if suffix == ".pyc":
        return "对应 Python 模块的自动编译字节码缓存；无需手动编辑"
    if name == "pyvenv.cfg":
        return "虚拟环境配置，记录解释器来源和是否继承系统包"
    if "/scripts/" in low:
        return "虚拟环境启动/命令入口：" + name + "；属于工具链，不是游戏物品数据"
    if name.lower() in {
        "metadata", "record", "wheel", "installer", "entry_points.txt", "direct_url.json"
    }:
        return "Python 包安装元数据：版本、依赖、文件清单或安装来源"
    if "license" in name.lower() or "copying" in name.lower():
        return "所属第三方库的许可证/版权说明，发布时应保留"
    if suffix == ".onnx":
        if "det" in name.lower():
            return "本地 OCR 文字区域检测模型权重；找出文本框，不储存物价"
        if "rec" in name.lower():
            return "本地 OCR 文字识别模型权重；把像素转换为字符，不储存物价"
        if "cls" in name.lower():
            return "本地 OCR 文字方向分类模型权重"
        return "ONNX 神经网络模型权重；具体任务由调用配置决定"
    components = [part.casefold().split(".")[0] for part in path.parts]
    owner = next(
        (LIBRARIES[part] for part in components if part in LIBRARIES), "Python/第三方运行环境"
    )
    kind = {
        ".py": "模块实现", ".pyi": "类型接口说明", ".pyd": "Windows Python 原生扩展",
        ".dll": "动态链接运行库", ".exe": "工具可执行程序", ".lib": "链接库",
        ".h": "C/C++ 接口头文件", ".hpp": "C++ 接口头文件", ".c": "原生扩展源文件",
        ".cpp": "C++ 扩展源文件", ".pxd": "Cython 类型接口", ".pyx": "Cython 扩展源文件",
        ".json": "结构化配置/资源", ".yaml": "配置资源", ".yml": "配置资源",
        ".txt": "说明/字典/数据资源", ".md": "说明文档", ".rst": "说明文档",
        ".html": "帮助/报告页面", ".png": "图像资源", ".ico": "图标资源",
        ".svg": "矢量图像资源", ".qsb": "Qt 预编译着色器", ".qml": "Qt 声明式界面组件",
        ".js": "脚本组件", ".qm": "Qt 翻译资源", ".ttf": "字体资源",
        ".zip": "归档资源", ".gz": "压缩资源", ".npz": "数组资源/测试数据",
        ".npy": "NumPy 数组资源/测试数据", ".csv": "表格资源/测试数据",
        ".dat": "二进制/结构化资源", ".db": "随库分发的数据文件",
    }.get(suffix, "配套资源/配置文件（仅按路径分类，未验证内部内容）")
    if "delta_loot_assistant-setup" in name.lower():
        return "旧版本 Inno Setup 安装包；双击会启动安装，本次未执行"
    return f"{owner}：{name} 的{kind}；非本项目手写物品规则"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", choices=list(ROOTS))
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    if args.summary:
        summary = {}
        for key, root in ROOTS.items():
            paths = list_files(root)
            summary[key] = {
                "root": root.as_posix(), "count": len(paths),
                "pages": math.ceil(len(paths) / PAGE_SIZE),
                "folders": dict(Counter(p.relative_to(root).parts[0] for p in paths)),
            }
        print(json.dumps(summary, ensure_ascii=False))
        return
    root = ROOTS[args.root]
    paths = list_files(root)
    selected = paths[args.page * PAGE_SIZE:(args.page + 1) * PAGE_SIZE]
    text = [
        f"# 逐文件用途清单：{args.root} 第 {args.page + 1} 册", "",
        f"根目录：`{root}`。核对日期：2026-09-08。", "",
        "每行对应一个实际文件。第三方说明按所属库和文件类型归类，不是逐行源码审计。",
        "只列举路径，不读取凭证内容。文件清单自身不递归纳入清单。", "",
        "[返回索引](<C:/Users/ASUS/Desktop/delta force/"
        "delta-loot-assistant/docs/file-inventory/INDEX.md>)", "",
        "| 相对于上述根目录的文件路径 | 用途 |", "|---|---|",
    ]
    for path in selected:
        relative = path.relative_to(root).as_posix().replace("|", "\\|")
        role = describe(path, root).replace("|", "\\|")
        text.append(f"| `{relative}` | {role} |")
    print(json.dumps({
        "filename": f"{args.root}-{args.page + 1:03}.md",
        "content": "\n".join(text) + "\n", "entries": len(selected),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
