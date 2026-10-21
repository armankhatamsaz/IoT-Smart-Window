import os
import sys
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(folder, filename):
    """Import a service script straight from its folder. Each microservice is a
    standalone script rather than an installed package."""
    folder_path = os.path.join(ROOT, folder)
    if folder_path not in sys.path:
        sys.path.insert(0, folder_path)
    spec = importlib.util.spec_from_file_location(f"{folder}_{filename[:-3]}", os.path.join(folder_path, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def settings(folder, name="settings.json"):
    return os.path.join(ROOT, folder, name)
