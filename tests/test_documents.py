import pytest
from pathlib import Path

from chimera.documents import create_docx


def test_docx_creation_does_not_delete_file_created_by_another_process(tmp_path, monkeypatch):
    target = tmp_path / "race.docx"
    external_content = b"external process owns this file"
    original_open = Path.open

    def racing_open(self, mode="r", *args, **kwargs):
        if self == target and mode == "xb":
            with original_open(self, "wb") as stream:
                stream.write(external_content)
            raise FileExistsError(str(target))
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    with pytest.raises(FileExistsError):
        create_docx(target, title="Race", content="Must not delete another file.")

    assert target.read_bytes() == external_content


def test_docx_writer_removes_its_own_partial_file_on_write_failure(tmp_path, monkeypatch):
    target = tmp_path / "partial.docx"

    def fail_after_open(self, *args, **kwargs):
        raise OSError("simulated disk write failure")

    monkeypatch.setattr("zipfile.ZipFile.writestr", fail_after_open)
    with pytest.raises(OSError, match="simulated"):
        create_docx(target, title="Partial", content="Partial document")
    assert not target.exists()
