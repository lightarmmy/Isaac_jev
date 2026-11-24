# 字典导入

get_item_details 参数：

```json
{"items":[{"item_kind":"collectible","item_id":1,"game_version":"Repentance+"}]}
```

miss 时以 wiki_search 的 query/search_url 用框架的网页工具定位并阅读正文。knowledge_upsert 参数为 `{ "entry": ... }`，entry 必需：item_kind、item_id、summary、sources。每个 source 包含真实的 Wiki `url` 和已阅读正文的 `excerpt`（20–12000 字符）。

可补充 names、aliases、game_version、item_type、quality、effects、stat_changes、synergies、unknown_fields、combat_support。默认 status=partial；只有实际核对页面身份及适用版本后才设置 identity_checked=true、applies_to 和 status=ready。搜索摘要保持 partial。数值、条件和组合均需对应阅读证据。

字段 combat_support 是针对当前 Jev 的项目判断，单独标注模型版本和依据。不要把 Wiki 效果误写成战斗模型已具备的能力。

条目存储在项目 `.local/knowledge_cache/items`，修订保存在 history。重复相同内容不产生新修订。未读正文、验证页和错误网页不构成证据。
