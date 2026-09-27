import os
from pathlib import Path
import pytest
pytest.importorskip("PySide6"); os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from file_janitor.application import RemoteFolderInfo
from file_janitor.gui import main_window as m
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow

def test_remote_feedback(monkeypatch):
 create_application(["t"]); w=MainWindow(); monkeypatch.setattr(m,"remote_folder_info",lambda p: RemoteFolderInfo(True,"fuse.rclone",Path("/cloud"))); w._auto_select_analysis_profile(Path("/cloud")); assert w.analysis_profile_combo.currentData()=="remote"; assert "fuse.rclone" in w.analysis_profile_hint.text(); w.close()

def test_local_feedback(monkeypatch):
 create_application(["t2"]); w=MainWindow(); monkeypatch.setattr(m,"remote_folder_info",lambda p: RemoteFolderInfo(False,"ext4",Path("/"))); w._auto_select_analysis_profile(Path("/home")); assert w.analysis_profile_combo.currentData()=="standard"; assert "ext4" in w.analysis_profile_hint.text(); w.close()
