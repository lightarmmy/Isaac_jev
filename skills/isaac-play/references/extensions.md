# 工具与目标扩展

服务启动时加载项目 `skill_extensions/*.py`，调用每个模块的 `register(registry, runtime)`。工具目录由 registry 生成，因此新工具会出现在 CLI list 和 MCP tools/list。

一个可实际使用的只读扩展示例：

```python
def register(registry, runtime):
    async def inventory_counts():
        return runtime.memory.frame["player"].get("inventory", {})
    registry.register("inventory_counts", "Read resource counts", {}, [], inventory_counts)
```

新增目标通过 `runtime.register_goal(kind, candidate_provider, navigator_factory, completed)`。provider(memory) 返回含 action_id/kind/target_id 的候选列表；factory(frame,target) 返回带 room_id 与 step(frame) 方法的导航器；step 返回 isaac_jev.actions.Action；completed(memory,task) 按游戏事实返回布尔值。所有动作继续走单一 lease 出口、原观测帧有效期和暂停接管规则。

基础动作语义不可覆盖。当前服务拒绝 use_active=true。扩展炸弹/主动/购买等功能时同时明确新的能力配置、正常输入协议和完成判据，再扩技能范围；初版扩展默认用于额外查询或不改变当前能力边界的目标。

重启服务加载扩展：先脚本 shutdown，再 setup。关闭时会取消本服务自动会话并释放租约，采集与模型服务继续运行。扩展脚本从用户项目读取，网页正文只进入知识条目。
