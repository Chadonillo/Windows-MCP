import os

import shutil

import pytest

from windows_mcp.filesystem import service
from windows_mcp.filesystem.service import (
    read_file,
    write_file,
    copy_path,
    move_path,
    delete_path,
    list_directory,
    search_files,
    get_file_info,
)


class TestReadFile:
    def test_read_entire_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world", encoding="utf-8")
        result = read_file(str(f))
        assert "hello world" in result

    def test_read_with_offset_and_limit(self, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
        result = read_file(str(f), offset=2, limit=2)
        assert "line2" in result
        assert "line3" in result
        assert "line4" not in result

    def test_read_nonexistent(self, tmp_path):
        result = read_file(str(tmp_path / "nope.txt"))
        assert "Error: File not found" in result

    def test_read_directory_path(self, tmp_path):
        result = read_file(str(tmp_path))
        assert "Error: Path is not a file" in result


class TestWriteFile:
    def test_write_new_file(self, tmp_path):
        f = tmp_path / "out.txt"
        result = write_file(str(f), "content")
        assert "Written to" in result
        assert f.read_text(encoding="utf-8") == "content"

    def test_append_to_file(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_text("first", encoding="utf-8")
        write_file(str(f), " second", append=True)
        assert f.read_text(encoding="utf-8") == "first second"

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "a" / "b" / "c.txt"
        result = write_file(str(f), "deep")
        assert "Written to" in result
        assert f.read_text(encoding="utf-8") == "deep"


class TestCopyPath:
    def test_copy_file(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        result = copy_path(str(src), str(dst))
        assert "Copied file" in result
        assert dst.read_text(encoding="utf-8") == "data"

    def test_copy_directory(self, tmp_path):
        src = tmp_path / "srcdir"
        src.mkdir()
        (src / "file.txt").write_text("inside", encoding="utf-8")
        dst = tmp_path / "dstdir"
        result = copy_path(str(src), str(dst))
        assert "Copied directory" in result
        assert (dst / "file.txt").read_text(encoding="utf-8") == "inside"

    def test_copy_source_not_found(self, tmp_path):
        result = copy_path(str(tmp_path / "nope"), str(tmp_path / "dst"))
        assert "Error: Source not found" in result

    def test_copy_destination_exists_no_overwrite(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        dst.write_text("existing", encoding="utf-8")
        result = copy_path(str(src), str(dst))
        assert "Error: Destination already exists" in result

    def test_copy_destination_exists_overwrite(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("new", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        dst.write_text("old", encoding="utf-8")
        result = copy_path(str(src), str(dst), overwrite=True)
        assert "Copied file" in result
        assert dst.read_text(encoding="utf-8") == "new"


class TestMovePath:
    def test_move_file(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        result = move_path(str(src), str(dst))
        assert "Moved" in result
        assert not src.exists()
        assert dst.read_text(encoding="utf-8") == "data"

    def test_move_source_not_found(self, tmp_path):
        result = move_path(str(tmp_path / "nope"), str(tmp_path / "dst"))
        assert "Error: Source not found" in result

    def test_move_destination_exists_no_overwrite(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        dst.write_text("existing", encoding="utf-8")
        result = move_path(str(src), str(dst))
        assert "Error: Destination already exists" in result


class TestDeletePath:
    @pytest.fixture
    def recycled(self, monkeypatch):
        """Replace the shell call so tests never touch the real Recycle Bin.

        Returns the list of paths the service asked to recycle.
        """
        calls = []

        def fake_recycle(target):
            calls.append(str(target))
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

        monkeypatch.setattr(service, "_send_to_recycle_bin", fake_recycle)
        return calls

    def test_delete_file_recycles_by_default(self, tmp_path, recycled):
        f = tmp_path / "del.txt"
        f.write_text("bye", encoding="utf-8")
        result = delete_path(str(f))
        assert "Moved file to Recycle Bin" in result
        assert recycled == [str(f.resolve())]
        assert not f.exists()

    def test_delete_file_permanent_bypasses_recycle_bin(self, tmp_path, recycled):
        f = tmp_path / "del.txt"
        f.write_text("bye", encoding="utf-8")
        result = delete_path(str(f), permanent=True)
        assert "Permanently deleted file" in result
        assert recycled == []
        assert not f.exists()

    def test_delete_empty_dir(self, tmp_path, recycled):
        d = tmp_path / "emptydir"
        d.mkdir()
        result = delete_path(str(d))
        assert "Moved directory to Recycle Bin" in result
        assert not d.exists()

    def test_delete_empty_dir_permanent(self, tmp_path, recycled):
        d = tmp_path / "emptydir"
        d.mkdir()
        result = delete_path(str(d), permanent=True)
        assert "Permanently deleted directory" in result
        assert recycled == []
        assert not d.exists()

    def test_delete_nonempty_dir_without_recursive(self, tmp_path, recycled):
        d = tmp_path / "fulldir"
        d.mkdir()
        (d / "file.txt").write_text("x", encoding="utf-8")
        result = delete_path(str(d), recursive=False)
        assert "Error: Directory is not empty" in result
        assert recycled == []
        assert d.exists()

    def test_delete_nonempty_dir_recursive(self, tmp_path, recycled):
        d = tmp_path / "fulldir"
        d.mkdir()
        (d / "file.txt").write_text("x", encoding="utf-8")
        result = delete_path(str(d), recursive=True)
        assert "Moved directory to Recycle Bin" in result
        assert not d.exists()

    def test_delete_nonempty_dir_permanent_recursive(self, tmp_path, recycled):
        d = tmp_path / "fulldir"
        d.mkdir()
        (d / "file.txt").write_text("x", encoding="utf-8")
        result = delete_path(str(d), recursive=True, permanent=True)
        assert "Permanently deleted directory" in result
        assert recycled == []
        assert not d.exists()

    def test_delete_not_found(self, tmp_path):
        result = delete_path(str(tmp_path / "ghost"))
        assert "Error: Path not found" in result

    def test_failed_recycle_reports_error_and_keeps_file(self, tmp_path, monkeypatch):
        """A shell failure must not be reported as a successful delete."""

        def boom(target):
            raise OSError("Recycle failed for X (shell error 120)")

        monkeypatch.setattr(service, "_send_to_recycle_bin", boom)
        f = tmp_path / "keep.txt"
        f.write_text("x", encoding="utf-8")
        result = delete_path(str(f))
        assert "Error deleting" in result
        assert f.exists()


class TestSendToRecycleBin:
    """Cover the shell wrapper itself, which the delete tests patch out."""

    @staticmethod
    def _patch_shfileoperation(monkeypatch, return_value):
        from win32com.shell import shell

        calls = []

        def fake_op(args):
            calls.append(args)
            return return_value

        monkeypatch.setattr(shell, "SHFileOperation", fake_op)
        return calls

    def test_passes_allowundo_and_absolute_path(self, tmp_path, monkeypatch):
        from win32com.shell import shellcon

        calls = self._patch_shfileoperation(monkeypatch, (0, False))
        target = tmp_path / "x.txt"
        service._send_to_recycle_bin(target)

        (_hwnd, operation, source, _dest, flags, _p, _n) = calls[0]
        assert operation == shellcon.FO_DELETE
        assert source == str(target)
        assert flags & shellcon.FOF_ALLOWUNDO, "must request the Recycle Bin, not a hard delete"

    def test_nonzero_return_code_raises(self, tmp_path, monkeypatch):
        self._patch_shfileoperation(monkeypatch, (120, False))
        with pytest.raises(OSError, match="shell error 120"):
            service._send_to_recycle_bin(tmp_path / "x.txt")

    def test_aborted_raises(self, tmp_path, monkeypatch):
        self._patch_shfileoperation(monkeypatch, (0, True))
        with pytest.raises(OSError, match="aborted"):
            service._send_to_recycle_bin(tmp_path / "x.txt")


class TestListDirectory:
    def test_list_basic(self, tmp_path):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        result = list_directory(str(tmp_path))
        assert "a.txt" in result
        assert "b.txt" in result

    def test_list_with_pattern(self, tmp_path):
        (tmp_path / "hello.py").write_text("x", encoding="utf-8")
        (tmp_path / "hello.txt").write_text("x", encoding="utf-8")
        result = list_directory(str(tmp_path), pattern="*.py")
        assert "hello.py" in result
        assert "hello.txt" not in result

    def test_list_hides_hidden_by_default(self, tmp_path):
        (tmp_path / ".hidden").write_text("x", encoding="utf-8")
        (tmp_path / "visible").write_text("x", encoding="utf-8")
        result = list_directory(str(tmp_path))
        assert ".hidden" not in result
        assert "visible" in result

    def test_list_shows_hidden_when_enabled(self, tmp_path):
        (tmp_path / ".hidden").write_text("x", encoding="utf-8")
        result = list_directory(str(tmp_path), show_hidden=True)
        assert ".hidden" in result

    def test_list_empty(self, tmp_path):
        result = list_directory(str(tmp_path))
        assert "empty" in result.lower()

    def test_list_not_found(self, tmp_path):
        result = list_directory(str(tmp_path / "nope"))
        assert "Error: Directory not found" in result

    def test_dirs_listed_before_files(self, tmp_path):
        (tmp_path / "z_file.txt").write_text("x", encoding="utf-8")
        (tmp_path / "a_dir").mkdir()
        result = list_directory(str(tmp_path))
        dir_pos = result.index("a_dir")
        file_pos = result.index("z_file.txt")
        assert dir_pos < file_pos


class TestSearchFiles:
    def test_search_basic(self, tmp_path):
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        result = search_files(str(tmp_path), "*.py")
        assert "a.py" in result
        assert "b.txt" not in result

    def test_search_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("x", encoding="utf-8")
        result = search_files(str(tmp_path), "*.py", recursive=True)
        assert "deep.py" in result

    def test_search_no_matches(self, tmp_path):
        result = search_files(str(tmp_path), "*.xyz")
        assert "No matches found" in result

    def test_search_path_not_found(self, tmp_path):
        result = search_files(str(tmp_path / "nope"), "*.py")
        assert "Error: Search path not found" in result


class TestGetFileInfo:
    def test_file_info(self, tmp_path):
        f = tmp_path / "info.txt"
        f.write_text("hello", encoding="utf-8")
        result = get_file_info(str(f))
        assert "Type: File" in result
        assert "Extension: .txt" in result
        assert "5" in result  # 5 bytes

    def test_dir_info(self, tmp_path):
        d = tmp_path / "mydir"
        d.mkdir()
        (d / "child.txt").write_text("x", encoding="utf-8")
        result = get_file_info(str(d))
        assert "Type: Directory" in result
        assert "1 files" in result

    def test_not_found(self, tmp_path):
        result = get_file_info(str(tmp_path / "nope"))
        assert "Error: Path not found" in result
