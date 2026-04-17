import logging
import os
import sys

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"


def resolve_log_file_path(log_file_name: str) -> str:
    """Resolve the configured log file against the app directory."""
    if os.path.isabs(log_file_name):
        return log_file_name

    base_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__)
    # Se estivemos em src/core, precisamos subir dois níveis para chegar na raiz se estivermos em modo dev
    if not getattr(sys, "frozen", False):
         # Se este arquivo está em src/core/logging_config.py, a raiz está 2 níveis acima
         base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    
    return os.path.abspath(os.path.join(base_dir, log_file_name))


def apply_logging_configuration(level_name: str, log_file_name: str) -> str:
    """Apply level and file target to the root logger."""
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    log_file_path = resolve_log_file_path(log_file_name)
    root_logger = logging.getLogger()

    root_logger.setLevel(level)
    for handler in list(root_logger.handlers):
        if isinstance(handler, logging.FileHandler):
            root_logger.removeHandler(handler)
            handler.close()
        else:
            handler.setLevel(level)

    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(file_handler)

    for handler in root_logger.handlers:
        handler.setLevel(level)

    return log_file_path


def setup_initial_logging(level_name: str, log_file_name: str) -> str:
    """Initial bootstrapping of the logging system."""
    log_level = getattr(logging, level_name.upper(), logging.INFO)
    log_file_path = resolve_log_file_path(log_file_name)
    
    logging.basicConfig(
        level=log_level,
        format=LOG_FORMAT,
        handlers=[
            logging.FileHandler(log_file_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file_path
