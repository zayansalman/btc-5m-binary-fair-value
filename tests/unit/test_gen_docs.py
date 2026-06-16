# tests/unit/test_gen_docs.py
from pathlib import Path
import tools.gen_docs as gd


def _make_tree(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""Pkg root."""\n')
    (pkg / "alpha.py").write_text('"""Computes alpha."""\nX = 1\n')
    (pkg / "beta.py").write_text("X = 2\n")  # no docstring
    return tmp_path


def test_inventory_reads_roles(tmp_path):
    root = _make_tree(tmp_path)
    mods = gd.collect_modules(root, source_roots=["pkg"], toplevel=[])
    by_path = {m.path: m for m in mods}
    assert by_path["pkg/alpha.py"].role == "Computes alpha."
    assert by_path["pkg/beta.py"].role == gd.NO_DOCSTRING
    assert by_path["pkg/__init__.py"].role == "Pkg root."
