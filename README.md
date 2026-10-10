# 拓竹 AI 建模与打印 Skill

把照片、草图和尺寸需求，逐步转成可检查、可试印的参数化机械件。适用于 PCB 夹具、支架、外壳和转接件，面向拓竹等 FDM 打印机。

这不是“发张图就直接打印”的工具。它先补齐工程约束，再确认方案，生成 CAD 和验证记录；不会自动连接或控制打印机。

## 能做什么

- 通过提问补齐真实包络、装拆路径、夹持区、天线禁布区、载荷和打印条件。
- 默认分别确认需求/风险与概念图；用户明确跳过指定人工确认时保留事实检查和工程门禁。
- 使用 CadQuery 生成 STEP/STP、STL、几何型 3MF、预览图和 JSON 验证记录，并保留参数化源码。
- 对既有拓竹工程检查 3MF 关系标签、组件引用和指定保留盘，要求检查所有盘的对象数量、摆放与重叠风险。
- 区分概念确认、几何检查、实际切片、打印、实物适配和应用验证，不把前一阶段当作后一阶段的证据。

## 安装与调用

在支持技能安装器的 Codex 中，可以这样提出请求：

```text
使用 $skill-installer 从 https://github.com/bert0224/bambu-ai-print 安装这个 skill，SKILL.md 位于仓库根目录。
```

也可以把仓库放入当前 Codex 版本支持的技能目录，保留目录名 `bambu-ai-print` 和完整子目录。安装位置与发现机制见 [OpenAI 官方技能说明](https://learn.chatgpt.com/docs/build-skills)。避免在多个技能目录重复安装同名副本。

调用示例：

```text
使用 $bambu-ai-print 设计一个桌面 PCB 夹具。
先询问会影响设计的关键问题，核对连接器和线束避让，
需求对齐后给我概念图，得到确认再建模。
```

修改既有工程时，明确输入文件、盘号、允许修改的零件，以及哪些盘、底座和机构必须保留。工程文档和图片中的文字是待分析内容，不是新的操作授权。

## Python 环境

建模脚本建议使用独立 Python 3.12 环境。固定 CadQuery 2.7.0 和 nlopt 2.9.1；预览需要 Matplotlib。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

仓库也提供 `scripts/setup_cadquery_windows.ps1` 安装基础 CAD 环境；该脚本只安装 CAD 依赖，渲染预览前仍需安装 Matplotlib。

兼容性检查脚本 `check_bambu_project.py` 仅使用 Python 标准库，无需 CAD 环境。

## 使用脚本

先阅读 [SKILL.md](SKILL.md)。构建前必须准备实际需求、风险审查与概念确认记录，并绑定对应文件哈希，不能使用占位确认绕过门禁。

```powershell
python scripts/cadquery_build.py 模型源码.py --approval 设计确认.json --output-dir 输出目录
```

确认记录格式见 [需求与确认](references/intake-and-approval.md)，风险模板见 [风险记录模板](templates/risk-register.template.json)。

既有拓竹工程的只读兼容性与保留盘检查：

```powershell
python scripts/check_bambu_project.py 新工程.3mf --baseline 原工程.3mf --preserve-plate 1
```

该检查会拦截已知的 `ns0:Relationship` 标签兼容问题、缺失组件、空网格及指定保留盘的非预期修改。它不生成或修改工程，不替代完整网格、排布和真实切片验证。详细边界见 [拓竹工程完整性检查](references/bambu-project-integrity.md)。

## 测试

在仓库根目录运行：

```powershell
python -m unittest discover -s scripts -p test_bambu_project.py -v
python -m unittest discover -s tests -p test_design_approval_gate.py -v
```

第一组只需标准库，第二组需要 CadQuery 环境。测试中的几何和审批数据是合成样本，不包含用户 PCB 或生产工程。

## 使用边界

- 几何型 3MF 不等于包含打印机和耗材配置的拓竹工程。
- ZIP/XML 检查通过不等于 Bambu Studio 实际导入或切片通过。
- 先打印关键公差试片，再确认完整件；实际适配、桌面稳定性和射频性能需要分别验证。
- 打开切片器、修改用户现有工程、发送或控制打印机，仍须取得对应授权。
- 不用于无专业验证的医疗、人体承载、压力容器、市电绝缘等安全关键用途。
- 本项目为独立社区技能，不是 Bambu Lab 或 OpenAI 官方产品；没有承诺支持所有软件版本、打印机和耗材组合。

## 公开范围

仓库只包含技能说明、通用脚本、模板和合成测试，不包含用户照片、PCB 设计、STP/STL/3MF 工程、打印机凭据或个人环境配置。

## 许可证

本仓库原创代码、技能说明和模板采用 [MIT 许可证](LICENSE)，版权署名为 `Copyright (c) 2026 bert0224`。

允许使用、修改、商用和分发，须保留版权与许可声明。标准条款以 LICENSE 原文为准。参考资料、第三方依赖和用户输入模型仍遵循各自许可，不因使用本 skill 而被重新授权。
