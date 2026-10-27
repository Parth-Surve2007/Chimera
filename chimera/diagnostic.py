import json, shutil, subprocess
from .local_model import config_from_env, check_status, tool_version

def diagnostic() -> dict:
    config = config_from_env(); status = check_status(config)
    nvidia = shutil.which("nvidia-smi")
    return {"status": status.state, "detail": status.detail, "model": {"path": str(config.model_path) if config.model_path else None, "exists": bool(config.model_path and config.model_path.is_file()), "size": config.model_path.stat().st_size if config.model_path and config.model_path.is_file() else None}, "runtime": {"path": config.cli_path, "exists": bool(config.cli_path and __import__('pathlib').Path(config.cli_path).is_file()), "version": tool_version(config.cli_path)}, "configuration": {"context": config.context, "max_tokens": config.max_tokens, "threads": config.threads, "timeout": config.timeout, "gpu_layers": config.gpu_layers}, "nvidia_telemetry": "available" if nvidia else "unavailable", "guidance": "Set CHIMERA_MODEL_PATH and CHIMERA_LLAMA_CLI to user-provided local files." if status.state != "ready" else "Ready for local inference."}

def main():
    print(json.dumps(diagnostic(), indent=2))
if __name__ == "__main__": main()

