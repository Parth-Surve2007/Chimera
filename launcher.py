"""Chimera launcher with persistent startup-crash diagnostics."""
import logging
import os
import sys
import traceback
from pathlib import Path


def _configure_startup_logging():
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Chimera" / "logs"
    try:
        root.mkdir(parents=True, exist_ok=True)
        log_path = root / "startup.log"
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            encoding="utf-8",
            force=True,
        )
        logging.info(
            "Chimera launcher starting; frozen=%s; executable=%s",
            getattr(sys, "frozen", False),
            sys.executable,
        )
        return log_path
    except OSError:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logging.exception("Could not create the startup log directory")
        return None


def _install_exception_hooks():
    def report(exc_type, exc_value, exc_traceback):
        logging.critical(
            "Uncaught startup exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        traceback.print_exception(exc_type, exc_value, exc_traceback)

    sys.excepthook = report
    import threading
    if hasattr(threading, "excepthook"):
        def report_thread(args):
            logging.critical(
                "Uncaught exception in thread %s",
                args.thread.name if args.thread else "<unknown>",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        threading.excepthook = report_thread


def main():
    log_path = _configure_startup_logging()
    _install_exception_hooks()
    try:
        from chimera.app import main as app_main
        result = app_main()
        logging.info("Chimera application entry point returned: %r", result)
        return result
    except BaseException:
        logging.exception("Chimera failed during startup")
        print("Chimera failed to start. Diagnostic log:")
        print(log_path or "See the console traceback above.")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    sys.exit(main())
