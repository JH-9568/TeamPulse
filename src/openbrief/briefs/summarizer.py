from collections.abc import Sequence

from openbrief.models import ClaimStatus, SourceItem, SourceItemKind
from openbrief.schemas import BriefClaim, BriefContent, BriefSection

SECTION_TITLES = {
    "direction": "Project direction",
    "design_changes": "Design changes",
    "decisions": "Decisions",
    "planning": "Planning",
    "tasks": "Tasks",
    "completed": "Completed work",
    "schedule_risks": "Schedule and delay risks",
    "conflicts": "Conflicts and open questions",
}


class StructuredBriefBuilder:
    """Deterministic fallback until a production LLM provider is selected."""

    def build(self, source_items: Sequence[SourceItem]) -> BriefContent:
        buckets: dict[str, list[BriefClaim]] = {key: [] for key in SECTION_TITLES}
        for item in source_items:
            status = self._claim_status(item)
            claim = BriefClaim(
                text=self._claim_text(item),
                status=status,
                source_item_ids=[str(item.id)],
            )
            buckets[self._section_key(item)].append(claim)

        sections = [
            BriefSection(key=key, title=title, claims=buckets[key])
            for key, title in SECTION_TITLES.items()
        ]
        return BriefContent(
            sections=sections,
            source_window={
                "source_item_count": len(source_items),
                "builder": "deterministic-fallback",
            },
            diff_from_last_confirmed=[],
        )

    def _section_key(self, item: SourceItem) -> str:
        lowered = f"{item.title}\n{item.body}".lower()
        provider = item.provider.value
        metadata_text = str(item.source_metadata).lower()
        if any(token in lowered for token in ["conflict", "contradict", "충돌"]):
            return "conflicts"
        if any(
            token in lowered
            for token in ["decision", "decided", "결정", "결론", "합의", "/meeting-end"]
        ):
            return "decisions"
        if any(
            token in lowered
            for token in ["blocker", "blocked", "지연", "막힘", "overdue", "기한 초과"]
        ):
            return "schedule_risks"
        if any(
            token in lowered
            for token in ["todo", "할 일", "해야", "담당", "assign", "action item", "/status"]
        ):
            return "tasks"
        if any(token in lowered for token in ["done", "completed", "완료", "끝남", "merged"]):
            return "completed"
        if provider == "figma" and item.kind == SourceItemKind.DESIGN_COMMENT:
            if any(token in lowered for token in ["todo", "수정", "확인", "반영", "요청"]):
                return "tasks"
            return "design_changes"
        if provider == "notion":
            if any(token in metadata_text for token in ["done", "complete", "완료"]):
                return "completed"
            if any(token in metadata_text for token in ["due", "deadline", "지연", "overdue"]):
                return "schedule_risks"
            return "planning"
        if provider == "discord" and item.kind == SourceItemKind.MEETING_MESSAGE:
            if any(token in lowered for token in ["결정", "결론", "담당", "하기로"]):
                return "decisions"
            return "planning"
        if provider == "github":
            if any(token in lowered for token in ["pull request", "pr", "review", "blocked"]):
                return "schedule_risks" if "blocked" in lowered else "tasks"
            if "issue" in lowered:
                return "tasks"
        if item.kind == SourceItemKind.DESIGN_UPDATE:
            return "design_changes"
        if item.kind == SourceItemKind.DESIGN_COMMENT:
            return "design_changes"
        if item.kind == SourceItemKind.PLANNING_DOC:
            return "planning"
        if item.kind == SourceItemKind.TASK_CHANGE:
            return "tasks"
        return "conflicts"

    def _claim_text(self, item: SourceItem) -> str:
        prefix = self._claim_prefix(item)
        body = item.body.strip()
        if body:
            return f"{prefix}{item.title}: {body[:500]}"
        return f"{prefix}{item.title}"

    def _claim_prefix(self, item: SourceItem) -> str:
        if item.provider.value == "figma":
            return "Figma 디자인 맥락 · "
        if item.provider.value == "notion":
            return "Notion 업무/문서 · "
        if item.provider.value == "discord":
            return "Discord 회의/대화 · "
        if item.provider.value == "github":
            return "GitHub 개발 맥락 · "
        return ""

    def _claim_status(self, item: SourceItem) -> ClaimStatus:
        lowered = f"{item.title}\n{item.body}".lower()
        if any(token in lowered for token in ["conflict", "contradict", "충돌"]):
            return ClaimStatus.CONFLICT
        if any(token in lowered for token in ["?", "확인 필요", "needs confirmation"]):
            return ClaimStatus.NEEDS_CONFIRMATION
        if item.kind in {SourceItemKind.MEETING_MESSAGE, SourceItemKind.DESIGN_COMMENT}:
            return ClaimStatus.AI_INFERENCE
        return ClaimStatus.CONFIRMED
