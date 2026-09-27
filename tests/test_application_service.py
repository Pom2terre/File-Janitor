"""Tests de la façade applicative de File Janitor."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from file_janitor.application import (
    PlannedRecoveryState,
    DEFAULT_MAX_ITEMS_PER_CATEGORY,
    AnalysisItem,
    AnalysisResult,
    AnalysisSummary,
    CategoryDetails,
    CategorySummary,
    ClassificationMode,
    DateGranularity,
    GroupBy,
    analyze_folder,
    as_copy_actions,
    build_analysis_result,
    build_report,
    classification_plan_config,
    clean_actions,
    create_plan,
    duplicate_actions,
    execute_actions,
    get_history_operations,
    install_schedule,
    large_file_actions,
    list_history,
    list_schedules,
    mismatch_actions,
    preview_schedule,
    remove_schedule,
    scan_folder,
    sort_actions,
    summarize_analysis,
    ScheduleError,
    render_report_html,
    undo_execution,
    write_report_html,
)
from file_janitor.models import (
    ActionItem,
    ActionKind,
    FileCategory,
)
from file_janitor.storage.history import (
    PlannedRecoveryState,
    BatchStatus,
    OperationStatus,
)


def test_scan_folder_returns_scan_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("data")

    result = scan_folder(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
    )

    assert result.root == tmp_path.resolve()
    assert result.total_count == 1
    assert result.files[0].path == source


def test_create_plan_wraps_planner(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("data")

    scan = scan_folder(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
    )

    plan = create_plan(scan)

    assert plan.scan is scan



def test_classification_plan_config_maps_product_modes_to_engine() -> None:
    extension = classification_plan_config(ClassificationMode.EXTENSION)
    date = classification_plan_config(ClassificationMode.DATE)
    common_name = classification_plan_config(ClassificationMode.COMMON_NAME)
    size = classification_plan_config(ClassificationMode.SIZE)

    assert extension.group_by is GroupBy.EXTENSION
    assert date.group_by is GroupBy.DATE
    assert date.date_granularity is DateGranularity.MONTH
    assert common_name.group_by is GroupBy.EXTENSION
    assert common_name.template is not None
    assert common_name.template_only is True
    assert common_name.template_min_group_size == 2
    assert common_name.infer_common_roots is True
    assert size.group_by is GroupBy.SIZE


def test_classification_modes_produce_expected_destinations(tmp_path: Path) -> None:
    first = tmp_path / "report-1.pdf"
    second = tmp_path / "report-2.pdf"
    first.write_text("one")
    second.write_text("two")

    extension = analyze_folder(
        tmp_path,
        config=classification_plan_config(ClassificationMode.EXTENSION),
        compute_hashes=False,
        compute_content_type=False,
    )
    extension_items = extension.details_for("to_sort")
    assert extension_items is not None
    assert {item.destination for item in extension_items.items} == {
        tmp_path / "pdf" / "report-1.pdf",
        tmp_path / "pdf" / "report-2.pdf",
    }

    common_name = analyze_folder(
        tmp_path,
        config=classification_plan_config(ClassificationMode.COMMON_NAME),
        compute_hashes=False,
        compute_content_type=False,
    )
    common_items = common_name.details_for("to_sort")
    assert common_items is not None
    assert {item.destination for item in common_items.items} == {
        tmp_path / "report" / "report-1.pdf",
        tmp_path / "report" / "report-2.pdf",
    }

def test_classification_date_and_size_modes_produce_expected_destinations(
    tmp_path: Path,
) -> None:
    import os
    from datetime import datetime

    dated = tmp_path / "dated.txt"
    dated.write_text("dated")

    timestamp = datetime(2026, 2, 15, 12, 0).timestamp()
    os.utime(dated, (timestamp, timestamp))

    date_result = analyze_folder(
        tmp_path,
        config=classification_plan_config(ClassificationMode.DATE),
        compute_hashes=False,
        compute_content_type=False,
    )
    date_items = date_result.details_for("to_sort")
    assert date_items is not None
    assert {item.destination for item in date_items.items} == {
        tmp_path / "2026-02" / "dated.txt",
    }

    small = tmp_path / "small.bin"
    small.write_bytes(b"x" * 1024)

    size_result = analyze_folder(
        tmp_path,
        config=classification_plan_config(ClassificationMode.SIZE),
        compute_hashes=False,
        compute_content_type=False,
    )
    size_items = size_result.details_for("to_sort")
    assert size_items is not None

    destinations = {
        item.path.name: item.destination
        for item in size_items.items
    }

    assert destinations["small.bin"] == (
        tmp_path / "moins_de_10_Ko" / "small.bin"
    )

def test_execute_and_undo_through_application_api(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("important")

    destination = (
        tmp_path
        / "sorted"
        / "source.txt"
    )

    stat_result = source.stat()

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=stat_result.st_size,
        reason="test application layer",
        destination=destination,
        action=ActionKind.MOVE,
    )

    db_path = tmp_path / "history.db"

    execute_result = execute_actions(
        [item],
        root=str(tmp_path),
        db_path=db_path,
    )

    assert execute_result.success == 1
    assert not execute_result.errors
    assert not source.exists()
    assert destination.read_text() == "important"

    batches = list_history(
        db_path=db_path,
    )

    assert len(batches) == 1
    assert batches[0].id == execute_result.batch_id
    assert batches[0].status is BatchStatus.COMPLETED

    operations = get_history_operations(
        execute_result.batch_id,
        db_path=db_path,
    )

    assert len(operations) == 1
    assert operations[0].status is OperationStatus.COMPLETED

    undo_result = undo_execution(
        execute_result.batch_id,
        db_path=db_path,
    )

    assert undo_result.success == 1
    assert not undo_result.errors
    assert source.read_text() == "important"
    assert not destination.exists()

    operations = get_history_operations(
        execute_result.batch_id,
        db_path=db_path,
    )

    assert operations[0].status is OperationStatus.UNDONE

def test_as_copy_actions_is_immutable_translation(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=4,
        reason="test copy intent",
        destination=destination,
        action=ActionKind.MOVE,
    )

    translated = as_copy_actions([item])

    assert translated[0] is not item
    assert translated[0].action is ActionKind.COPY
    assert item.action is ActionKind.MOVE
    assert translated[0].path == item.path
    assert translated[0].destination == item.destination

def test_report_functions_are_available_through_application_api(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("data")

    scan = scan_folder(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
    )
    plan = create_plan(scan)

    data = build_report(scan, plan)
    html = render_report_html(
        scan,
        plan,
        max_items_per_category=DEFAULT_MAX_ITEMS_PER_CATEGORY,
    )
    output = tmp_path / "report.html"
    write_report_html(
        scan,
        plan,
        output,
        max_items_per_category=DEFAULT_MAX_ITEMS_PER_CATEGORY,
    )

    assert isinstance(data, dict)
    assert isinstance(html, str)
    assert html
    assert output.read_text(encoding="utf-8") == html



def test_preview_schedule_hides_scheduler_infrastructure(monkeypatch, tmp_path: Path) -> None:
    from file_janitor.application import service

    monkeypatch.setattr(service.scheduler, "cron_available", lambda: True)
    monkeypatch.setattr(
        service.scheduler,
        "build_cron_expression",
        lambda every, unit: "0 */2 * * *",
    )
    monkeypatch.setattr(
        service.scheduler,
        "build_command",
        lambda folder, action, extra_args: "janitor sort --apply --yes",
    )

    preview = preview_schedule(
        tmp_path,
        every=2,
        unit="hours",
        action="sort",
    )

    assert preview.cron_available is True
    assert preview.cron_expression == "0 */2 * * *"
    assert preview.command == "janitor sort --apply --yes"
    assert preview.windows_hint is None


def test_preview_schedule_returns_windows_hint_when_cron_is_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from file_janitor.application import service

    monkeypatch.setattr(service.scheduler, "cron_available", lambda: False)
    monkeypatch.setattr(
        service.scheduler,
        "schtasks_hint",
        lambda *args, **kwargs: "schtasks /Create ...",
    )

    preview = preview_schedule(
        tmp_path,
        every=1,
        unit="days",
        action="clean",
    )

    assert preview.cron_available is False
    assert preview.cron_expression is None
    assert preview.command is None
    assert preview.windows_hint == "schtasks /Create ..."


def test_schedule_action_validation_is_application_responsibility(tmp_path: Path) -> None:
    with pytest.raises(ScheduleError, match="sort.*clean"):
        preview_schedule(
            tmp_path,
            every=1,
            unit="hours",
            action="bogus",
        )

    with pytest.raises(ScheduleError, match="sort.*clean"):
        install_schedule(
            tmp_path,
            every=1,
            unit="hours",
            action="bogus",
        )


def test_schedule_mutations_are_available_through_application_api(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from file_janitor.application import service

    job = SimpleNamespace(
        job_id="abc123",
        cron_expression="0 * * * *",
        description="sort test",
    )
    monkeypatch.setattr(service.scheduler, "install_job", lambda *args, **kwargs: job)
    monkeypatch.setattr(service.scheduler, "list_jobs", lambda: [job])
    monkeypatch.setattr(service.scheduler, "remove_job", lambda job_id: job_id == "abc123")

    installed = install_schedule(
        tmp_path,
        every=1,
        unit="hours",
        action="sort",
    )

    assert installed is job
    assert list_schedules() == [job]
    assert remove_schedule("abc123") is True
    assert remove_schedule("missing") is False


def test_action_selectors_express_application_use_cases(tmp_path: Path) -> None:
    from file_janitor.models import Plan, ScanResult

    scan = ScanResult(root=tmp_path)
    plan = Plan(scan=scan)

    def add(category: FileCategory, name: str) -> ActionItem:
        item = ActionItem(
            category=category,
            path=tmp_path / name,
            size=1,
            reason="test selector",
        )
        plan.add(item)
        return item

    duplicate = add(FileCategory.DUPLICATE, "duplicate.txt")
    archive = add(FileCategory.OLD_ARCHIVE, "archive.zip")
    large = add(FileCategory.LARGE_FILE, "large.bin")
    sortable = add(FileCategory.TO_SORT, "sort.txt")
    mismatch = add(FileCategory.EXTENSION_MISMATCH, "mismatch.jpg")

    assert duplicate_actions(plan) == [duplicate]
    assert large_file_actions(plan) == [large]
    assert mismatch_actions(plan) == [mismatch]
    assert sort_actions(plan) == [sortable]
    assert clean_actions(plan) == [duplicate, archive]


def test_action_selectors_do_not_expose_plan_lists(tmp_path: Path) -> None:
    from file_janitor.models import Plan, ScanResult

    plan = Plan(scan=ScanResult(root=tmp_path))
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=tmp_path / "source.txt",
        size=1,
        reason="test isolation",
    )
    plan.add(item)

    selected = sort_actions(plan)
    selected.clear()

    assert plan.items(FileCategory.TO_SORT) == [item]


def test_summarize_analysis_exposes_ui_neutral_summary(tmp_path: Path) -> None:
    from file_janitor.models import Plan, ScanResult

    scan = ScanResult(root=tmp_path)
    plan = Plan(scan=scan)
    plan.add(
        ActionItem(
            category=FileCategory.TO_SORT,
            path=tmp_path / "sort.txt",
            size=12,
            reason="test summary",
        )
    )
    plan.add(
        ActionItem(
            category=FileCategory.DUPLICATE,
            path=tmp_path / "duplicate.txt",
            size=34,
            reason="test summary",
        )
    )

    summary = summarize_analysis(scan, plan)

    assert isinstance(summary, AnalysisSummary)
    assert summary.root == tmp_path
    assert summary.total_count == 0
    assert summary.total_size == 0
    assert all(isinstance(item, CategorySummary) for item in summary.categories)

    by_key = {item.key: item for item in summary.categories}
    assert by_key["to_sort"].label == "Fichiers à classer"
    assert by_key["to_sort"].item_count == 1
    assert by_key["to_sort"].total_size == 12
    assert by_key["to_sort"].display_metric == "count"
    assert by_key["duplicate"].item_count == 1
    assert by_key["duplicate"].total_size == 34
    assert by_key["duplicate"].display_metric == "size"


def test_summarize_analysis_categories_are_immutable(tmp_path: Path) -> None:
    from dataclasses import FrozenInstanceError
    from file_janitor.models import Plan, ScanResult

    summary = summarize_analysis(
        ScanResult(root=tmp_path),
        Plan(scan=ScanResult(root=tmp_path)),
    )

    with pytest.raises(FrozenInstanceError):
        summary.categories[0].label = "changed"

def test_build_analysis_result_exposes_read_only_details(tmp_path: Path) -> None:
    from dataclasses import FrozenInstanceError
    from file_janitor.models import Plan, ScanResult

    scan = ScanResult(root=tmp_path)
    plan = Plan(scan=scan)
    destination = tmp_path / "Sorted" / "source.txt"
    plan.add(
        ActionItem(
            category=FileCategory.TO_SORT,
            path=tmp_path / "source.txt",
            size=1024,
            reason="test details",
            destination=destination,
            action=ActionKind.MOVE,
        )
    )

    result = build_analysis_result(scan, plan)

    assert isinstance(result, AnalysisResult)
    details = result.details_for("to_sort")
    assert isinstance(details, CategoryDetails)
    assert details.label == "Fichiers à classer"
    assert len(details.items) == 1
    item = details.items[0]
    assert isinstance(item, AnalysisItem)
    assert item.path == tmp_path / "source.txt"
    assert item.size == 1024
    assert item.reason == "test details"
    assert item.destination == destination
    assert item.action == "move"
    assert result.details_for("unknown") is None

    with pytest.raises(FrozenInstanceError):
        item.reason = "changed"




def test_build_analysis_result_propagates_scan_errors(tmp_path: Path) -> None:
    from file_janitor.models import Plan, ScanResult

    scan = ScanResult(
        root=tmp_path,
        errors=[
            f"{tmp_path / 'blocked.txt'}: Permission denied",
            f"{tmp_path / 'vanished.txt'}: No such file",
        ],
    )
    result = build_analysis_result(scan, Plan(scan=scan))

    assert result.scan_errors == (
        f"{tmp_path / 'blocked.txt'}: Permission denied",
        f"{tmp_path / 'vanished.txt'}: No such file",
    )


def test_analysis_result_scan_errors_are_immutable(tmp_path: Path) -> None:
    from file_janitor.models import Plan, ScanResult

    scan = ScanResult(root=tmp_path, errors=["first"])
    result = build_analysis_result(scan, Plan(scan=scan))
    scan.errors.append("later")

    assert result.scan_errors == ("first",)


def test_analyze_folder_orchestrates_read_only_analysis(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from file_janitor.application import service

    scan = object()
    plan = object()
    expected = object()

    monkeypatch.setattr(
        service,
        "scan_folder",
        lambda folder, **kwargs: scan,
    )
    monkeypatch.setattr(
        service,
        "create_plan",
        lambda scan_value, config=None: plan,
    )
    captured_build: dict[str, object] = {}

    def fake_build_analysis_result(
        scan_value,
        plan_value,
        **kwargs,
    ):
        captured_build["scan"] = scan_value
        captured_build["plan"] = plan_value
        captured_build["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(
        service,
        "build_analysis_result",
        fake_build_analysis_result,
    )

    result = analyze_folder(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
    )

    assert result is expected
    assert captured_build["scan"] is scan
    assert captured_build["plan"] is plan
    assert captured_build["kwargs"] == {
        "hashes_computed": False,
        "content_types_computed": False,
    }


def test_execute_selected_actions_uses_only_previewed_selection(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import execute_selected_actions
    from file_janitor.application import service
    from file_janitor.models import Plan, ScanResult

    scan = ScanResult(root=tmp_path)
    plan = Plan(scan=scan)
    first = ActionItem(
        category=FileCategory.TO_SORT,
        path=tmp_path / "first.txt",
        size=1,
        reason="test",
        destination=tmp_path / "Sorted" / "first.txt",
        action=ActionKind.MOVE,
    )
    second = ActionItem(
        category=FileCategory.TO_SORT,
        path=tmp_path / "second.txt",
        size=1,
        reason="test",
        destination=tmp_path / "Sorted" / "second.txt",
        action=ActionKind.MOVE,
    )
    plan.add(first)
    plan.add(second)
    result = build_analysis_result(scan, plan)
    calls = []

    def fake_execute(items, *, root, db_path=None):
        calls.append((items, root, db_path))
        return service.ExecuteResult(batch_id=7, success=1, errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)
    execution = execute_selected_actions(result, {("to_sort", 1)})

    assert execution.success == 1
    assert len(calls) == 1
    executed_items, root, db_path = calls[0]
    assert root == str(tmp_path)
    assert db_path is None
    assert len(executed_items) == 1
    executed = executed_items[0]
    assert executed.path == second.path
    assert executed.destination == second.destination
    assert executed.conflict_policy.value == "skip"
    assert second.conflict_policy.value == "rename"


def test_execute_selected_actions_rejects_unknown_selection(tmp_path: Path) -> None:
    from file_janitor.application import execute_selected_actions
    from file_janitor.models import Plan, ScanResult

    result = build_analysis_result(
        ScanResult(root=tmp_path),
        Plan(scan=ScanResult(root=tmp_path)),
    )

    with pytest.raises(ValueError, match="invalide ou périmée"):
        execute_selected_actions(result, {("to_sort", 999)})


def test_get_history_summary_hides_storage_types_and_is_lazy(tmp_path: Path) -> None:
    from file_janitor.application import (
    PlannedRecoveryState,
        get_history_operation_summary,
        get_history_summary,
    )

    source = tmp_path / "source.txt"
    source.write_text("history")
    destination = tmp_path / "sorted" / "source.txt"
    stat_result = source.stat()
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=stat_result.st_size,
        reason="history summary",
        destination=destination,
        action=ActionKind.MOVE,
    )
    db_path = tmp_path / "history-summary.db"

    execution = execute_actions([item], root=str(tmp_path), db_path=db_path)
    batches = get_history_summary(db_path=db_path)

    assert len(batches) == 1
    batch = batches[0]
    assert batch.id == execution.batch_id
    assert batch.root == str(tmp_path)
    assert batch.status == "completed"
    assert batch.undone is False
    assert batch.planned_count == 1
    assert batch.success_count == 1
    assert batch.failed_count == 0
    assert batch.skipped_count == 0
    assert not hasattr(batch, "operations")
    assert type(batch).__module__ == "file_janitor.application.service"

    operations = get_history_operation_summary(batch.id, db_path=db_path)
    assert len(operations) == 1
    operation = operations[0]
    assert operation.kind == "move"
    assert operation.original_path == source
    assert operation.stored_path == destination
    assert operation.size == stat_result.st_size
    assert operation.status == "completed"
    assert operation.error is None
    assert type(operation).__module__ == "file_janitor.application.service"


def test_classification_group_summary_exposes_engine_destinations(tmp_path: Path) -> None:
    from file_janitor.application import summarize_classification_groups

    result = AnalysisResult(
        summary=AnalysisSummary(root=tmp_path, total_count=3, total_size=6, categories=()),
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(tmp_path / "a.pdf", 1, "x", tmp_path / "pdf" / "a.pdf", "move"),
                    AnalysisItem(tmp_path / "b.pdf", 2, "x", tmp_path / "pdf" / "b.pdf", "move"),
                    AnalysisItem(tmp_path / "c.txt", 3, "x", tmp_path / "txt" / "c.txt", "move"),
                ),
            ),
        ),
    )

    groups = summarize_classification_groups(result)

    assert [(group.name, group.item_count, group.total_size) for group in groups] == [
        ("pdf", 2, 3),
        ("txt", 1, 3),
    ]

def test_classification_plan_config_preserves_destination_root(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "sorted"

    for mode in ClassificationMode:
        config = classification_plan_config(
            mode,
            destination_root=destination,
        )
        assert config.destination_root == destination


def test_classification_destination_root_redirects_preview(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "report.pdf").write_text("report")

    result = analyze_folder(
        source,
        config=classification_plan_config(
            ClassificationMode.EXTENSION,
            destination_root=destination,
        ),
        compute_hashes=False,
        compute_content_type=False,
    )

    details = result.details_for("to_sort")
    assert details is not None
    assert {item.destination for item in details.items} == {
        destination / "pdf" / "report.pdf",
    }

def test_analyze_folder_reports_progress_stages(tmp_path: Path) -> None:
    stages: list[str] = []
    (tmp_path / "a.txt").write_text("same")
    (tmp_path / "b.txt").write_text("same")

    analyze_folder(
        tmp_path,
        compute_hashes=True,
        compute_content_type=True,
        progress_callback=stages.append,
    )

    assert stages == [
        "metadata",
        "hashes",
        "content",
        "planning",
        "result",
    ]


def test_analyze_folder_skips_content_progress_when_disabled(tmp_path: Path) -> None:
    stages: list[str] = []
    (tmp_path / "a.txt").write_text("content")

    analyze_folder(
        tmp_path,
        compute_hashes=True,
        compute_content_type=False,
        progress_callback=stages.append,
    )

    assert "metadata" in stages
    assert "hashes" in stages
    assert "content" not in stages
    assert stages[-2:] == ["planning", "result"]


def test_duplicate_groups_are_exposed_with_all_exact_members(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from file_janitor.models import FileRecord, Plan, ScanResult

    first = FileRecord(
        path=tmp_path / "first.txt", size=4,
        mtime=datetime(2024, 1, 1, tzinfo=timezone.utc), extension=".txt", hash="abc123",
    )
    second = FileRecord(
        path=tmp_path / "second.txt", size=4,
        mtime=datetime(2024, 1, 2, tzinfo=timezone.utc), extension=".txt", hash="abc123",
    )
    scan = ScanResult(root=tmp_path, files=[first, second])
    result = build_analysis_result(scan, Plan(scan=scan))

    assert len(result.duplicate_groups) == 1
    assert result.duplicate_groups[0].key == "abc123"
    assert {member.path for member in result.duplicate_groups[0].members} == {
        first.path, second.path
    }




def test_resolved_duplicate_group_preview_matches_execution_plan(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone
    from file_janitor.application import resolve_duplicate_groups_for_selection
    from file_janitor.models import FileRecord, Plan, ScanResult

    records = [
        FileRecord(
            path=tmp_path / name,
            size=4,
            mtime=datetime(2024, 1, day, tzinfo=timezone.utc),
            extension=".txt",
            hash="group3",
        )
        for day, name in enumerate(("a.txt", "b.txt", "c.txt"), start=1)
    ]
    scan = ScanResult(root=tmp_path, files=records)
    plan = Plan(scan=scan)
    for record in records:
        plan.add(
            ActionItem(
                category=FileCategory.TO_SORT,
                path=record.path,
                size=4,
                reason="Classer",
                destination=tmp_path / "Sorted" / record.path.name,
                action=ActionKind.MOVE,
            )
        )
    for record in records[1:]:
        plan.add(
            ActionItem(
                category=FileCategory.DUPLICATE,
                path=record.path,
                size=4,
                reason="Doublon",
                action=ActionKind.TRASH,
            )
        )
    result = build_analysis_result(scan, plan)

    resolved = resolve_duplicate_groups_for_selection(
        result,
        {("duplicate", 0)},
        {"group3": records[1].path},
    )

    assert len(resolved) == 1
    group = resolved[0]
    assert group.keeper == records[1].path
    assert group.mutation_count == 3
    assert [(item.path, item.action) for item in group.preview_items] == [
        (records[1].path, "move"),
        (records[0].path, "trash"),
        (records[2].path, "trash"),
    ]
    assert [(item.path, item.action) for item in group.actions] == [
        (records[1].path, ActionKind.MOVE),
        (records[0].path, ActionKind.TRASH),
        (records[2].path, ActionKind.TRASH),
    ]
    assert "Fichier conservé (keeper)" in group.preview_items[0].reason


def test_resolved_duplicate_group_shows_keeper_without_fake_mutation(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone
    from file_janitor.application import resolve_duplicate_groups_for_selection
    from file_janitor.models import FileRecord, Plan, ScanResult

    first = FileRecord(
        path=tmp_path / "a.txt",
        size=4,
        mtime=datetime(2024, 1, 1, tzinfo=timezone.utc),
        extension=".txt",
        hash="samehash",
    )
    second = FileRecord(
        path=tmp_path / "b.txt",
        size=4,
        mtime=datetime(2024, 1, 2, tzinfo=timezone.utc),
        extension=".txt",
        hash="samehash",
    )
    scan = ScanResult(root=tmp_path, files=[first, second])
    plan = Plan(scan=scan)
    plan.add(
        ActionItem(
            category=FileCategory.DUPLICATE,
            path=second.path,
            size=4,
            reason="Doublon",
            action=ActionKind.TRASH,
        )
    )
    result = build_analysis_result(scan, plan)

    resolved = resolve_duplicate_groups_for_selection(
        result,
        {("duplicate", 0)},
        {"samehash": second.path},
    )[0]

    assert [(item.path, item.action) for item in resolved.preview_items] == [
        (second.path, "keep"),
        (first.path, "trash"),
    ]
    assert [(item.path, item.action) for item in resolved.actions] == [
        (first.path, ActionKind.TRASH),
    ]
    assert resolved.mutation_count == 1


def test_duplicate_group_selection_is_atomic_even_with_one_selected_member(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone
    from file_janitor.application import resolve_selected_actions
    from file_janitor.models import FileRecord, Plan, ScanResult

    records = [
        FileRecord(
            path=tmp_path / name,
            size=4,
            mtime=datetime(2024, 1, day, tzinfo=timezone.utc),
            extension=".txt",
            hash="group3",
        )
        for day, name in enumerate(("a.txt", "b.txt", "c.txt"), start=1)
    ]
    scan = ScanResult(root=tmp_path, files=records)
    plan = Plan(scan=scan)
    for record in records[1:]:
        plan.add(
            ActionItem(
                category=FileCategory.DUPLICATE,
                path=record.path,
                size=4,
                reason="Doublon",
                action=ActionKind.TRASH,
            )
        )
    result = build_analysis_result(scan, plan)

    actions = resolve_selected_actions(
        result,
        {("duplicate", 0)},
        duplicate_keepers={"group3": records[1].path},
    )

    assert [(item.path, item.action) for item in actions] == [
        (records[0].path, ActionKind.TRASH),
        (records[2].path, ActionKind.TRASH),
    ]


def test_duplicate_resolution_can_keep_planner_duplicate_and_trash_original(
    monkeypatch, tmp_path: Path
) -> None:
    from datetime import datetime, timezone
    from file_janitor.application import execute_selected_actions
    from file_janitor.application import service
    from file_janitor.models import FileRecord, Plan, ScanResult

    original = FileRecord(
        path=tmp_path / "original.txt", size=4,
        mtime=datetime(2024, 1, 1, tzinfo=timezone.utc), extension=".txt", hash="samehash",
    )
    duplicate = FileRecord(
        path=tmp_path / "duplicate.txt", size=4,
        mtime=datetime(2024, 1, 2, tzinfo=timezone.utc), extension=".txt", hash="samehash",
    )
    original.path.write_text("same")
    duplicate.path.write_text("same")

    scan = ScanResult(root=tmp_path, files=[original, duplicate])
    plan = Plan(scan=scan)
    plan.add(ActionItem(
        category=FileCategory.DUPLICATE, path=duplicate.path, size=4,
        reason="Doublon", action=ActionKind.TRASH,
    ))
    result = build_analysis_result(scan, plan)
    captured = []

    def fake_execute(
        items,
        *,
        root,
        db_path=None,
        dependent_trash_keeper_guards=None,
    ):
        captured.extend(items)
        return service.ExecuteResult(batch_id=1, success=len(items), errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)
    execute_selected_actions(
        result,
        {("duplicate", 0)},
        duplicate_keepers={"samehash": duplicate.path},
    )

    assert [(item.path, item.action) for item in captured] == [
        (original.path, ActionKind.TRASH)
    ]


def test_duplicate_resolution_keeps_only_keeper_sort_and_trashes_other_members(
    monkeypatch, tmp_path: Path
) -> None:
    from datetime import datetime, timezone
    from file_janitor.application import execute_selected_actions
    from file_janitor.application import service
    from file_janitor.models import FileRecord, Plan, ScanResult

    records = [
        FileRecord(
            path=tmp_path / name, size=4,
            mtime=datetime(2024, 1, day, tzinfo=timezone.utc), extension=".txt", hash="group3",
        )
        for day, name in enumerate(("a.txt", "b.txt", "c.txt"), start=1)
    ]
    for record in records:
        record.path.write_text("same")

    scan = ScanResult(root=tmp_path, files=records)
    plan = Plan(scan=scan)
    for record in records:
        plan.add(ActionItem(
            category=FileCategory.TO_SORT, path=record.path, size=4,
            reason="Classer", destination=tmp_path / "Sorted" / record.path.name,
            action=ActionKind.MOVE,
        ))
    for record in records[1:]:
        plan.add(ActionItem(
            category=FileCategory.DUPLICATE, path=record.path, size=4,
            reason="Doublon", action=ActionKind.TRASH,
        ))
    result = build_analysis_result(scan, plan)
    captured = []
    captured_dependencies = {}
    captured_guards = {}

    def fake_execute(
        items,
        *,
        root,
        db_path=None,
        dependent_trash_keepers=None,
        dependent_trash_keeper_guards=None,
    ):
        captured.extend(items)
        captured_dependencies.update(dependent_trash_keepers or {})
        captured_guards.update(dependent_trash_keeper_guards or {})
        return service.ExecuteResult(batch_id=2, success=len(items), errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)
    execute_selected_actions(
        result,
        {("duplicate", 0), ("to_sort", 0), ("to_sort", 1), ("to_sort", 2)},
        duplicate_keepers={"group3": records[1].path},
    )

    assert [(item.path, item.action) for item in captured] == [
        (records[1].path, ActionKind.MOVE),
        (records[0].path, ActionKind.TRASH),
        (records[2].path, ActionKind.TRASH),
    ]
    assert captured_dependencies == {
        records[0].path: records[1].path,
        records[2].path: records[1].path,
    }
    assert set(captured_guards) == {records[0].path, records[2].path}
    assert all(
        guard.keeper_path == records[1].path
        for guard in captured_guards.values()
    )




def test_duplicate_execution_builds_live_guard_for_keeper_without_sort_action(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from datetime import datetime
    from file_janitor.application import execute_selected_actions
    from file_janitor.application import service
    from file_janitor.models import FileRecord, Plan, ScanResult
    from file_janitor.path_safety import current_file_identity

    keeper = tmp_path / "keeper.txt"
    duplicate = tmp_path / "duplicate.txt"
    keeper.write_text("same")
    duplicate.write_text("same")
    now = datetime.now()
    keeper_record = FileRecord(
        path=keeper, size=4, mtime=now, extension=".txt", hash="samehash",
        identity=current_file_identity(keeper),
    )
    duplicate_record = FileRecord(
        path=duplicate, size=4, mtime=now, extension=".txt", hash="samehash",
        identity=current_file_identity(duplicate),
    )
    scan = ScanResult(root=tmp_path, files=[keeper_record, duplicate_record])
    plan = Plan(scan=scan)
    plan.add(ActionItem(
        category=FileCategory.DUPLICATE,
        path=duplicate,
        size=4,
        reason="Doublon",
        identity=duplicate_record.identity,
        action=ActionKind.TRASH,
    ))
    result = build_analysis_result(scan, plan)
    captured_guards = {}

    def fake_execute(items, *, root, dependent_trash_keeper_guards=None, **kwargs):
        captured_guards.update(dependent_trash_keeper_guards or {})
        return service.ExecuteResult(batch_id=1, success=len(items), errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)
    execute_selected_actions(
        result,
        {("duplicate", 0)},
        duplicate_keepers={"samehash": keeper},
    )

    assert set(captured_guards) == {duplicate}
    guard = captured_guards[duplicate]
    assert guard.keeper_path == keeper
    assert guard.identity == keeper_record.identity


def test_duplicate_preflight_rejects_modified_keeper_before_any_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from datetime import datetime
    from file_janitor.application import execute_selected_actions
    from file_janitor.application import service
    from file_janitor.models import FileRecord, Plan, ScanResult
    from file_janitor.path_safety import current_file_identity

    keeper = tmp_path / "keeper.txt"
    duplicate = tmp_path / "duplicate.txt"
    keeper.write_text("same")
    duplicate.write_text("same")
    now = datetime.now()

    keeper_record = FileRecord(
        path=keeper,
        size=keeper.stat().st_size,
        mtime=now,
        extension=".txt",
        hash="samehash",
        identity=current_file_identity(keeper),
    )
    duplicate_record = FileRecord(
        path=duplicate,
        size=duplicate.stat().st_size,
        mtime=now,
        extension=".txt",
        hash="samehash",
        identity=current_file_identity(duplicate),
    )
    scan = ScanResult(root=tmp_path, files=[keeper_record, duplicate_record])
    plan = Plan(scan=scan)
    plan.add(
        ActionItem(
            category=FileCategory.DUPLICATE,
            path=duplicate,
            size=duplicate_record.size,
            reason="Doublon",
            identity=duplicate_record.identity,
            action=ActionKind.TRASH,
        )
    )
    result = build_analysis_result(scan, plan)

    keeper.write_text("changed-after-preview")
    called = False

    def fake_execute(items, *, root, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("aucune mutation ne doit démarrer")

    monkeypatch.setattr(service, "execute_actions", fake_execute)

    execution = execute_selected_actions(
        result,
        {("duplicate", 0)},
        duplicate_keepers={"samehash": keeper},
    )

    assert called is False
    assert execution.batch_id is None
    assert execution.success == 0
    assert execution.skipped == 1
    assert len(execution.errors) == 1
    assert "préflight refusé" in execution.errors[0]
    assert "keeper" in execution.errors[0]
    assert keeper.exists()
    assert duplicate.exists()


def test_duplicate_preflight_rejects_missing_non_keeper_before_any_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from datetime import datetime
    from file_janitor.application import execute_selected_actions
    from file_janitor.application import service
    from file_janitor.models import FileRecord, Plan, ScanResult
    from file_janitor.path_safety import current_file_identity

    keeper = tmp_path / "keeper.txt"
    duplicate = tmp_path / "duplicate.txt"
    keeper.write_text("same")
    duplicate.write_text("same")
    now = datetime.now()

    keeper_record = FileRecord(
        path=keeper,
        size=keeper.stat().st_size,
        mtime=now,
        extension=".txt",
        hash="samehash",
        identity=current_file_identity(keeper),
    )
    duplicate_record = FileRecord(
        path=duplicate,
        size=duplicate.stat().st_size,
        mtime=now,
        extension=".txt",
        hash="samehash",
        identity=current_file_identity(duplicate),
    )
    scan = ScanResult(root=tmp_path, files=[keeper_record, duplicate_record])
    plan = Plan(scan=scan)
    plan.add(
        ActionItem(
            category=FileCategory.DUPLICATE,
            path=duplicate,
            size=duplicate_record.size,
            reason="Doublon",
            identity=duplicate_record.identity,
            action=ActionKind.TRASH,
        )
    )
    result = build_analysis_result(scan, plan)

    duplicate.unlink()
    called = False

    def fake_execute(items, *, root, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("aucune mutation ne doit démarrer")

    monkeypatch.setattr(service, "execute_actions", fake_execute)

    execution = execute_selected_actions(
        result,
        {("duplicate", 0)},
        duplicate_keepers={"samehash": keeper},
    )

    assert called is False
    assert execution.batch_id is None
    assert execution.success == 0
    assert execution.skipped == 1
    assert len(execution.errors) == 1
    assert "préflight refusé" in execution.errors[0]
    assert "membre" in execution.errors[0]
    assert keeper.exists()


def test_duplicate_preflight_rejects_occupied_keeper_destination(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from datetime import datetime
    from file_janitor.application import execute_selected_actions
    from file_janitor.application import service
    from file_janitor.models import FileRecord, Plan, ScanResult
    from file_janitor.path_safety import current_file_identity

    keeper = tmp_path / "keeper.txt"
    duplicate = tmp_path / "duplicate.txt"
    keeper.write_text("same")
    duplicate.write_text("same")
    now = datetime.now()

    keeper_record = FileRecord(
        path=keeper,
        size=keeper.stat().st_size,
        mtime=now,
        extension=".txt",
        hash="samehash",
        identity=current_file_identity(keeper),
    )
    duplicate_record = FileRecord(
        path=duplicate,
        size=duplicate.stat().st_size,
        mtime=now,
        extension=".txt",
        hash="samehash",
        identity=current_file_identity(duplicate),
    )
    scan = ScanResult(root=tmp_path, files=[keeper_record, duplicate_record])
    plan = Plan(scan=scan)

    destination = tmp_path / "Sorted" / keeper.name
    plan.add(
        ActionItem(
            category=FileCategory.TO_SORT,
            path=keeper,
            size=keeper_record.size,
            reason="Classer le keeper",
            identity=keeper_record.identity,
            destination=destination,
            action=ActionKind.MOVE,
        )
    )
    plan.add(
        ActionItem(
            category=FileCategory.DUPLICATE,
            path=duplicate,
            size=duplicate_record.size,
            reason="Doublon",
            identity=duplicate_record.identity,
            action=ActionKind.TRASH,
        )
    )
    result = build_analysis_result(scan, plan)

    destination.parent.mkdir()
    destination.write_text("external")
    called = False

    def fake_execute(items, *, root, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("aucune mutation ne doit démarrer")

    monkeypatch.setattr(service, "execute_actions", fake_execute)

    execution = execute_selected_actions(
        result,
        {("duplicate", 0)},
        duplicate_keepers={"samehash": keeper},
    )

    assert called is False
    assert execution.batch_id is None
    assert execution.success == 0
    assert execution.skipped == 2
    assert len(execution.errors) == 1
    assert "destination du keeper" in execution.errors[0]
    assert keeper.read_text() == "same"
    assert duplicate.read_text() == "same"
    assert destination.read_text() == "external"


def test_duplicate_resolution_requires_one_valid_keeper_per_selected_group(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from file_janitor.application import execute_selected_actions
    from file_janitor.models import FileRecord, Plan, ScanResult

    records = [
        FileRecord(
            path=tmp_path / name, size=1,
            mtime=datetime(2024, 1, day, tzinfo=timezone.utc), extension=".txt", hash="group",
        )
        for day, name in enumerate(("a.txt", "b.txt"), start=1)
    ]
    scan = ScanResult(root=tmp_path, files=records)
    plan = Plan(scan=scan)
    plan.add(ActionItem(
        category=FileCategory.DUPLICATE, path=records[1].path, size=1,
        reason="Doublon", action=ActionKind.TRASH,
    ))
    result = build_analysis_result(scan, plan)

    with pytest.raises(ValueError, match="résolution interactive"):
        execute_selected_actions(result, {("duplicate", 0)})
    with pytest.raises(ValueError, match="invalide"):
        execute_selected_actions(
            result,
            {("duplicate", 0)},
            duplicate_keepers={"group": tmp_path / "other.txt"},
        )


def test_remote_metadata_light_execution_hydrates_identity_before_execute(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import service
    from file_janitor.models import ActionItem, ActionKind, FileCategory, Plan, ScanResult

    source = tmp_path / "source.txt"
    source.write_text("payload")
    scan = ScanResult(root=tmp_path, metadata_complete=False)
    action = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=0,
        reason="Classer",
        destination=tmp_path / "Sorted" / source.name,
        action=ActionKind.MOVE,
    )
    plan = Plan(scan=scan)
    plan.add(action)
    result = service.build_analysis_result(
        scan, plan, hashes_computed=False, content_types_computed=False,
        metadata_computed=False,
    )
    captured = []

    def fake_execute(items, *, root, db_path=None):
        captured.extend(items)
        return service.ExecuteResult(batch_id=1, success=1, errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)
    service.execute_selected_actions(result, {("to_sort", 0)})

    assert len(captured) == 1
    assert captured[0].size == len("payload")
    assert captured[0].identity is not None


def test_empty_files_are_not_exposed_as_duplicate_groups(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from file_janitor.models import FileRecord, Plan, ScanResult

    empty_hash = "e3b0c44298fc1c149afbf4c8996fb924"
    first = FileRecord(
        path=tmp_path / "__init__.py", size=0,
        mtime=datetime(2024, 1, 1, tzinfo=timezone.utc),
        extension=".py", hash=empty_hash,
    )
    second = FileRecord(
        path=tmp_path / "py.typed", size=0,
        mtime=datetime(2024, 1, 2, tzinfo=timezone.utc),
        extension=".typed", hash=empty_hash,
    )

    result = build_analysis_result(
        ScanResult(root=tmp_path, files=[first, second]),
        Plan(scan=ScanResult(root=tmp_path, files=[first, second])),
    )

    assert result.duplicate_groups == ()


def test_execute_selected_actions_forwards_progress_callback(monkeypatch, tmp_path: Path) -> None:
    from file_janitor.application import service
    from file_janitor.models import ActionItem, ActionKind, FileCategory, Plan, ScanResult

    source = tmp_path / "source.txt"
    source.write_text("payload")
    scan = ScanResult(root=tmp_path)
    action = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=7,
        reason="Classer",
        destination=tmp_path / "txt" / source.name,
        action=ActionKind.MOVE,
    )
    plan = Plan(scan=scan)
    plan.add(action)
    result = service.build_analysis_result(scan, plan)
    callback = object()
    captured = {}

    def fake_execute(items, *, root, db_path=None, progress_callback=None):
        captured["callback"] = progress_callback
        return service.ExecuteResult(batch_id=1, success=1, errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)
    service.execute_selected_actions(
        result,
        {("to_sort", 0)},
        progress_callback=callback,
    )
    assert captured["callback"] is callback


def test_scan_folder_prefers_rclone_metadata_fast_path(monkeypatch, tmp_path: Path) -> None:
    from file_janitor.application import service
    from file_janitor.models import ScanResult

    expected = ScanResult(root=tmp_path)
    monkeypatch.setattr(service, "scan_rclone_metadata", lambda *args, **kwargs: expected)
    monkeypatch.setattr(
        service,
        "scan_directory",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("FUSE scan fallback must not run")
        ),
    )

    actual = service.scan_folder(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
        collect_metadata=True,
    )
    assert actual is expected


def test_scan_folder_falls_back_when_rclone_metadata_is_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import service
    from file_janitor.models import ScanResult

    expected = ScanResult(root=tmp_path)
    monkeypatch.setattr(service, "scan_rclone_metadata", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "scan_directory", lambda *args, **kwargs: expected)

    actual = service.scan_folder(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
        collect_metadata=True,
    )
    assert actual is expected


def test_rclone_metadata_result_hydrates_missing_identity_before_execution(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import service
    from file_janitor.models import (
        ActionItem,
        ActionKind,
        FileCategory,
        FileIdentity,
        Plan,
        ScanResult,
    )

    source = tmp_path / "photo.jpg"
    scan = ScanResult(
        root=tmp_path,
        metadata_complete=True,
        identities_complete=False,
    )
    action = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=123,
        reason="Classer",
        destination=tmp_path / "2026-09" / source.name,
        action=ActionKind.MOVE,
    )
    plan = Plan(scan=scan)
    plan.add(action)
    result = service.build_analysis_result(
        scan,
        plan,
        identities_computed=False,
    )
    identity = FileIdentity(device=1, inode=2, size=124, mtime_ns=3)
    monkeypatch.setattr(service, "current_file_identity", lambda path: identity)
    captured = {}

    def fake_execute(items, *, root, db_path=None, progress_callback=None):
        captured["item"] = items[0]
        return service.ExecuteResult(batch_id=1, success=1, errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)
    service.execute_selected_actions(result, {("to_sort", 0)})

    assert captured["item"].identity == identity
    assert captured["item"].size == 124


def _remote_classification_result(
    tmp_path: Path,
    *,
    mode: ClassificationMode,
    source_name: str,
    size: int,
    mtime: datetime,
):
    from file_janitor.application import service
    from file_janitor.models import FileRecord, ScanResult

    source = tmp_path / source_name
    scan = ScanResult(
        root=tmp_path,
        files=[
            FileRecord(
                path=source,
                size=size,
                mtime=mtime,
                extension=source.suffix.lower(),
                identity=None,
            )
        ],
        metadata_complete=True,
        identities_complete=False,
    )
    plan = service.create_plan(
        scan,
        classification_plan_config(mode),
    )
    return service.build_analysis_result(
        scan,
        plan,
        identities_computed=False,
    )


def test_remote_size_execution_rejects_file_that_crossed_bucket(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import service
    from file_janitor.models import FileIdentity

    result = _remote_classification_result(
        tmp_path,
        mode=ClassificationMode.SIZE,
        source_name="payload.bin",
        size=1,
        mtime=datetime(2026, 9, 7, 10, 0),
    )
    monkeypatch.setattr(
        service,
        "current_file_identity",
        lambda path: FileIdentity(
            device=1,
            inode=2,
            size=20 * 1024,
            mtime_ns=int(datetime(2026, 9, 7, 10, 0).timestamp() * 1_000_000_000),
        ),
    )
    monkeypatch.setattr(
        service,
        "execute_actions",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("stale classification must not execute")
        ),
    )

    with pytest.raises(ValueError, match="classe prévue.*classe actuelle"):
        service.execute_selected_actions(result, {("to_sort", 0)})


def test_remote_date_execution_rejects_file_that_crossed_month(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import service
    from file_janitor.models import FileIdentity

    result = _remote_classification_result(
        tmp_path,
        mode=ClassificationMode.DATE,
        source_name="photo.jpg",
        size=100,
        mtime=datetime(2026, 8, 31, 12, 0),
    )
    current_mtime = datetime(2026, 9, 1, 12, 0)
    monkeypatch.setattr(
        service,
        "current_file_identity",
        lambda path: FileIdentity(
            device=1,
            inode=2,
            size=100,
            mtime_ns=int(current_mtime.timestamp() * 1_000_000_000),
        ),
    )
    monkeypatch.setattr(
        service,
        "execute_actions",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("stale classification must not execute")
        ),
    )

    with pytest.raises(ValueError, match="2026-08.*2026-09"):
        service.execute_selected_actions(result, {("to_sort", 0)})


def test_remote_combined_date_size_rejects_changed_secondary_bucket(
    monkeypatch, tmp_path: Path,
) -> None:
    from file_janitor.application import service
    from file_janitor.models import FileIdentity, FileRecord, ScanResult

    timestamp = datetime(2026, 8, 10, 12)
    source = tmp_path / "clip.mp4"
    scan = ScanResult(
        root=tmp_path,
        files=[FileRecord(path=source, size=1, mtime=timestamp, extension=".mp4", identity=None)],
        metadata_complete=True,
        identities_complete=False,
    )
    plan = service.create_plan(scan, classification_plan_config(
        ClassificationMode.DATE, secondary_mode=ClassificationMode.SIZE,
    ))
    result = service.build_analysis_result(scan, plan, identities_computed=False)
    monkeypatch.setattr(service, "current_file_identity", lambda path: FileIdentity(
        device=1, inode=2, size=20 * 1024,
        mtime_ns=int(timestamp.timestamp() * 1_000_000_000),
    ))
    monkeypatch.setattr(service, "execute_actions", lambda *args, **kwargs: (
        _ for _ in ()
    ).throw(AssertionError("stale classification must not execute")))
    with pytest.raises(ValueError, match="moins_de_10_Ko.*10_a_50_Ko"):
        service.execute_selected_actions(result, {("to_sort", 0)})


def test_remote_size_execution_allows_change_within_same_bucket(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import service
    from file_janitor.models import FileIdentity

    result = _remote_classification_result(
        tmp_path,
        mode=ClassificationMode.SIZE,
        source_name="payload.bin",
        size=1,
        mtime=datetime(2026, 9, 7, 10, 0),
    )
    identity = FileIdentity(
        device=1,
        inode=2,
        size=9 * 1024,
        mtime_ns=int(datetime(2026, 9, 7, 10, 0).timestamp() * 1_000_000_000),
    )
    monkeypatch.setattr(service, "current_file_identity", lambda path: identity)
    captured = {}

    def fake_execute(items, *, root, db_path=None, progress_callback=None):
        captured["item"] = items[0]
        return service.ExecuteResult(batch_id=1, success=1, errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)
    service.execute_selected_actions(result, {("to_sort", 0)})

    assert captured["item"].identity == identity
    assert captured["item"].size == 9 * 1024


def test_remote_date_execution_allows_change_within_same_month(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import service
    from file_janitor.models import FileIdentity

    result = _remote_classification_result(
        tmp_path,
        mode=ClassificationMode.DATE,
        source_name="photo.jpg",
        size=100,
        mtime=datetime(2026, 9, 1, 8, 0),
    )
    current_mtime = datetime(2026, 9, 29, 18, 0)
    identity = FileIdentity(
        device=1,
        inode=2,
        size=101,
        mtime_ns=int(current_mtime.timestamp() * 1_000_000_000),
    )
    monkeypatch.setattr(service, "current_file_identity", lambda path: identity)
    captured = {}

    def fake_execute(items, *, root, db_path=None, progress_callback=None):
        captured["item"] = items[0]
        return service.ExecuteResult(batch_id=1, success=1, errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)
    service.execute_selected_actions(result, {("to_sort", 0)})

    assert captured["item"].identity == identity
    assert captured["item"].size == 101


def test_execute_actions_reports_cancelled_and_skipped(monkeypatch, tmp_path: Path) -> None:
    from file_janitor.application import service
    from file_janitor.storage.history import BatchStatus

    source1 = tmp_path / "a.txt"
    source2 = tmp_path / "b.txt"
    source1.write_text("a")
    source2.write_text("b")
    items = [
        ActionItem(
            category=FileCategory.TO_SORT,
            path=source1,
            size=1,
            reason="sort",
            destination=tmp_path / "out" / source1.name,
            action=ActionKind.MOVE,
        ),
        ActionItem(
            category=FileCategory.TO_SORT,
            path=source2,
            size=1,
            reason="sort",
            destination=tmp_path / "out" / source2.name,
            action=ActionKind.MOVE,
        ),
    ]
    checks = iter((False, True))

    result = service.execute_actions(
        items,
        root=str(tmp_path),
        db_path=tmp_path / "history.db",
        cancel_callback=lambda: next(checks),
    )

    assert result.cancelled is True
    assert result.success == 1
    assert result.skipped == 1
    assert result.errors == ()
    assert result.batch_id is not None


def test_execute_selected_actions_can_cancel_before_remote_identity_hydration(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import service
    from file_janitor.models import Plan, ScanResult

    source = tmp_path / "remote.txt"
    scan = ScanResult(
        root=tmp_path,
        metadata_complete=True,
        identities_complete=False,
    )
    action = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=12,
        reason="sort",
        destination=tmp_path / "txt" / source.name,
        action=ActionKind.MOVE,
    )
    plan = Plan(scan=scan)
    plan.add(action)
    result = service.build_analysis_result(
        scan, plan, identities_computed=False
    )
    monkeypatch.setattr(
        service,
        "current_file_identity",
        lambda path: (_ for _ in ()).throw(AssertionError("must not stat")),
    )
    monkeypatch.setattr(
        service,
        "execute_actions",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("must not execute")
        ),
    )

    cancelled = service.execute_selected_actions(
        result,
        {("to_sort", 0)},
        cancel_callback=lambda: True,
    )

    assert cancelled.cancelled is True
    assert cancelled.batch_id is None
    assert cancelled.success == 0
    assert cancelled.skipped == 1


def test_analysis_result_exposes_preplanned_batch_collision(tmp_path: Path) -> None:
    from file_janitor.application import service
    from file_janitor.models import (
        ActionItem,
        ActionKind,
        FileCategory,
        Plan,
        ScanResult,
    )

    scan = ScanResult(root=tmp_path)
    plan = Plan(scan=scan)
    plan.add(
        ActionItem(
            category=FileCategory.TO_SORT,
            path=tmp_path / "a.pdf",
            size=1,
            reason="Classer",
            destination=tmp_path / "pdf" / "a (1).pdf",
            action=ActionKind.MOVE,
            conflict=True,
            conflict_reason="Collision interne au lot : destination renommée.",
        )
    )

    result = service.build_analysis_result(scan, plan)
    item = result.details_for("to_sort").items[0]
    assert item.conflict is True
    assert item.conflict_reason == (
        "Collision interne au lot : destination renommée."
    )

def test_startup_crash_recovery_facade_reconciles_persistent_history(
    tmp_path: Path,
) -> None:
    from file_janitor.application import run_startup_crash_recovery
    from file_janitor.path_safety import current_file_identity
    from file_janitor.storage.history import (
    PlannedRecoveryState,
        BatchStatus,
        HistoryStore,
        OperationStatus,
    )

    db = tmp_path / "history.db"
    destination = tmp_path / "sorted" / "a.txt"
    destination.parent.mkdir()
    destination.write_text("published", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="copy",
        original_path=tmp_path / "a.txt",
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.set_operation_stored_identity(
        operation_id,
        current_file_identity(destination),
    )
    store.close()

    report = run_startup_crash_recovery(db_path=db)

    assert report.promoted_operation_ids == (operation_id,)

    reopened = HistoryStore(db_path=db)
    operation = reopened.get_operations(batch_id)[0]
    batch = reopened.get_batch(batch_id)
    assert operation.status is OperationStatus.COMPLETED
    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED
    reopened.close()

def test_history_operation_summary_exposes_unverified_recovery_state(
    tmp_path: Path,
) -> None:
    from file_janitor.application import (
    PlannedRecoveryState,
        get_history_operation_summary,
        run_startup_crash_recovery,
    )
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    destination.parent.mkdir()
    destination.write_text("published", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    run_startup_crash_recovery(db_path=db)
    summary = get_history_operation_summary(batch_id, db_path=db)

    assert len(summary) == 1
    assert summary[0].status == "planned"
    assert summary[0].recovery_state is PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED
    assert summary[0].recovery_attention_required is True
    assert summary[0].error is not None


def test_history_operation_summary_exposes_ambiguous_recovery_state(
    tmp_path: Path,
) -> None:
    from file_janitor.application import (
    PlannedRecoveryState,
        get_history_operation_summary,
        run_startup_crash_recovery,
    )
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination.parent.mkdir()
    destination.write_text("destination", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    run_startup_crash_recovery(db_path=db)
    summary = get_history_operation_summary(batch_id, db_path=db)

    assert len(summary) == 1
    assert summary[0].status == "planned"
    assert summary[0].recovery_state is PlannedRecoveryState.AMBIGUOUS
    assert summary[0].recovery_attention_required is True


def test_history_operation_summary_marks_resolved_no_publication_without_attention(
    tmp_path: Path,
) -> None:
    from file_janitor.application import (
    PlannedRecoveryState,
        get_history_operation_summary,
        run_startup_crash_recovery,
    )
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    run_startup_crash_recovery(db_path=db)
    summary = get_history_operation_summary(batch_id, db_path=db)

    assert len(summary) == 1
    assert summary[0].status == "failed"
    assert summary[0].recovery_state is PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED
    assert summary[0].recovery_attention_required is False


def test_history_operation_summary_has_no_recovery_state_for_normal_operation(
    tmp_path: Path,
) -> None:
    from file_janitor.application import get_history_operation_summary
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=123,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )
    store.close()

    summary = get_history_operation_summary(batch_id, db_path=db)

    assert len(summary) == 1
    assert summary[0].recovery_state is None
    assert summary[0].recovery_attention_required is False


def test_application_can_close_unresolved_recovery_without_file_action(
    tmp_path: Path,
) -> None:
    from file_janitor.application import (
    PlannedRecoveryState,
        get_history_operation_summary,
        resolve_history_recovery_without_file_action,
    )
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination.parent.mkdir()
    destination.write_text("destination", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
        error=(
            "récupération après crash : état filesystem ambigu ; "
            "opération laissée PLANNED, aucune action automatique"
        ),
    )
    store.close()

    assert resolve_history_recovery_without_file_action(
        batch_id, db_path=db
    ) == 1

    summary = get_history_operation_summary(batch_id, db_path=db)
    assert len(summary) == 1
    assert summary[0].status == "failed"
    assert summary[0].recovery_state is PlannedRecoveryState.AMBIGUOUS
    assert summary[0].recovery_attention_required is False
    assert "résolution manuelle après crash" in (summary[0].error or "")
    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "destination"

def test_history_operation_summary_prefers_structured_recovery_state(
    tmp_path: Path,
) -> None:
    from file_janitor.application import get_history_operation_summary
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("destination", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
        error="diagnostic non reconnu volontairement",
    )
    store.set_operation_recovery_audit(
        operation_id,
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
    )
    store.close()

    summary = get_history_operation_summary(batch_id, db_path=db)
    assert summary[0].recovery_state is PlannedRecoveryState.AMBIGUOUS
    assert summary[0].recovery_attention_required is True


def test_history_operation_summary_keeps_legacy_error_fallback(
    tmp_path: Path,
) -> None:
    from file_janitor.application import get_history_operation_summary
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    destination = tmp_path / "destination.txt"
    destination.write_text("destination", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "missing.txt",
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
        error=(
            "récupération après crash : objet publié observé mais identité non "
            "vérifiable ; opération laissée PLANNED, aucune action automatique"
        ),
    )
    store.close()

    summary = get_history_operation_summary(batch_id, db_path=db)
    assert summary[0].recovery_state is PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED
    assert summary[0].recovery_attention_required is True

def test_history_operation_summary_exposes_resolution_audit_metadata(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    from file_janitor.application import get_history_operation_summary
    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        RecoveryResolutionKind,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.FAILED,
        error="resolved",
    )
    resolved_at = datetime(2026, 9, 13, 11, 0, tzinfo=timezone.utc)
    store.set_operation_recovery_audit(
        operation_id,
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
        resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
        resolved_at=resolved_at,
    )
    store.close()

    summary = get_history_operation_summary(batch_id, db_path=db)
    assert summary[0].resolution_kind is RecoveryResolutionKind.MANUAL_NO_FILE_ACTION
    assert summary[0].resolved_at == resolved_at
    assert summary[0].recovery_attention_required is False

def test_history_operation_summary_marks_unknown_recovery_state_for_attention(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.application import (
        PlannedRecoveryState,
        get_history_operation_summary,
    )
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE operations SET recovery_state = ? WHERE id = ?",
        ("future_recovery_state", operation_id),
    )
    conn.commit()
    conn.close()

    summary = get_history_operation_summary(batch_id, db_path=db)

    assert len(summary) == 1
    assert summary[0].status == "planned"
    assert summary[0].recovery_state is PlannedRecoveryState.UNKNOWN
    assert summary[0].recovery_attention_required is True

def test_history_operation_summary_exposes_raw_unknown_recovery_metadata(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.application import (
        PlannedRecoveryState,
        RecoveryResolutionKind,
        get_history_operation_summary,
    )
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE operations
        SET recovery_state = ?,
            resolution_kind = ?
        WHERE id = ?
        """,
        ("future_recovery_state", "future_resolution_kind", operation_id),
    )
    conn.commit()
    conn.close()

    summary = get_history_operation_summary(batch_id, db_path=db)

    assert len(summary) == 1
    assert summary[0].recovery_state is PlannedRecoveryState.UNKNOWN
    assert summary[0].recovery_state_raw == "future_recovery_state"
    assert summary[0].resolution_kind is RecoveryResolutionKind.UNKNOWN
    assert summary[0].resolution_kind_raw == "future_resolution_kind"
    assert summary[0].recovery_attention_required is True

