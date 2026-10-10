"""项目记忆文件的结构规范：TASK.md、CLAUDE.md、docs/architecture.md、docs/adr/。

这些测试让「文档跟着代码走」变成可执行的检查：新增 engine 模块却没写进架构文档、
ADR 缺章节或引用了不存在的 ADR，都会让测试失败。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADR_DIR = ROOT / "docs" / "adr"
ADR_NAME = re.compile(r"^ADR-(\d{3})-[a-z0-9-]+\.md$")
ADR_SECTIONS = ["## Context", "## Options Considered", "## Decision", "## Why", "## Consequences"]


def adr_files():
    return sorted(p for p in ADR_DIR.glob("ADR-*.md"))


def test_task_md_has_required_sections():
    text = (ROOT / "TASK.md").read_text(encoding="utf-8")
    for h in ["## Goal", "## Constraints", "## Done", "## In Progress", "## Next", "## Blockers / Open Questions"]:
        assert h in text, f"TASK.md 缺少 {h}"


def test_claude_md_has_session_start_and_rules():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for key in ["TASK.md", "docs/architecture.md", "docs/adr", "git status", "pytest", "Git 规范", "文档维护"]:
        assert key in text, f"CLAUDE.md 缺少「{key}」"


def test_adr_naming_numbering_and_sections():
    files = adr_files()
    assert files, "docs/adr/ 下没有 ADR"
    numbers = []
    for f in files:
        m = ADR_NAME.match(f.name)
        assert m, f"ADR 文件名不规范：{f.name}（应为 ADR-NNN-小写短横线.md）"
        numbers.append(int(m.group(1)))
        text = f.read_text(encoding="utf-8")
        assert text.startswith(f"# ADR-{m.group(1)}"), f"{f.name} 标题应以 # ADR-{m.group(1)} 开头"
        assert "- Status:" in text and "- Date:" in text, f"{f.name} 缺少 Status / Date"
        for sec in ADR_SECTIONS:
            assert sec in text, f"{f.name} 缺少章节 {sec}"
    assert len(numbers) == len(set(numbers)), "ADR 编号重复"
    assert numbers == list(range(1, len(numbers) + 1)), f"ADR 编号应从 001 连续递增：{numbers}"


def test_adr_supersedes_references_are_consistent():
    existing = {int(ADR_NAME.match(f.name).group(1)): f for f in adr_files()}
    for n, f in existing.items():
        text = f.read_text(encoding="utf-8")
        for ref in re.findall(r"Supersedes ADR-(\d{3})", text):
            old = int(ref)
            assert old in existing and old < n, f"{f.name} 取代了不存在或更新的 ADR-{ref}"
            old_text = existing[old].read_text(encoding="utf-8")
            assert f"Superseded by ADR-{n:03d}" in old_text, f"ADR-{ref} 的 Status 行应标注 Superseded by ADR-{n:03d}"


def test_adr_index_lists_every_adr():
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    for f in adr_files():
        assert f.name in index, f"docs/adr/README.md 索引缺少 {f.name}"


def test_architecture_covers_every_engine_module_and_workflow():
    text = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    for py in sorted((ROOT / "engine").glob("*.py")):
        if py.name == "__init__.py":
            continue
        assert f"engine/{py.name}" in text, f"docs/architecture.md 没有写到 engine/{py.name}"
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        assert wf.stem in text, f"docs/architecture.md 没有写到工作流 {wf.stem}"
