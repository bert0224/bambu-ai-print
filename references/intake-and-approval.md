# 机械设计需求采集与确认

本清单用于防止从名义尺寸或单张照片直接跳到错误夹持原理。按结构风险逐项提问，不要求用户一次回答全部内容。提问不只复述用户要求，还应暴露用户尚未意识到的装配、公差、受力、材料和验证风险。

## 照片判读

分别记录三类信息：

- `可见事实`：例如某个 USB 口超出 PCB 下边缘。
- `暂定判断`：例如接口可能需要从下方插拔。
- `待测量`：照片无法可靠确定的宽、高、突出深度和距板边位置。

正反面照片必须按同一朝向对齐。手指、线束或透视遮挡的区域视为未知，不把自制的简化假体当成真实包络。

## 结构性问题顺序

优先询问会改变夹具原理的问题：

1. 物体最终朝向，以及哪一边定义为上、下、前、后。
2. 安装和拆卸动作：从上方滑入、从侧面放入、张开夹爪后放入，还是需要旋转进入。
3. 所有超出名义外形的刚性突出物，以及它们在安装路径中的扫掠空间。
4. 允许接触的位置：板边、正面、背面或指定矩形区域；同时给出禁止接触区域。
5. 工作时必须暴露的接口、按键、天线、测试点、散热面和观察面。
6. 线束的自然方向、最小弯曲空间及是否会持续拉拽底座。
7. 夹紧力、拆装频率、是否允许螺丝或弹簧件，以及打印材料和喷嘴条件。
8. 使用环境和失效后果：温度、日晒、湿度、振动、带电状态，以及零件失效是否会损坏设备或伤人。
9. 真实制造条件：拓竹打印机型号、喷嘴直径/材质、耗材、构建板和是否可运行 Bambu Studio 验证。

对于 PCB 中部夹持，至少确认夹持区距某一基准边的起止高度、可覆盖板面的宽度，以及是否只能接触裸板边。

## 需求确认单模板

```text
用途：
对象与朝向：
名义尺寸：
突出物完整包络：
允许夹持区：
禁止接触区：
安装动作：
拆卸动作：
必须保持可用/可见的区域：
线束方向与外力：
底座和稳定性要求：
材料、打印机和喷嘴：
尺寸来源与置信度：
已确认公差：
待实测项：
项目特定风险与缓解措施：
首件/公差试片计划：
成功标准：
```

默认必须让用户明确回复确认或提出修改；用户已逐次明确跳过对应人工确认时，按后文记录，无需重复询问。未取得事实的字段不能用默认值悄悄补齐。需求确认单附带风险摘要；影响结构原理的风险仍为 `open` 时，不得请求用户笼统确认，也不能借跳过进入建模。

## 风险台账

按 [engineering-risk-gates.md](engineering-risk-gates.md) 建立 JSON 风险台账。每个任务都要覆盖通用类别；PCB 任务还要声明 `risk_profiles: ["pcb"]` 并覆盖 PCB 扩展类别。

可从 [templates/risk-register.template.json](../templates/risk-register.template.json) 复制骨架。模板故意保持未通过状态；只有填入本项目证据并关闭阻塞项后，构建门禁才会放行。

示例骨架：

```json
{
  "design_id": "项目唯一标识",
  "risk_profiles": ["pcb"],
  "manufacturing_fingerprint": {
    "status": "locked",
    "printer_model": "用户确认的具体型号",
    "nozzle_diameter_mm": 0.4,
    "nozzle_material": "用户确认的喷嘴材质",
    "filament": "品牌和完整材料名",
    "build_plate": "实际打印板",
    "slicer": "Bambu Studio",
    "slicer_version": "实际版本",
    "process_profile": "实际工艺配置名"
  },
  "coverage": {
    "evidence_measurement": {"status": "reviewed", "evidence": "关键尺寸由用户用卡尺实测"},
    "object_envelope": {"status": "reviewed", "evidence": "已记录正反面器件、接口和线束包络"},
    "assembly_motion": {"status": "reviewed", "evidence": "概念前已确定张开夹爪后侧向放入"},
    "interfaces_keepouts": {"status": "reviewed", "evidence": "用户确认允许夹持区和禁触区"},
    "loads_stability": {"status": "reviewed", "evidence": "已定义桌面放置与线缆侧拉工况"},
    "tolerance_fit": {"status": "reviewed", "evidence": "首件先打印夹爪公差试片"},
    "fdm_process": {"status": "reviewed", "evidence": "已锁定喷嘴、方向及支撑策略"},
    "material_environment": {"status": "reviewed", "evidence": "材料与工作温度已确认"},
    "cad_export": {"status": "reviewed", "evidence": "计划检查实体、单位、回读和网格"},
    "slicer_first_article": {"status": "reviewed", "evidence": "计划真实切片并先做局部试装"},
    "pcb_contact": {"status": "reviewed", "evidence": "夹爪只接触用户确认的裸板边"},
    "electrical_rf_thermal": {"status": "reviewed", "evidence": "未知金色区域按禁触区处理"}
  },
  "risks": [],
  "open_questions": []
}
```

不要照抄示例内容。`evidence` 必须指向本项目的用户实测、照片事实、图纸、确认原文或具体验证计划。

## 概念效果图门禁

