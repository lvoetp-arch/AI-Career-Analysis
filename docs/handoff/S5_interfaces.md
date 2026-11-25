# S5 介面摘要（給 S6 以後只附這份）

## API
`PUT /api/career-inputs`（需 Bearer JWT）
```json
{ "profile_id": "<uuid，已 confirmed>",
  "goal": "continue | switch | undecided",
  "skill_ratings": [ {"skill_id": "skill-3", "name": "Supabase", "rating": 3, "source": "resume"},
                     {"skill_id": null, "name": "Excel", "rating": 4, "source": "added"} ],
  "preferences": { /* job_preferences 子結構，全部可省略 */ } }
```
→ `200 {"career_input_id": "<uuid>"}`；`422 VALIDATION_ERROR`（`details:[{path,message}]`，path 如 `skill_ratings[2].rating`、`preferences.salary_expectation.raw_text`、`goal`、`profile_id`）；`404 NOT_FOUND`（profile 不存在、非本人或 id 格式錯）。
一個 profile 對應一列 `career_inputs`；重複 PUT 為更新，回傳同一個 id。

## 存進 `career_inputs` 的內容
- `goal`：三選一。
- `skill_ratings`：陣列，每項只有 `skill_id|null, name, rating(1–5 整數), source(resume|added)`；`resume` 項的 `name` 已是履歷內名稱。**自評，不是已證實能力**；證據請從 `resume_profiles.parsed_json.skills[].linked_ids` 另外取，不在此表。被使用者按「不評」的技能不會出現。
- `preferences`：已通過 `job_preferences` Schema；`remote_willingness` 一定有（可為 `unknown`），其餘有填才有；`salary_expectation` 只是使用者填的原文（`raw_text`）或履歷原值。

## 後端函式
- `app.services.s5.validation.validate_career_input(payload: dict, profile: dict | None) -> list[{"path","message"}]`（空列表＝通過；profile 為 `resume_profiles` 的列，需含 `status`、`parsed_json`）
- `normalize_ratings(items: list[dict], profile: dict | None) -> list[dict]`（驗證通過後才呼叫）
- `profile_skill_map(profile) -> {skill_id: name}`
- 常數：`GOALS`、`MAX_SKILLS=30`、`MAX_ADDED=10`、`MAX_ADDED_NAME=60`、`MAX_PREF_STR=200`、`MAX_PREF_LIST=20`

## 前端
- `frontend/assess.html` ＋ `js/assess.js` ＋ `css/assess.css`；腳本順序 config → supabase-js → auth → api → ui → assess。
- 進入條件：先 `Auth.requireLogin()` 通過（沒通過不呼叫任何 API），再 `GET /api/profiles/latest` 回 `status=confirmed`；否則顯示空白／導向 `review.html`、`upload.html`。
- 完成後導向 `analyzing.html?career_input_id=<uuid>`（`assess.js` 檔頭 `NEXT_PAGE` 常數）。
- 等級說明（逐字）：1 了解概念、需指導；2 能完成基礎任務；3 能獨立完成常見任務；4 能處理複雜問題並改善流程；5 能主導、設計方法並指導他人。

## 用法範例（S6 讀取）
```python
row = db.select_one("career_inputs", filters={"id": career_input_id}, columns="id,resume_profile_id,goal,skill_ratings,preferences")
rated = [r for r in row["skill_ratings"]]          # 只含使用者評過的技能
```
