from pathlib import Path
from file_janitor.application.remote import remote_folder_info

def test_remote_info(tmp_path):
 p=tmp_path/"m"; p.write_text("1 0 0:1 / / rw - ext4 /dev/root rw\n2 1 0:2 / /cloud rw - fuse.rclone x rw\n")
 i=remote_folder_info(Path("/cloud/a"),mountinfo_path=p)
 assert (i.is_remote,i.filesystem_type,i.mount_point)==(True,"fuse.rclone",Path("/cloud"))

def test_local_info(tmp_path):
 p=tmp_path/"m"; p.write_text("1 0 0:1 / / rw - ext4 /dev/root rw\n")
 i=remote_folder_info(Path("/home"),mountinfo_path=p)
 assert (i.is_remote,i.filesystem_type,i.mount_point)==(False,"ext4",Path("/"))

def test_unknown_info(tmp_path):
 i=remote_folder_info(Path("/x"),mountinfo_path=tmp_path/"missing")
 assert i.filesystem_type is None and i.mount_point is None and not i.is_remote
