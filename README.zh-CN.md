# 导表管理器 (Table Exporter)

[English](README.md) · **中文**

一个把 Excel 工作簿（`.xlsx`）转成 Lua 配置表的跨平台桌面小工具。

指定一个存放工作簿的目录，它会把生成的 `.lua` 分别写到**客户端**目录和/或**服务端**
目录，两侧可以用各自的文本编码。字段可以只给客户端、只给服务端，或者两边都给。

Python + Tkinter（ttkbootstrap）实现，支持 Windows 与 macOS。

---

## 目录

- [功能](#功能)
- [环境要求](#环境要求)
- [从源码运行](#从源码运行)
- [使用说明](#使用说明)
- [Excel 表格式规范](#excel-表格式规范)
- [示例](#示例)
- [打包成独立可执行文件](#打包成独立可执行文件)
- [项目结构](#项目结构)
- [备注](#备注)
- [开源协议](#开源协议)

## 功能

- **一个工作簿 → 多张表**：每个 sheet 独立解析，各自生成一个 `.lua` 文件。
- **前后端分离导出**：字段级的 `scope` 决定这一列去客户端还是服务端，两侧各用各的
  编码（UTF-8、UTF-8-BOM、GBK 等）。
- **两种表形态**：按行记录的 `base` 表，和整份文件只有一条记录的 `tiny` 表；再用
  `key_count` 控制是否输出多级嵌套。
- **先校验再写文件**：`table` / `any` 列会被当作 Lua 表达式做语法检查，`number` 列
  会检查是否真能转成数字。**只要有一处不对，导出直接中断、一个文件都不写**，
  绝不会留下"导了一半"的目录。
- **多项目管理**：可保存多套「源目录 + 输出目录」组合并随时切换，全部记在
  `config/projects.json`。
- **SVN 辅助**：「更新表格」「提交表格」按钮（Windows 走 TortoiseSVN 窗口，
  macOS 走终端里的 `svn`）。
- **明暗主题 + 中英文界面**：都在菜单栏切换，选择会被记住。

## 环境要求

- Python 3.9+，且带 **Tkinter**（python.org 官方安装包与 conda 自带；部分 Linux
  发行版需要单独装）
- [`openpyxl`](https://openpyxl.readthedocs.io/) ≥ 3.0
- [`ttkbootstrap`](https://ttkbootstrap.readthedocs.io/) ≥ 2.2, < 3
  （Tk 9 / Python 3.14 必须 2.0.1+：1.x 在 Tk 9 上控件会放大、滚动失效）

## 从源码运行

```bash
git clone https://github.com/d29107d/table_export.git
cd table_export
python -m pip install -r requirements.txt
python main.py
```

程序默认最大化打开。窗口标题、菜单栏和所有文案都跟随所选语言，**默认英文**。

## 使用说明

1. **表格目录**：存放 `.xlsx` 的文件夹。该目录下的每个 `.xlsx` 都会被扫描
   （不递归，跳过 `~$` 开头的临时文件），左侧列表里逐条列出。
2. **客户端输出** / **服务端输出**：生成 Lua 的落地目录。可以只填一个；两侧各自
   有编码选择框。
3. **导出选中** / **导出全部**：先分析，再写文件。

导出时进度条分两段：前半段是只读预检，后半段才真正写文件。预检发现问题就地中断
（见[数据校验](#数据校验)）。

快捷键：`Ctrl+E` 导出选中 · `Ctrl+Shift+E` 导出全部 · `Ctrl+A` 全选 ·
`Ctrl+Shift+A` 反选 · `F5` 刷新 · `Ctrl+F` 定位到搜索框 · `Ctrl+Q` 退出。

表格列表支持按**名称**或**修改时间**排序，并可用搜索框过滤。

**语言与主题**都在菜单栏：`文件 / 导出 / 主题 / 语言 / 帮助`。在「语言」里选
`English` 或 `中文`，整个界面立刻切换，选择写入 `config/projects.json`。

### 配置存放位置

| 平台 | 位置 |
|---|---|
| Windows | `table_exporter.exe` 同级的 `config/`（源码运行时是项目根目录下的 `config/`） |
| macOS | `~/Library/Application Support/table_exporter/` |

`projects.json` 存工程列表、当前工程、表格列表排序方式和界面语言；`theme.json`
存主题。删掉它们即可恢复初始状态。

## Excel 表格式规范

**一个 sheet 就是一张表。** 只有同时满足下面两条的 sheet 才会被当成配置表：

- `B1` 恰好是 `base` 或 `tiny`；
- `B2` 是以 `.lua` 结尾的文件名。

其它 sheet（封面页、空白页、说明页）会被静默忽略，所以文档可以和配置放在同一个
工作簿里。

### 第 1~3 行：表级元信息

| 单元格 | 含义 | 例子 |
|---|---|---|
| `B1` | 表类型：`base` 或 `tiny` | `base` |
| `B2` | 输出文件名（必须以 `.lua` 结尾） | `cfg_item.lua` |
| `B3` | `key_count`：前几个字段作为嵌套 key（仅 `base` 用） | `1` |
| `E1` | 文件头：写在表内容之前 | `return {` |
| `E2` | 文件尾：写在文件末尾 | `}` |

`A1`、`A2`、`A3`、`D1`、`D2` 只是给人看的标签，程序读的是 `B`、`E` 两列。

文件头/尾是自由文本：写成 `local cfg = {` + `}` 和 `return cfg`，产物就会被包在
一个局部变量里。

### `base` 表 —— 第 5~8 行描述列，第 9 行起是数据

| 行 | 含义 |
|---|---|
| 5 | 该列的注释（给人看） |
| 6 | `scope`：`c` / `s` / `sc` / `cs`（空 = `c`） |
| 7 | 类型：`number` / `string` / `table` / `any`（空 = `string`） |
| 8 | 字段名（必须是合法的 Lua 标识符） |
| 9 起 | 一行一条数据 |

以 `key_count = 1` 为例，产物长这样：

```lua
return {		--[[ cfg_item.xlsx -> Item ]]
[1] = {
	id = 1,
	name = [[长剑]],
	weight = 100,
},
[2] = {
	id = 2,
	name = [[铁盾]],
	weight = 80,
},
}
```

### `tiny` 表 —— 第 6 行起一行一个字段

| 列 | 含义 |
|---|---|
| `A` | 注释（给人看） |
| `B` | `scope` |
| `C` | 类型 |
| `D` | 字段名 |
| `E` | 值 |

第 5 行是表头。**空行会被跳过**，所以中间可以留空。产物是一张扁平的表：

```lua
return {		--[[ cfg_setting.xlsx -> Settings ]]
	free_revive_count = 3,
	entrance_npc = {10000, "复活使者", 1503},
	notice = [[欢迎光临]],
}
```

### 字段类型

| 类型 | 产物写法 | 例子 | 说明 |
|---|---|---|---|
| `number` | Lua 数字 | `12` | 转不成数字的值会被**静默写成 nil**（数据丢失），所以这里会**直接报错**而不是放过。 |
| `string` | 长字符串 `[[...]]` | `[[长剑]]` | 内容不用转义。若内容含 `]`，会自动提升括号层级（`[[` → `[=[` → `[==[` …），保证产物仍是合法 Lua。 |
| `table` | 原样输出 | `{{1001, 2}, {1002, 1}}` | Lua 表格由你自己写，内容会做**语法校验**。 |
| `any` | 原样输出 | `nil` / `true` / `100+50` / `{quality=3}` | 同 `table`，另外裸数字与布尔值也直接透传。 |

### scope

| 取值 | 含义 |
|---|---|
| `c` | 只导客户端（单元格为空时的默认值） |
| `s` | 只导服务端 |
| `sc` / `cs` | 两端都要（两种写法等价） |

被 scope 排除的字段在该侧文件里**根本不出现**。如果一张表在某一侧过滤后已经没有
任何字段，那一侧就不会产生文件。

### `key_count`

| `key_count` | 输出形态 |
|---|---|
| `0` | 列表 —— 每行是一个匿名 `{ ... },` 元素 |
| `1` | `[k1] = { ... },` |
| `2` | `[k1] = { [k2] = { ... } },` |
| `n` | 嵌套 n 层 |

key 取的是**前 `key_count` 个字段名合法的列**，**不受 scope 影响** —— 标了 `s` 的
key 列，在客户端文件里照样当 key 用。同一条 key 出现两次时，后出现的行覆盖先出现
的（这就是 Lua 对重复表键的语义）。

### 容易踩的规则

- **字段名不合法的列会被丢弃**：表右侧的草稿、重复的 `id`、孤立的 `{` 或 `★` 都
  不会导出。所以字段名必须长得像 `some_field`。
- **最后一列只看第 5 行**：第 5 行最靠右的非空单元格决定扫描到哪一列（只有第 5 行
  整行都空时，才退回看 7/6/8 行）。6~8 行不会把范围往右扩，所以表右边零散写着的
  字段名不会被带上。
- **E 列是断点**：如果 A~D 四列的 5~8 行都完整，而 E 列的 5~8 行全空，那么 E 及其
  右侧一律不导出 —— 这是留给作者写备注的位置。
- **数据区为空 = 整表不导出**：第 9 行（有效列上）为空时，整张表直接跳过、不生成
  文件，哪怕第 10 行以后有数据也一样。**第一条记录请写在第 9 行。**
- **空格 ≠ 空着**：单元格空着，该字段在**这条记录里整个消失**；单元格里只有空白
  字符（含空串）则算一个值，按类型写成 `number`/`any` → `nil`、`table` → `{}`、
  `string` → `[[ ]]`。
- 产物换行符统一是 **CRLF**。

### 数据校验

写文件之前，所有 `table` / `any` 单元格会被当作 Lua 表达式解析一遍（自写的递归下降
解析器，优先级对齐 Lua 5.1），所有 `number` 单元格则用**和导出完全同一套**判定来
检查 —— 所以不会出现"校验说没问题、导出却是 nil"的自相矛盾。

中文工作簿最常见的手误是全角标点，这类错误会给出明确提示（`'，'` 是全角逗号，
应该用半角 `','`）。

只要有一格不通过：

- **导出中断** —— 一个文件都不写，已存在的旧文件也不会被动；
- 弹窗按 `文件 / 表 / 单元格地址 / 字段 -> 原因` 列出前 10 条；
- 日志里有完整清单，并且带上该单元格的内容预览。

**刻意不提供"强制导出"**。

## 示例

`example/` 是一套可直接运行的样例，中英文各一份。两份的表结构和字段名完全相同，
只有"给人看的文字"不同（表内注释、单元格里的示例数据、`tiny` 表头）。

```
example/
├── en/
│   ├── import/     源工作簿
│   ├── client/     导给客户端的结果
│   └── server/     导给服务端的结果
└── zh-CN/          同样一套，注释与示例数据是中文
```

| 工作簿 | 演示内容 |
|---|---|
| `01_types_and_scopes.xlsx` | 四种字段类型 × 四种 scope；一个工作簿里两张表 |
| `02_keys_and_layout.xlsx` | `key_count` 取 0 / 1 / 2；以及会被跳过的说明页和空表 |
| `03_tiny_config.xlsx` | `tiny` 表布局，中间特意留了一个空行 |
| `04_edge_cases.xlsx` | 多行文本、含 `]]` 的文本、以 `]` 结尾的文本、"空着"与"只有空格"的对比、布尔值、科学计数、自定义文件头尾 |

把工作簿和它旁边的产物对照着看即可，例如
`en/import/01_types_and_scopes.xlsx` → `en/client/cfg_example_item.lua`。同一张表因为
`scope` 不同，在两侧生成的文件内容并不一样。

想重新生成示例：

```bash
python tools/make_examples.py            # 中英两套都生成
python tools/make_examples.py en         # 只生成英文
python tools/make_examples.py zh-CN      # 只生成中文
```

这个生成脚本本身就是一份可执行的格式说明。

## 打包成独立可执行文件

```bash
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean table_exporter.spec
```

产物在 `dist/table_exporter.exe`（Windows）或 `dist/table_exporter`（macOS）。

几点说明：

- 请用**带 Tkinter 的 Python**。Windows 上官方安装包和 conda 都自带；从绿色版
  Python 建的裸 `venv` 可能没有。
- `table_exporter.spec` 通过 `sys.base_prefix` 定位 Python 安装位置，不绑定某台机器。
- spec 同级若存在 `config/`，会被作为首次启动的兜底模板打进包，这一步是可选的。

## 项目结构

```
main.py                     入口
core/
  excel_reader.py           工作簿 -> 表字典，以及单元格校验
  lua_writer.py             表字典 -> Lua 源码（字段格式化、嵌套）
  exporter.py               写 .lua 文件（编码、CRLF）
  lua_syntax.py             Lua 字面量校验 + 数字校验 + 报错渲染
  i18n.py                   中英文文案表（不依赖 Tk）
gui/
  main_window.py            Tkinter 界面
  platform_compat.py        Windows / macOS 差异（字体、配置目录、svn）
tools/
  make_examples.py          重新生成 example/
  compare_export.py         两个导出目录的结构化对拍
  luaparse.py               compare_export.py 用的极简 Lua 解析器
example/                    示例工作簿与它们的产物（en / zh-CN）
table_exporter.spec         PyInstaller 打包描述
```

`tools/` 是开发期工具，不会被打进 exe。

## 备注

- 产物是纯文本，重复导出会直接覆盖。
- `tools/compare_export.py` 按**结构**而不是字节比较两个输出目录，当年就是靠它
  和旧版导表工具做对齐验证的：
  ```bash
  python tools/compare_export.py <源目录> <客户端目录> <服务端目录>
  ```
  不带参数时读 `tools/compare_paths.json`（不入库），再退回环境变量
  `COMPARE_SRC` / `COMPARE_CLI` / `COMPARE_SRV`。

## 开源协议

[MIT](LICENSE)
