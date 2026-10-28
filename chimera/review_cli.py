import argparse, os
from pathlib import Path
from .review import ReviewService

def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m chimera.review_cli"); parser.add_argument("operation", choices=["list","inspect","verify","behavior-verify","approve-execution","approve-acceptance","accept","reject","history"]); parser.add_argument("candidate_id", nargs="?"); parser.add_argument("--reason", default="")
    args = parser.parse_args(argv); service = ReviewService(Path(os.environ.get("CHIMERA_DATA_DIR", ".chimera")))
    try:
        if args.operation == "list":
            for c in service.list(): print(c.candidate_id, c.status, c.name)
        elif args.operation == "inspect": print(service.get(args.candidate_id))
        elif args.operation == "verify": print(service.verify(args.candidate_id))
        elif args.operation == "behavior-verify": print(service.behavior_verify(args.candidate_id))
        elif args.operation == "approve-execution": print(service.approve_execution(args.candidate_id))
        elif args.operation == "approve-acceptance": print(service.approve_acceptance(args.candidate_id))
        elif args.operation == "accept": print(service.accept(args.candidate_id))
        elif args.operation == "reject": print(service.reject(args.candidate_id, args.reason or "rejected by reviewer"))
        else:
            for row in service.audit.events(args.candidate_id): print(tuple(row))
        return 0
    except Exception as exc: print(f"review failed: {exc}"); return 2
if __name__ == "__main__": raise SystemExit(main())
