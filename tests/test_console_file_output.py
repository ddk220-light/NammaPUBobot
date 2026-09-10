import importlib.util
from pathlib import Path

from nammaoe2bot.runtime.config import cfg


CONSOLE_PATH = (
	Path(__file__).resolve().parent.parent / "nammaoe2bot" / "runtime" / "console.py"
)


def _load_real_console(module_name):
	spec = importlib.util.spec_from_file_location(module_name, CONSOLE_PATH)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_file_duplication_is_off_by_default(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)
	monkeypatch.setattr(cfg, "FILE_LOG_ENABLED", False, raising=False)
	module = _load_real_console("test_console_default_off")

	assert module.log.file is None
	assert not (tmp_path / "logs").exists()
	module.log.info("stdout remains available")


def test_file_duplication_can_be_opted_in(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)
	monkeypatch.setattr(cfg, "FILE_LOG_ENABLED", True, raising=False)
	module = _load_real_console("test_console_opted_in")

	assert module.log.file is not None
	module.log.info("written locally")
	module.log.close()
	files = list((tmp_path / "logs").iterdir())
	assert len(files) == 1
	assert "written locally" in files[0].read_text()