概念图的目标是确认夹持原理，不是展示已经完成的 CAD。图中应同时表现：

- 被夹持物的正反方向和完整突出物；
- 夹具主体、活动件及桌面；
- 夹持区和禁触区；
- 安装与拆卸箭头；
- 关键空间尺寸；
- 可能有歧义的局部放大图。

用户确认概念图后，保存确认记录：

```json
{
  "design_id": "项目唯一标识",
  "requirements_approved": true,
  "risk_review_approved": true,
  "concept_approved": true,
  "requirements_confirmation": "用户确认需求和风险摘要的原文",
  "concept_confirmation": "用户确认概念图的原文",
  "requirements_document": "需求确认单路径",
  "requirements_sha256": "需求确认单文件的 SHA-256",
  "risk_register": "风险台账 JSON 路径",
  "risk_register_sha256": "风险台账文件的 SHA-256",
  "concept_image": "概念图路径",
  "concept_image_sha256": "概念图文件的 SHA-256"
}
```

模型源码中的 `DESIGN_ID` 必须与确认记录和风险台账一致。需求、风险台账、对象版本或概念工件发生变化时，重新计算相应哈希并重新获得受影响的用户确认或适用于新对象的明确跳过记录。

## 逐次跳过人工确认

默认 CLI 不启用跳过，原审批 JSON 保持兼容。只有用户针对当前设计明确要求跳过具体人工确认时，追加一个或多个 `--skip-gate requirements`、`--skip-gate risk_review`、`--skip-gate concept`。不支持 `all`，也不支持跳过风险事实、几何、专业验证或设备操作授权。

跳过分为两个时点：

1. 解除人工等待：先记录明确的门禁、设计对象、范围、用户原文和理由。未生成的概念图、核心结构说明或源码不需要提前提供，更不能索取它们的哈希。只跳过需求或风险人工确认时，继续准备后续概念工件；未跳过的后续人工确认仍正常执行。三项人工等待均已处理且工程事实与风险审查通过后，可按明确需求编写可审阅源码。
2. 绑定后构建：工件实际首次生成后，构建前计算其真实哈希，补齐以下 `gate_waivers` 记录。对象、门禁及授权范围未变时，首次补齐工件与哈希属于落实已有授权，不重复提问。绑定未完成时不得运行构建脚本或执行模型源码，不能把草稿记录当作构建门禁通过。

构建时审批 JSON 的 `gate_waivers` 键必须与本次 CLI 指定门禁完全一致。每个键对应以下完整记录；用户原文由代理忠实抄录，脚本仅校验记录结构与文件绑定，不能证明话语来源或用户身份。不能把无关的原文、含糊的“继续”或本次功能实现授权当成具体模型的跳过授权。

```json
{
  "concept_approved": false,
  "gate_waivers": {
    "concept": {
      "status": "skipped",
      "design_id": "与当前设计一致的标识",
      "user_request": "用户明确跳过本次概念人工确认的原文",
      "reason": "用户给出的理由；未提供时如实写用户未说明理由",
      "scope": "明确的设计版本、对象和适用动作范围",
      "artifacts_sha256": {
        "source": "当前模型源码文件的 SHA-256",
        "requirements": "当前需求文件的 SHA-256",
        "risk_register": "当前风险台账文件的 SHA-256",
        "concept": "当前概念图或核心结构说明文件的 SHA-256"
      }
    }
  }
}
```

此片段合并到原审批 JSON，不能代替需求文件、风险台账与完整的设计标识。对应 `*_approved` 字段应为 `false` 或省略；不得同时写 `true`。报告中的 `gate_statuses` 会如实给出 `skipped`，原确认字段不会被改写成批准。未跳过的项仍要求原批准字段及原文；需求和风险审查沿用同一 `requirements_confirmation`，因此只跳过其中一项时仍须保留另一项的真实确认原文。

`concept` 已明确跳过且不需要概念图时，可省略 `concept_image` 和 `concept_image_sha256`，改为提供 `concept_definition` 路径与 `concept_definition_sha256`。核心结构说明必须是包含以下非空字段的 JSON 文件：

```json
{
  "design_id": "与当前设计一致的标识",
  "structural_principle": "结构原理与制造零件关系",
  "interfaces_keepouts": "允许接触位置、禁触区和接口包络",
  "assembly_motion": "完整装入、锁止、解锁与取出路径",
  "critical_dimensions": "关键尺寸、来源及尚待验证的假设"
}
```

这些字段必须来自本任务事实，并经风险台账交叉检查；跳过不允许删除关键结构信息。若提供任何概念图字段，则图像与哈希仍必须完整且一致，不能靠缺图转入替代分支。没有概念图确认时，不报告“概念已确认”。

每次调用均需显式选择，不能靠磁盘上的旧跳过记录自动启用。构建记录只适用于绑定的当前设计和文件；完全相同且仍有效的授权可复用，不重复索取同一确认。上述首次生成与补齐哈希不算后续输入变更。已经绑定的模型源码或工件随后变化时，必须重新核实授权适用范围并记录依据；对象、设计方案、风险或动作范围发生实质变化时，取得适用于新对象的确认或明确跳过授权，不能仅更新旧哈希掩盖变更。此入口不调用切片器或打印机，不授予外部动作权限。
