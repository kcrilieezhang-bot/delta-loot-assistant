# 三角洲最高收益理包助手

新增本机网页版：双击外层 `启动网页版.cmd`，浏览器打开 `http://127.0.0.1:18765`。支持全图候选、人工框选、搜索纠错、同图纠错记录及确认后的总价/每格参考排序。不自动更新价格，整枪隐藏配件仍需核实总价。详见 [网页说明](docs/WEB-REVIEW.md)。

Windows 端、本地运行的《三角洲行动》截图估价助手原型。当前交付范围是：导入截图／F8 截图、识别候选、核对名称和数量、查询本地价格快照、按价值排序。真实截图还不能保证每件识别正确；CP-SAT 仅作为演示求解功能，真实截图的 F9 只完成估价确认，不宣称全局最优。

日常使用外层 `delta force/启动助手.cmd`，运行重新打包的 `app/DeltaLootAssistant`。开发仍用本目录 `start.cmd`。旧桌面安装目录和旧 `dist` 已归档到外层 `_archive/2026-09-08-cleanup`，真实 AppData 数据未移动。详见外层 `使用说明.md`。

## 已实现

- 全局 `F8` 截图/追加扫描，`F9` 完成扫描并求解，`Esc` 隐藏置顶侧边窗。
- 只接受 1920×1080 画面，支持首次区域校准预览、滚动区域补拍提示和多截图合并。
- RapidOCR 3.9.2 本地中文 OCR、名称模糊匹配、OpenCV ORB 图标特征以及固定储物格校准。模板由本地目录记录的图标地址构建，识别时只读取缓存。
- 当前原型中，全部截图候选需核对；匹配分不是识别准确率。待核对列表展示名称候选和裁剪，确认时更新原物品，不重复计价。
- 人工添加、改名、修改数量/耐久/单价、删除、锁定物品，并可通过“装配到”列维护枪械配件关系。
- 本地 JSON/CSV 价格包导入、SQLite 版本保留、回滚、赛季及 24 小时过期提醒。
- 用户授权的三角洲数据帝手动日更快照；Token 由 Windows DPAPI 加密保存，
  基础物品和价格写入 SQLite，24 小时内禁止重复请求且不会后台轮询。
- 弹药按实际发数估价；整枪和已装配配件合成估价且避免重复计算。
- 二维多容器 CP-SAT 求解，支持方向、隔断、安全箱限制、弹药合并、弹匣装弹、武器栏、配件槽位、兼容性、锁定物品及背包/胸挂替换。
- 固定目标优先级：总价值、安全箱保护价值、操作次数、重量。
- 明确区分 `OPTIMAL` 和 `FEASIBLE`，并显示当前价值、目标价值、净增益、布局与编号操作步骤。
- 展示候选单价、参考总价、目录占格和单格参考价。未定价项目单独标记；扫描未完成、未核对、缺价格或整枪配件不全时，不给出优先丢弃建议。
- 可选贡献识别样本；只在用户确认/锁定物品后保存内存中的物品裁剪和标签，不保存完整截图。
- PyInstaller 单目录程序和 Inno Setup 安装包脚本。

## 安全边界

本程序不注入游戏、不读取进程内存、不抓包、不覆盖游戏画面、不发送鼠标键盘操作，也不实现反检测功能。它不会访问 KK 一图流；只有用户主动点击“手动日更”时，才使用用户合法取得的三角洲数据帝 Token 生成一次本地快照。

公开发布前需要另行确认游戏名称、图标、素材和数据授权，并持续核对游戏用户协议。

## 当前数据限制

仓库不分发真实价格、Token、游戏图标或截图。OCR 模型由 RapidOCR 依赖包提供，扫描前检查模型文件齐全；缺失时提示重装，不在识别时下载。其许可见 [RapidOCR 项目](https://github.com/RapidAI/RapidOCR)。

本机已有本地目录、价格快照及图标缓存，无须再调用付费接口或手动制作整套模板。当前校准只适配提供的 1080p 截图布局，识别实际背包格、安全箱格及右侧物资；不包括左侧装备图标、上方未完整显示的胸挂/口袋。不同布局需重新校准。

当前已知限制：小字弹药子型号可能混淆；枪械皮肤、配件、医疗剩余用量和耐久未自动完成；目录尺寸不保证等于改装后占格；未知、遗漏和隐藏项目需人工补全。默认数量若未读到为 1，必须核对。图标相同的价格变体不能靠外观决定。单张样本不是准确率测试集，尚未达到 2 秒性能目标。识别目前同步执行，窗口在分析期间会短暂无响应。

第一版的价值排序仅在物品全部确认后给出可靠结论。默认以“单格价值”判断优先替换项，
因为它能表示有限背包空间的机会成本；界面同时保留物品总价值，用户可点击表头切换排序。

## 开发运行

要求 Windows 11、Python 3.12 x64。

```powershell
cd "C:\Users\ASUS\Desktop\delta force\delta-loot-assistant"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,build]"
python -m delta_loot_assistant
```

## 使用

1. 将游戏设置为简体中文、1920×1080、100% UI 缩放及无边框/窗口模式。
2. 打开理包或战利品界面，按 `F8`。
3. 按侧边窗提示滚动物资区、切换装备区或打开枪械详情，再按 `F8` 补拍。
4. 确认未知物品，修正数量、耐久、价格和枪械装配关系，锁定不能丢弃的物品。
5. 按 `F9` 完成估价。真实截图不会进入全局优化；整枪若要参与替换比较，请在单价列填写整枪含配件、装填弹药的合计价，或先锁定。配置演示数据才能测试 CP-SAT 功能。

可离线生成可核对报告（不保存完整截图、不访问网络）：

```powershell
.\.venv\Scripts\python.exe -m delta_loot_assistant.screenshot_report "截图绝对路径.png" --output "$env:LOCALAPPDATA\DeltaLootAssistant\reports\review.md"
```

## 价格包

JSON 示例见 `resources/sample_price_pack.json`。CSV 至少包含：

```csv
item_id,unit_price,durability_factor
demo_ammo,1200,1
```

JSON 包含 `schema_version`、`region`、`season`、`generated_at`、`source` 和 `prices`。`durability_factor` 可省略。

## 手动日更数据

在“价格与设置”页输入自己取得的三角洲数据帝 Token，点击“安全保存 Token”，
再点击“手动日更一次”。一次完整日更包含一次基础物品请求和一次全物品价格请求；
成功后写入 `%LOCALAPPDATA%\DeltaLootAssistant\assistant.sqlite3`。程序不会定时请求，
并会拒绝 24 小时内的重复同步。

## 测试与构建

```powershell
python -m ruff check src tests
python -m pytest
.\build.ps1
```

`build.ps1 -SkipInstaller` 强制 Python 3.12，先测试，再生成外层 `app\DeltaLootAssistant`；默认仅使用本地依赖，需要安装依赖时才显式加 `-InstallDependencies`。构建缓存位于外层 `_archive\build-cache`。不加 `-SkipInstaller` 且装有 Inno Setup 6 时，会继续生成外层 `app\installer` 安装包。构建前请退出便携版。

应用数据位于 `%LOCALAPPDATA%\DeltaLootAssistant`，包括 SQLite 价格数据库、用户模板和明确选择贡献的裁剪样本。
