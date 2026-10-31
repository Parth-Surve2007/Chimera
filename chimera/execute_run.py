import argparse, json, os
from pathlib import Path
from .execution import run

def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m chimera.execute_run")
    parser.add_argument("objective")
    parser.add_argument("--no-open", action="store_true", help="create and verify without launching Paint")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = run(args.objective, Path(os.environ.get("CHIMERA_DATA_DIR", ".chimera")), not args.no_open)
    if args.json: print(json.dumps(result, indent=2))
    else:
        observation = result.get("observation") or {}
        print(f"Status: {result.get('status', 'unknown')}\nError: {result.get('error') or result.get('reason') or 'none'}\nArtifact: {result.get('artifact') or 'none'}\nVerification: {observation.get('valid', False)}")
    return 0 if result["status"] == "completed" else 2

if __name__ == "__main__": raise SystemExit(main())
