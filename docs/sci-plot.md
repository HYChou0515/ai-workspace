# sci-plot — 科學繪圖工具 (#285)

`sci-plot` 是一套可擴充的領域圖表目錄,由 agent 從表格資料繪製而成。它是一個
sandbox(沙箱)的 **tool package**(`sample-tools/sci-plot/`),對外只暴露單一的
`chart` 指令,再加上一個後端的 **VLM 自我審查迴圈**,會在圖表回傳前自動修正版面問題。

它在 **RCA** 與 **Playground** 兩個 App 中啟用(列在它們的 `allowed_tools` 裡)。

## 使用方式(agent 的視角)

agent 只呼叫一個 tool,`chart`,挑選圖表類型並提供資料:

```json
{
  "chart": "box_scatter",
  "data": {"tool": ["E1","E1","E2"], "defects": [2, 3, 9]},
  "group": "tool",
  "y": "defects"
}
```

- **`data`** — 一個 workspace 檔案路徑(`.csv/.tsv/.json/.xlsx/.parquet`)**或**
  inline JSON:一份 row records 清單 `[{"col": v}, …]`,或是一個 column dict
  `{"col": [v, …]}`。
- **欄位角色(column roles)**(`group`、`y`、`die_x`、…)是**可選的**:有給 → 直接採用;
  省略且無歧義 → 自動推斷;省略且有歧義 → 結果會是一個 `needs_input` 物件,列出候選欄位,
  agent 再帶著明確的名稱重新呼叫。型別會寬鬆地強制轉換(像數字的字串 → 數字,
  像日期的 → datetime)。
- 輸出是 `{"images": ["charts/<chart>_<timestamp>.png"]}`。聊天介面會把 PNG 內嵌呈現
  (任何回報 `images`/`plots` 路徑的 tool 都會被渲染 — 見
  `web/src/renderers/toolImages.ts`)。

### v1 目錄

| chart | 呈現什麼 | 主要角色 | 值得注意的選項 |
|-------|---------------|-----------|-----------------|
| `box_scatter` | box 加上各 group 的散點,每個 group 一種顏色 | `group`、`y` | `max_points`(超過時,該 group 只顯示 outliers — 預設 1000) |
| `grouped_line` | 一條數值序列疊在多層級的階層式 x 軸上 | `levels`(由細到粗)、`value` | `line_level`(哪一層級會拆成不同顏色的線) |
| `wafermap` | wafer 圓圈裡的 die 網格,依數值上色 | `die_x`、`die_y`、`value` | `color_mode` `uni`(≥0,缺陷計數)/ `bi`(發散,量測值)、`wafer_diameter`、`notch` |
| `defectmap` | 每個缺陷在其座標上以一個小方塊呈現 | `x`、`y` | `die_pitch`(淡淡的參考網格)、`color`、`marker_size` |

`grouped_line` 的階層式刻度標籤會把橫跨數個位置的同一個值收合成一個 `|value|` 括弧
(一個 group 只顯示一次),而把唯一的值原樣顯示。

## 新增一個圖表(開發者的視角)

一個圖表就是一個 `IChart` subclass。在 `src/sci_plot/charts/` 底下新增一個 module、
註冊它,`chart` 指令的 JSON schema(一個以 `chart` 為判別子的 discriminated union)
就會自動長出來:

```python
from sci_plot.framework.chart import IChart
from sci_plot.framework.registry import register
from sci_plot.framework.roles import Role, RoleKind
from sci_plot.framework.style import plt
from pydantic import BaseModel

class MyOptions(BaseModel):
    ...

class MyChart(IChart):
    name = "my_chart"
    description = "one line the LLM reads to pick + fill this chart"
    roles = (Role("x", RoleKind.NUMBER), Role("y", RoleKind.NUMBER))
    Options = MyOptions

    def draw(self, df, roles, options):
        fig, ax = plt.subplots()      # 不帶參數 → 繼承統一的 house style
        ax.plot(df[roles["x"]], df[roles["y"]])
        return fig

register(MyChart())
```

接著在 `charts/__init__.py` 加上 `from sci_plot.charts import my_chart`,
再重跑 prebuild(`uv run python scripts/prebuild_tools.py`)。

**framework 為每個圖表統一處理掉無聊的 80%**:讀檔案或 inline 資料 → 把每個角色欄位
強制轉成它宣告的 kind → 解析出哪個欄位填入哪個角色(明確指定 / 推斷 / 詢問)→
套用 house style → `savefig`。你的 `draw` 只需宣告 `roles` 加 `Options`,並畫出內容
(它保有完整的自由度 — 建立 die 網格、計算累積 %、收合標籤、抑制散點 —
也可以覆寫框架的部分,例如 equal aspect)。

## VLM 自我審查迴圈 (#285)

當有接上 vision model(`describer`)時,任何會輸出圖片的圖表都會在回傳前被自動審查:

1. **render** — 在 sandbox 裡繪製;
2. **detect** — VLM 回答一份固定的 yes/no 檢查清單(空白、標籤重疊、
   截斷、文字過小、裁切、缺少 legend/colorbar)再加上一段自由備註
   (`agent/plot_review.py: detect_issues`);
3. **adjust** — 一條*確定性*的規則把偵測到的問題對應到呈現旋鈕
   (figsize / dpi / 刻度旋轉 / 字級 / padding),並以 VLM 備註作為軟性提示
   (`adjust_style`)。它絕不會去動哪個欄位是哪個角色。
4. **re-render** — 帶著新的 `style` 重新繪製,最多 **2 次修正回合**。

這個迴圈會保留**最佳**的嘗試,永遠不會讓圖表變得更糟;如果它無法完全修好版面,
就回傳最佳的繪製結果,加上一行摘要說明還剩下什麼問題
(「Visual check (2 passes): auto-fixed overlap; still tiny_text.」)。
小型 VLM 永遠只負責*偵測*(可靠);*修正*則是確定性的,而且有 unit test 覆蓋。
這個 model 是一個可抽換的外部依賴(本地的 qwen2.5vl 透過 Ollama)—
需要多模態能力並不代表需要一個 hosted model。

設計上經過 grill 的決策見 `docs/plan-sci-plot.md`。
