"""mv workflow（用途別の工程案内と完了確認。docs/workflow.md）の組み立てと処理。"""
import argparse
from pathlib import Path


def _workflow_parser(commands):
    """用途別の工程案内と完了確認。既存の解析・描画を自動実行しない。"""
    workflow = commands.add_parser("workflow", help="purpose-based production plans and completion checks")
    sub = workflow.add_subparsers(dest="workflow_command", required=True)
    plan = sub.add_parser("plan", help="save the required inputs, commands and handoffs for a production")
    plan.add_argument("target", type=Path, help="MV project directory or dance spec JSON")
    plan.add_argument("--purpose", choices=["lyric-mv", "dance-edit"], required=True)
    plan.add_argument("--out", type=Path, help="workflow JSON path (default: beside the target)")
    plan.add_argument("--lyrics", action=argparse.BooleanOptionalAction, default=None,
                      help="declare sung lyrics for a dance edit (otherwise inferred from an existing spec)")
    plan.add_argument("--inspection", choices=["optional", "required"],
                      help="add the mv inspect stage (required: the plan is incomplete without a matching inspect report)")
    check = sub.add_parser("check", help="check current evidence; exit 1 for missing or failed requirements")
    check.add_argument("plan", type=Path)
    check.add_argument("--run", type=Path, help="render run directory (required for dance edits)")
    check.add_argument("--gate-config", type=Path, help="gate config for this dance run; checks are rerun")
    check.add_argument("--out", type=Path, help="write the completion report JSON and Markdown here")
    check.add_argument("--inspect-report", type=Path, action="append", default=[],
                       help="mv inspect report (inspect_report.json) to record; repeatable. Matched to the final video, spec or assets by SHA-256")
    omit = sub.add_parser("omit", help="record an intentional omission; never turns a failed check into a pass")
    omit.add_argument("plan", type=Path)
    omit.add_argument("stage", choices=["beats", "lyrics"])
    omit.add_argument("--reason", required=True)
    restore = sub.add_parser("restore", help="remove an omission and require the stage again")
    restore.add_argument("plan", type=Path)
    restore.add_argument("stage", choices=["beats", "lyrics"])


def workflow_command(args):
    """workflow サブコマンド。check で必須工程が未実施・未確認なら args.failed=1。"""
    from .. import workflow

    if args.workflow_command == "plan":
        return workflow.create_plan(args.target, args.purpose, args.out, args.lyrics, args.inspection)
    if args.workflow_command == "omit":
        return workflow.omit(args.plan, args.stage, args.reason)
    if args.workflow_command == "restore":
        return workflow.restore(args.plan, args.stage)
    report = workflow.check(args.plan, args.run, args.gate_config, args.out, args.inspect_report)
    args.failed = 0 if report["overall"] in {"complete", "complete_with_omissions"} else 1
    return report
