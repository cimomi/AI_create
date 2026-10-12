"""Render the local provenance manifest as a readable catalogue."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import HOME, ROOT  # noqa: E402
manifest = json.loads((ROOT / 'manifest.json').read_text())
lines = ['# Skills 目录与版本取舍', '',
         '按本机现有版本归并，没有将本次迁移表述为全部上游最新版更新。完整文件指纹和候选来源见 `manifest.json`。', '',
         '同名比较优先采用已核对的 Codex / DeepSeek 公共目录近期维护版本；平台专用适配分别保留。', '',
         '人物类的 15 个平铺副本与女娲库内新版完全一致，统一引用女娲库内文件。旧别名包的历史附加材料在恢复快照中保留，不再作为活动 Skill 注册。', '']
labels = {'common': '通用', 'codex': 'Codex 专用', 'claude': 'Claude 专用', 'hermes': 'Hermes 托管引用'}
for scope, label in labels.items():
    entries = [e for e in manifest['entries'] if e['scope'] == scope]
    lines += [f'## {label}（{len(entries)}）', '', '| Skill | 库内位置 | 选用来源 |', '| --- | --- | --- |']
    for entry in entries:
        destination = Path(entry['destination']).relative_to(ROOT)
        source = entry['selected']['path'].replace(str(HOME) + '/', '~/')
        lines.append(f'| {entry["name"]} | `{destination}` | `{source}` |')
    lines.append('')
lines += ['## 兼容性与保留边界', '',
          '- Karpathy Skill 的自定义 `type: perspective` 已移入 `metadata.type`，适配 Kimi Code；正文未修改。',
          '- `platforms/claude/deep-research.md` 是原有独立 Markdown 入口，保留原址链接。',
          '- Claude 旧 academic 系列引用的部分共享目录在迁移前已缺失；本次保留其内容，没有声称这些完整业务流程已经运行验收。',
          '- 平台内置与插件缓存继续由原软件管理，`managed/` 仅提供引用入口。',
          '- 4 个 Skill 包（guizang-ppt-skill、huashu-design、darwin-skill、guizang-social-card-skill）保留各自的上游 git 仓库，供 huashu-skill-updater 检查更新。',
          '- Obsidian 原稿保留。入口目录 `~/.claude/skills/.git` 已于 2026-10-12 删除（陈旧且工作树不一致），其历史不再保留。', '']
(ROOT / 'CATALOG.md').write_text('\n'.join(lines))
print(f'Catalogue written: {len(manifest["entries"])} skills')
