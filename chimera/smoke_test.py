import sys
from .local_model import LocalModelProvider, config_from_env

def main() -> int:
    provider = LocalModelProvider(config_from_env()); result = provider.plan("Describe a safe way to organize a local folder; return a domain-independent task plan.")
    if result.status != "ready": print(f"Local inference not completed: {result.status}: {result.error}", file=sys.stderr); return 2
    print("Genuine local inference succeeded."); print(result.task); print(f"elapsed_ms={result.duration_ms} generated_tokens={result.generated_tokens}"); return 0
if __name__ == "__main__": raise SystemExit(main())