def test_history_operation_summary_exposes_raw_malformed_resolved_at(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.application import get_history_operation_summary
    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        PlannedRecoveryState,
        RecoveryResolutionKind,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.FAILED,
        error="resolved",
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE operations
        SET recovery_state = ?,
            resolution_kind = ?,
            resolved_at = ?
        WHERE id = ?
        """,
        (
            PlannedRecoveryState.AMBIGUOUS.value,
            RecoveryResolutionKind.MANUAL_NO_FILE_ACTION.value,
            "not-a-valid-timestamp",
            operation_id,
        ),
    )
    conn.commit()
    conn.close()

    summary = get_history_operation_summary(batch_id, db_path=db)

    assert len(summary) == 1
    assert summary[0].resolved_at is None
    assert summary[0].resolved_at_raw == "not-a-valid-timestamp"

def test_history_operation_summary_flags_inconsistent_recovery_audit_pairs(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.application import get_history_operation_summary
    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        RecoveryResolutionKind,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=2)
    first_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source-a.txt",
        stored_path=tmp_path / "destination-a.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.FAILED,
    )
    second_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source-b.txt",
        stored_path=tmp_path / "destination-b.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.FAILED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE operations SET resolution_kind = ? WHERE id = ?",
        (RecoveryResolutionKind.MANUAL_NO_FILE_ACTION.value, first_id),
    )
    conn.execute(
        "UPDATE operations SET resolved_at = ? WHERE id = ?",
        ("2026-09-13T12:00:00+00:00", second_id),
    )
    conn.commit()
    conn.close()

    summary = get_history_operation_summary(batch_id, db_path=db)

    assert summary[0].recovery_audit_issue == "resolution_kind_without_resolved_at"
    assert summary[1].recovery_audit_issue == "resolved_at_without_resolution_kind"


def test_history_operation_summary_flags_resolution_state_mismatch(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.application import get_history_operation_summary
    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        PlannedRecoveryState,
        RecoveryResolutionKind,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.FAILED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE operations
        SET recovery_state = ?,
            resolution_kind = ?,
            resolved_at = ?
        WHERE id = ?
        """,
        (
            PlannedRecoveryState.AMBIGUOUS.value,
            RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION.value,
            "2026-09-13T12:00:00+00:00",
            operation_id,
        ),
    )
    conn.commit()
    conn.close()

    summary = get_history_operation_summary(batch_id, db_path=db)
    assert summary[0].recovery_audit_issue == "resolution_kind_state_mismatch"

