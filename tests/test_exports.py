"""The exports with shaped and special pieces: the STL stack, the FreeCAD document (its colours inside, its sheet of
dimensions, its stacking check) and the building manual."""
import json
import shutil
import subprocess
import zipfile
from xml.dom import minidom

import numpy as np
import pytest

from lego_engine import LegoSettings, Legolizer
from lego_engine.export_freecad import material_list_blob, packed, write_view_colours
from lego_engine.export_manual import write_manual
from lego_engine.export_stl import write_stl_stack
from lego_engine.mesh_io import find_freecadcmd

ALL = ("slope", "curve", "round", "dome", "inverted")


@pytest.fixture(scope="module")
def shaped(parts):
    return Legolizer().run(LegoSettings(input=str(parts["sphere"]), size=8, shapes=ALL, special=True,
                                        special_tolerance=0.2))


def test_packed_colours_like_freecad():
    # Base::Color::getPackedValue: 0xRRGGBBAA, x * 255 + 0.5 truncated (FreeCAD wrote 0xcc1a0d00 for 0.8, 0.1, 0.05)
    assert packed((0.8, 0.1, 0.05)) == 0xCC1A0D00
    blob = material_list_blob([((0.8, 0.1, 0.05), 0.0), ((0.1, 0.4, 0.9), 0.8)])
    assert len(blob) == 76 and blob[:4] == b"\x02\x00\x00\x00"


@pytest.mark.skipif(find_freecadcmd() is None, reason="needs freecadcmd")
def test_colour_record_matches_what_freecad_writes(tmp_path):
    """Our ShapeAppearance record byte for byte against the one freecadcmd writes for the same materials."""
    out = tmp_path / "ref.FCStd"
    script = tmp_path / "ref.py"
    script.write_text(f'''import FreeCAD as App
doc = App.newDocument("ref")
o = doc.addObject("App::FeaturePython", "Holder")
o.addProperty("App::PropertyMaterialList", "Mats")
mats = []
for rgb, tr in (((0.8, 0.1, 0.05), 0.0), ((0.1, 0.4, 0.9), 0.8)):
    m = App.Material()
    m.DiffuseColor = rgb
    m.AmbientColor = (0.3333, 0.3333, 0.3333)
    m.SpecularColor = (0.5333, 0.5333, 0.5333)
    m.EmissiveColor = (0.0, 0.0, 0.0)
    m.Shininess = 0.9
    m.Transparency = tr
    mats.append(m)
o.Mats = mats
doc.saveAs({str(out)!r})
''')
    subprocess.run([find_freecadcmd(), str(script)], capture_output=True, timeout=120)
    ref = zipfile.ZipFile(out).read("Mats")
    assert material_list_blob([((0.8, 0.1, 0.05), 0.0), ((0.1, 0.4, 0.9), 0.8)]) == ref


def test_view_colours_go_after_freecads_own_entries(tmp_path):
    path = tmp_path / "x.FCStd"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Document.xml", "<Document/>")
        z.writestr("Brick00000.Shape.brp", "x")
    write_view_colours(path, {"Brick00000": ((1, 0, 0), 0), "Input": ((0.7, 0.7, 0.7), 80)}, "")
    names = zipfile.ZipFile(path).namelist()
    assert names == ["Document.xml", "Brick00000.Shape.brp", "GuiDocument.xml", "ShapeAppearance", "ShapeAppearance1"]
    gui = minidom.parseString(zipfile.ZipFile(path).read("GuiDocument.xml"))
    vps = gui.getElementsByTagName("ViewProvider")
    assert [v.getAttribute("name") for v in vps] == ["Brick00000", "Input"]
    files = [e.getAttribute("file") for e in gui.getElementsByTagName("MaterialList")]
    assert files == ["ShapeAppearance", "ShapeAppearance1"]
    with pytest.raises(ValueError):
        write_view_colours(path, {"Brick00000": ((1, 0, 0), 0)})


def test_stl_stack_with_pieces(shaped, tmp_path):
    assert shaped.stats["shaped"] and shaped.stats["special"]
    m = write_stl_stack(shaped, tmp_path / "stack", "both")
    assert all(x["watertight"] for x in m["files"]["layers"])
    files = m["files"]["bricks"]
    assert any(x["file"].startswith("bricks/piece_") for x in files)
    assert any(x["file"].startswith("bricks/special_") for x in files)
    assert all(x["watertight"] for x in files)
    assert sum(x["count"] for x in files) == shaped.stats["bricks"]


def test_result_json_names_the_pieces(shaped, tmp_path):
    d = json.loads(shaped.save_json(tmp_path / "r.json").read_text())
    assert len(d["bricks"]) == len(d["kinds"]) == len(d["turns"])
    assert all(len(b) == 5 for b in d["bricks"])                       # the logo's reader takes plain lists
    assert "special" in d["kinds"] and any(k.startswith("curve") for k in d["kinds"])


@pytest.mark.skipif(find_freecadcmd() is None, reason="needs freecadcmd")
def test_freecad_document_is_coloured_and_stacks(shaped, tmp_path):
    from lego_engine.export_freecad import write_fcstd
    info = write_fcstd(shaped, tmp_path / "s.FCStd", colours=shaped.colours("rainbow"))
    z = zipfile.ZipFile(tmp_path / "s.FCStd")
    assert "GuiDocument.xml" in z.namelist() and info["coloured"] == len(shaped.model.bricks) + 1
    st = info["stacking"]
    assert st["pairs"] > 5 and st["max_overlap_mm3"] < 0.01
    assert abs(st["min_stud_gap_mm"] - 0.15) < 0.005 and abs(st["max_stud_gap_mm"] - 0.15) < 0.005
    check = tmp_path / "check.py"
    check.write_text(f'''import FreeCAD as App
d = App.openDocument({str(tmp_path / "s.FCStd")!r})
s = d.getObject("Dimensions")
print("SHEET", s.get("studPlay"), s.get("wall"), s.get("tubeD"), s.get("socketD"), len(d.Objects))
''')
    out = subprocess.run([find_freecadcmd(), str(check)], capture_output=True, text=True, timeout=120).stdout
    line = [x for x in out.splitlines() if x.startswith("SHEET")][0].split()
    assert [float(x) for x in line[1:5]] == pytest.approx([0.15, 1.35, 6.2137, 5.1], abs=1e-4)


def test_building_manual(shaped, tmp_path):
    info = write_manual(shaped, tmp_path / "manual", pictures=False, pdf=shutil.which("pdflatex") is not None)
    tex = (tmp_path / "manual" / "manual.tex").read_text()
    layers = sorted({b.layer for b in shaped.model.bricks})
    assert info["steps"] == len(layers) and tex.count(r"\section*{Step ") == len(layers)
    assert r"\begin{tikzpicture}" in tex and "special" in tex
    if shutil.which("pdflatex"):
        assert info["pdf"] and (tmp_path / "manual" / "manual.pdf").stat().st_size > 10000, info["log"][-1500:]
