import threading

_runner = None
_lock = threading.Lock()


def get_runner(models_dir, device=None):
    # one Router per process. both checkpoints resident is ~2GB of RAM already,
    # and the abuse detector loading its own copy would just double that
    global _runner
    with _lock:
        if _runner is None:
            from laya import Router

            _runner = Router(
                models={
                    "english": (str(models_dir), None),
                    "multilingual": (str(models_dir), "multilingual"),
                    "typed-decisions": (str(models_dir), "typed-decisions"),
                },
                device=device,
                max_loaded=2,
                default="english",
            )
        return _runner