def test_manual_recovery_closure_refuses_inconsistent_audit(
    tmp_path: Path,
) -> None:
    import sqlite3

    import pytest

    from file_janitor.application import (
        resolve_history_recovery_without_file_action,
    )
    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        PlannedRecoveryState,
        RecoveryResolutionKind,
    )

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("destination", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE operations
        SET recovery_state = ?,
            resolution_kind = ?
        WHERE id = ?
        """,
        (
            PlannedRecoveryState.AMBIGUOUS.value,
            RecoveryResolutionKind.MANUAL_NO_FILE_ACTION.value,
            operation_id,
        ),
    )
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="audit recovery incohérent"):
        resolve_history_recovery_without_file_action(batch_id, db_path=db)

    reopened = HistoryStore(db_path=db)
    operation = reopened.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.PLANNED
    assert operation.recovery_state is PlannedRecoveryState.AMBIGUOUS
    assert operation.resolution_kind is RecoveryResolutionKind.MANUAL_NO_FILE_ACTION
    assert operation.resolved_at is None
    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "destination"
    reopened.close()

def test_history_summary_preserves_raw_invalid_core_metadata(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.application import (
        get_history_operation_summary,
        get_history_summary,
    )
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE batches SET created_at = ?, status = ? WHERE id = ?",
        ("bad-created-at", "future_batch_status", batch_id),
    )
    conn.execute(
        "UPDATE operations SET status = ? WHERE id = ?",
        ("future_operation_status", operation_id),
    )
    conn.commit()
    conn.close()

    batch_summary = get_history_summary(db_path=db)[0]
    operation_summary = get_history_operation_summary(batch_id, db_path=db)[0]

    assert batch_summary.created_at == "Horodatage invalide"
    assert batch_summary.created_at_raw == "bad-created-at"
    assert batch_summary.status == "unknown"
    assert batch_summary.status_raw == "future_batch_status"

    assert operation_summary.status == "unknown"
    assert operation_summary.status_raw == "future_operation_status"

def test_unknown_operation_status_is_not_manual_recovery_attention(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.application import get_history_operation_summary
    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        PlannedRecoveryState,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE operations
        SET status = ?,
            recovery_state = ?
        WHERE id = ?
        """,
        (
            "future_operation_status",
            PlannedRecoveryState.AMBIGUOUS.value,
            operation_id,
        ),
    )
    conn.commit()
    conn.close()

    summary = get_history_operation_summary(batch_id, db_path=db)

    assert len(summary) == 1
    assert summary[0].status == "unknown"
    assert summary[0].status_raw == "future_operation_status"
    assert summary[0].recovery_state is PlannedRecoveryState.AMBIGUOUS
    assert summary[0].recovery_attention_required is False

