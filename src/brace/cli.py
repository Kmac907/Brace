from __future__ import annotations

import argparse
from collections.abc import Sequence

from . import __version__, audit, bootstrap, build, planning
from .common import BraceError
from .ui import error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="brace", description="Branch-safe repository agent coordination, from plan to merge.")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = result.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="Create a new Brace project or install Brace into an existing repository.")
    bootstrap.add_arguments(initialize)

    plan = commands.add_parser("plan", help="Create the project plan and implementation task queue.")
    plan.add_argument("repository", nargs="?", default=".")
    plan.add_argument("--start-new-workflow", action="store_true")

    build_command = commands.add_parser("build", help="Build and integrate every planned task.")
    build_command.add_argument("repository", nargs="?", default=".")

    audit_command = commands.add_parser("audit", help="Audit, repair, validate, and complete the project.")
    audit_command.add_argument("repository", nargs="?", default=".")
    return result


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "init":
        bootstrap.bootstrap(args)
    elif args.command == "plan":
        planning.run(args.repository, args.start_new_workflow)
    elif args.command == "build":
        next_stage = build.run(args.repository)
        while next_stage == "build":
            next_stage = build.run(args.repository)
    else:
        next_stage = audit.run(args.repository)
        while next_stage == "build":
            next_stage = build.run(args.repository)
            if next_stage == "audit":
                next_stage = audit.run(args.repository)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _dispatch(parser().parse_args(argv))
        return 0
    except (BraceError, bootstrap.BootstrapError) as exc:
        error(str(exc))
        return 1
    except KeyboardInterrupt:
        error("Interrupted.")
        return 130
