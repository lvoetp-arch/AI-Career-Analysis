# S6 介面摘要

兩個 API 都需要 Supabase Bearer JWT。建立分析也須有最新版隱私同意。

| API | 輸入 | 成功回應 |
|---|---|---|
| POST /api/analyses | `{career_input_id}`，來自 S5 | `{analysis_id, status, result}` |
| GET /api/analyses/{id} | 分析 ID | `{analysis_id, status, result, created_at}` |

- status 為 running、succeeded 或 failed。只有 succeeded 有通過驗證的 result，格式見 [analysis_result.schema.json](../../backend/app/schemas/analysis_result.schema.json)。
- 同份輸入重送會沿用分析 ID。running 可輪詢 GET。失敗後可再 POST，修改快照內容後會取得新 ID。
- 建立分析可能回 ANALYSIS_TIMEOUT、ANALYSIS_INVALID、LLM_ERROR、RATE_LIMITED、NOT_FOUND 或 CONSENT_REQUIRED。讀取非本人或不存在的分析回 NOT_FOUND。
- 分析頁入口為 `analyzing.html?career_input_id=<uuid>`。成功後導向 `report.html?id=<analysis_id>`。
- `snapshot_for(profile, career_input)` 建立快照。`masked_snapshot(snapshot)` 建立送給模型的副本。`check_result(raw, snapshot)` 回傳 `(result 或 None, 驗證摘要)`。`generate(llm, snapshot, deadline, on_attempt)` 回傳驗證後的結果。
- 證據路徑以快照為根，例 `work_experience[0].title`、`career_input.skill_ratings[0]`。分析列保存原始快照，GET 的公開回應只包含結果和狀態。
