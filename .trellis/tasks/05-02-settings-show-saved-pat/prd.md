# brainstorm: settings show saved PAT state

## Goal

Settings 页面在检测到本机安全存储中已有 GitHub PAT 时，应当在应用启动或重新打开后直接呈现“已配置且可用”的状态。用户不应每次都重新粘贴 PAT，或额外点击 `Use Saved Token` 才能看到 owner、Project 等 GitHub discovery 信息。

## What I Already Know

* 用户观察到：应用每次重启动后，Settings 里 PAT 输入框为空，owner、Project 等信息不会显示；必须重新添加 PAT 或点击 `Use Saved Token`。
* 用户期望：如果 PAT 已保存，Settings 中应直接用 password 样式的加密占位字符串表示 token 已存在。
* 用户期望：保存的 PAT 应当在打开 Settings 时直接驱动 owner、Project 等已保存配置的可视化状态，而不是让用户手动再次选择“使用保存的 PAT”。
* 当前 Electron main process 已通过 `safeStorage` 保存 PAT，`tokenStatus()` 只返回 `configured` 和 `encryptionAvailable`，不会向 renderer 返回明文 token。
* 当前后端启动时会调用 `bootstrapSettings()`，并通过 `applySettingsToBackend()` 把保存的 token 热应用到后端；问题主要在 Settings renderer 表单体验和 discovery 初始化。

## Assumptions

* Renderer 不应拿到或显示真实 PAT 明文。
* password 输入框里的内容应是固定掩码占位符，只表达“已有保存的 token”，保存时默认不覆盖现有 token。
* 用户手动输入新 PAT 后，保存语义仍应是 `mode: "set"`；用户点击清除时，保存语义仍应是 `mode: "clear"`。
* 若保存的 PAT 解密失败、权限不足或 discovery API 返回错误，页面应保留已保存配置字段，并通过现有错误提示显示问题。

## Confirmed Product Decision

* 打开 Settings 时，如果本机已有保存的 PAT，应立即发起一次 GitHub discovery，并明确呈现当前应用正在使用哪个 owner / Project。
* 这样做的理由是：Settings 是配置确认界面，用户进入页面时需要直接看到当前有效配置；空白 select 会让人误以为配置丢失或应用没有使用已保存 PAT。

## Requirements

* Settings 初始加载时，如果 `tokenStatus.configured` 为 true，GitHub token 输入框应显示 password 样式的掩码占位值，而不是空白。
* 掩码占位值不应被当作真实 PAT 保存，也不应改变已保存 token。
* 在已有保存 PAT 的状态下，打开 Settings 应自动使用已保存 PAT 执行一次 GitHub discovery。
* 自动 discovery 应读取 owner 列表和当前 owner 的 Project 列表，并把当前 settings 中保存的 owner / project number 作为优先选中项。
* owner 和 Project 控件应优先显示当前 settings 中已保存的 owner / project number；自动 discovery 失败时也不能显示为空白。
* 自动 discovery 失败时，应保留本地 settings 中的 owner / project 展示，并通过现有错误提示显示失败原因。
* Connect / Inspect 等手动 discovery 操作在已有保存 PAT 且未输入新 PAT 时，默认使用已保存 PAT。
* 用户仍可粘贴新 PAT 覆盖旧 token。
* 用户仍可显式清除已保存 token。
* 任何实现不得把真实 PAT 返回到 renderer、日志、错误提示或 settings 文件。

## Acceptance Criteria

* [ ] 启动应用并进入 Settings，在已有保存 PAT 时，token 输入框显示为 password 样式的掩码值。
* [ ] 未修改 token 直接点击 Save / Save & Apply，不会把掩码值写入安全存储，也不会清除原 token。
* [ ] 已保存 PAT 存在时，打开 Settings 会自动用保存的 PAT 执行 GitHub discovery，而不是要求用户重新输入或点击 `Use Saved Token`。
* [ ] 自动 discovery 成功后，owner 下拉显示 GitHub 返回的 owner 候选，并选中当前 settings owner 或合理默认 owner。
* [ ] 自动 discovery 成功后，Project 下拉显示当前 owner 的 GitHub Project 候选，并选中当前 settings project number 或合理默认 Project。
* [ ] 自动 discovery 失败时，owner / Project 下拉仍显示当前 settings 中保存的 owner 和 project number，不为空白。
* [ ] 粘贴新 PAT 后保存，会替换已保存 token。
* [ ] Clear Token 后保存，会清除安全存储里的 token，并恢复未保存状态。
* [ ] lint、typecheck 通过。

## Definition of Done

* Tests added or updated where practical.
* Lint / typecheck pass.
* README or docs updated if user-visible settings behavior documentation changes.
* No PAT leaks in renderer state serialization, logs, settings JSON, or error payloads.

## Out of Scope

* 不改变 PAT 的存储机制，不引入系统钥匙串外的新 secret provider。
* 不要求 renderer 能读取真实 PAT。
* 不重做 Settings 页整体信息架构。
* 不改变后端 GitHub discovery API 的权限模型，除非实现过程中发现必须补齐契约。

## Technical Notes

* `desktop/electron/main.ts`:
  * `tokenStatus()` 只返回保存状态，不返回明文。
  * `readGithubToken()` 只在 main process 解密 token。
  * `resolveDiscoveryToken()` 当前只有在 `request.use_saved_token` 为 true 时才读取保存 token。
  * `bootstrapSettings()` 启动时已经把保存 token 应用到后端。
* `desktop/src/App.tsx`:
  * `SettingsPage` 当前使用 `useState("")` 初始化 `tokenInput`，保存后也会 `setTokenInput("")`。
  * `GitHubSettings` 当前 `discoverySource` 默认是 `"input"`。
  * `Connect PAT` 按钮当前在 `!tokenInput.trim()` 时禁用；保存 PAT 存在但输入框为空时不可用。
  * `Use Saved Token` 按钮可以显式调用 saved token discovery，但用户认为不应每次手动点击。
  * Owner / Project 下拉选项完全来自 `owners` / `projects` discovery state；启动时数组为空，导致已保存的 `settings.tracker.owner` / `settings.tracker.project_number` 没有对应 `<option>` 可展示。
* `desktop/src/types.ts` 和 `desktop/src/settingsClient.ts` 当前 `SettingsLoadResult` 未携带任何 token display hint。