def test_history_summaries_classify_core_metadata_issues(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.application import (
        get_history_operation_summary,
        get_history_summary,
    )
    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE batches SET created_at = ?, status = ? WHERE id = ?",
        ("bad-created-at", "future_batch_status", batch_id),
    )
    conn.execute(
        "UPDATE operations SET status = ? WHERE id = ?",
        ("future_operation_status", operation_id),
    )
    conn.commit()
    conn.close()

    batch_summary = get_history_summary(db_path=db)[0]
    operation_summary = get_history_operation_summary(batch_id, db_path=db)[0]

    assert batch_summary.core_metadata_issues == (
        "invalid_created_at",
        "unknown_status",
    )
    assert operation_summary.core_metadata_issues == ("unknown_status",)


def test_history_stored_identity_issue_classifier_distinguishes_states() -> None:
    from types import SimpleNamespace

    from file_janitor.application.service import _history_stored_identity_issue

    absent = SimpleNamespace(stored_identity=None, stored_identity_raw=None)
    valid = SimpleNamespace(stored_identity=object(), stored_identity_raw=None)
    invalid = SimpleNamespace(
        stored_identity=None,
        stored_identity_raw=(123, 456, None, None),
    )

    assert _history_stored_identity_issue(absent) is None
    assert _history_stored_identity_issue(valid) is None
    assert (
        _history_stored_identity_issue(invalid)
        == "invalid_stored_identity"
    )
