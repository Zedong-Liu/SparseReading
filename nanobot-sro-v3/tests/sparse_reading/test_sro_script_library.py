from pathlib import Path

from nanobot.sparse_reading.detector import inspect_file
from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator


def _hint(card, **overrides):
    base = {
        "goal": "select a LAMMPS script template",
        "needles": [],
        "want": "verbatim",
        "scope": "new",
        "artifact": card.artifact_id,
        "type_hint": "script_library",
        "must_keep": [],
    }
    base.update(overrides)
    return base


def _write_template(root: Path, name: str, family: str, material: str, pair_style: str, body: str) -> None:
    (root / name).write_text(
        f"# task_family: {family}\n"
        f"# material: {material}\n"
        "class LammpsRunner:\n"
        "    pass\n\n"
        "lammps_input = \"\"\"\n"
        "units metal\n"
        "atom_style atomic\n"
        f"pair_style {pair_style}\n"
        f"# material marker {material}\n"
        f"{body}\n"
        "run 1000\n"
        "\"\"\"\n",
        encoding="utf-8",
    )


def _script_library(root: Path) -> Path:
    lib = root / "lammps_templates"
    lib.mkdir()
    _write_template(
        lib,
        "LCG_0001_tensile_Al.py",
        "tensile_test",
        "Al",
        "eam/alloy",
        "fix deform all deform 1 x erate 0.001\ncompute stress all stress/atom NULL",
    )
    _write_template(
        lib,
        "LCG_0002_tensile_Cu.py",
        "tensile_test",
        "Cu",
        "eam/alloy",
        "fix deform all deform 1 y erate 0.001\nthermo_style custom step temp press",
    )
    _write_template(
        lib,
        "LCG_0003_bulk_Ni.py",
        "bulk_equilibration",
        "Ni",
        "eam",
        "fix npt all npt temp 300 300 0.1 iso 0 0 1.0",
    )
    _write_template(
        lib,
        "LCG_0004_rdf_Ar.py",
        "rdf_analysis",
        "Ar",
        "lj/cut",
        "compute rdf_all all rdf 100\nfix rdf_out all ave/time 10 10 100 c_rdf_all[*] file rdf.dat mode vector",
    )
    return lib


def test_detect_script_library(tmp_path):
    lib = _script_library(tmp_path)

    info = inspect_file(lib)

    assert info.type == "script_library"
    assert info.supported
    assert info.large


def test_scout_returns_index(tmp_path):
    lib = _script_library(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(lib)

    pack = sro.read({"artifact_id": card.artifact_id}, "scout", _hint(card))

    assert pack.type == "script_library"
    assert any("task_family=tensile_test" in line for line in pack.skeleton)
    assert all("pair_style" not in block.text for block in pack.evidence)


def test_focus_returns_best_match(tmp_path):
    lib = _script_library(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(lib)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        _hint(card, goal="aluminum tensile test", needles=["tensile_test", "Al", "eam/alloy"]),
    )

    assert pack.error == ""
    assert pack.evidence
    assert "task_family=tensile_test" in pack.evidence[0].anchor
    assert "material=Al" in pack.evidence[0].anchor
    assert "fix deform" in pack.evidence[0].text


def test_focus_no_match_returns_advisory(tmp_path):
    lib = _script_library(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(lib)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        _hint(card, goal="polymer crosslinking with ReaxFF", needles=["polymer_crosslinking", "reax/c"]),
    )

    assert pack.evidence == []
    assert pack.unresolved
    assert pack.next_action is not None
    assert "native fallback" in pack.next_action["allowed_next"]


def test_focus_adds_runtime_invariants_for_manybody_pair_coeff(tmp_path):
    lib = tmp_path / "lammps_templates"
    lib.mkdir()
    _write_template(
        lib,
        "LCG_0104_nano_melting_Fe.py",
        "nano_melting",
        "Fe",
        "eam/fs",
        "region sphere sphere 0 0 0 15 units lattice\n"
        "create_atoms 1 region sphere\n"
        "pair_coeff * * Fe.eam.fs\n"
        "fix heat all nvt temp 300 600 0.1\n"
        "unfix heat",
    )
    for idx in range(3):
        _write_template(
            lib,
            f"LCG_extra_{idx}_bulk_Cu.py",
            "bulk_equilibration",
            "Cu",
            "eam/alloy",
            "pair_coeff * * Cu.eam.alloy Cu\nfix nvt all nvt temp 300 300 0.1",
        )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(lib)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        _hint(card, goal="iron nano melting", needles=["nano_melting", "Fe", "eam/fs"]),
    )

    text = "\n".join(block.text for block in pack.evidence)
    assert "RUNTIME_INVARIANTS_DO_NOT_COPY_BLINDLY" in text
    assert "pair_coeff * * Fe.eam.fs Fe" in text
    assert "bare `pair_coeff * * file`" in text


def test_focus_adds_runtime_invariants_for_read_data(tmp_path):
    lib = tmp_path / "lammps_templates"
    lib.mkdir()
    _write_template(
        lib,
        "LCG_0057_defect_diffusion_LJ.py",
        "defect_diffusion",
        "LJ fluid",
        "lj/cut",
        "read_data data.lmp\n"
        "pair_coeff 1 1 1.0 1.0 2.5\n"
        "compute msd all msd\n"
        "fix msd_out all ave/time 100 1 100 c_msd[4] file msd.dat",
    )
    for idx in range(3):
        _write_template(
            lib,
            f"LCG_extra_{idx}_rdf_Ar.py",
            "rdf_analysis",
            "Ar",
            "lj/cut",
            "compute rdf_all all rdf 100\nfix rdf_out all ave/time 10 10 100 c_rdf_all[*] file rdf.dat mode vector",
        )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(lib)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        _hint(card, goal="LJ defect diffusion", needles=["defect_diffusion", "LJ fluid", "read_data"]),
    )

    text = "\n".join(block.text for block in pack.evidence)
    assert "RUNTIME_INVARIANTS_DO_NOT_COPY_BLINDLY" in text
    assert "must create or copy that exact data file" in text
    assert "prefer lattice/create_box/create_atoms" in text
    assert "mass 1 1.0" in text
    assert "delete_atoms group <group>" in text


def test_focus_adds_restart_stage_boundary_invariants(tmp_path):
    lib = tmp_path / "lammps_templates"
    lib.mkdir()
    _write_template(
        lib,
        "LCG_0059_restart_Si.py",
        "restart_workflow",
        "Si",
        "tersoff",
        "lattice diamond 5.43\n"
        "create_box 1 box\n"
        "pair_coeff * * Si.tersoff Si\n"
        "write_restart equilibrated.restart\n"
        "read_restart equilibrated.restart\n"
        "run 1000",
    )
    for idx in range(3):
        _write_template(
            lib,
            f"LCG_extra_{idx}_bulk_Cu.py",
            "bulk_equilibration",
            "Cu",
            "eam/alloy",
            "pair_coeff * * Cu.eam.alloy Cu\nfix nvt all nvt temp 300 300 0.1",
        )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(lib)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        _hint(card, goal="Si restart workflow", needles=["restart_workflow", "Si", "read_restart"]),
    )

    text = "\n".join(block.text for block in pack.evidence)
    assert "use `clear` between `write_restart` and `read_restart`" in text


def test_focus_adds_surface_slab_vacuum_invariants(tmp_path):
    lib = tmp_path / "lammps_templates"
    lib.mkdir()
    _write_template(
        lib,
        "LCG_0125_surface_CuNi.py",
        "surface_relaxation",
        "Cu-Ni",
        "eam/alloy",
        "region slab block 0 10 0 10 0 6\n"
        "create_atoms 1 region slab\n"
        "pair_coeff * * CuNi.eam.alloy Cu Ni\n"
        "minimize 1e-6 1e-8 1000 10000",
    )
    for idx in range(3):
        _write_template(
            lib,
            f"LCG_extra_{idx}_rdf_Ar.py",
            "rdf_analysis",
            "Ar",
            "lj/cut",
            "compute rdf_all all rdf 100\nfix rdf_out all ave/time 10 10 100 c_rdf_all[*] file rdf.dat mode vector",
        )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(lib)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        _hint(card, goal="Cu-Ni surface relaxation slab", needles=["surface_relaxation", "Cu-Ni", "slab"]),
    )

    text = "\n".join(block.text for block in pack.evidence)
    assert "simulation box taller than the populated slab region" in text
    assert "do not fill the full box" in text


def test_collect_multi_family(tmp_path):
    lib = _script_library(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(lib)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        _hint(
            card,
            goal="need tensile and rdf templates",
            slots=[
                {"id": "tensile_test", "question": "Find an Al tensile_test template", "aliases": ["Al"]},
                {"id": "rdf_analysis", "question": "Find an Ar rdf_analysis template", "aliases": ["Ar"]},
            ],
        ),
    )

    anchors = "\n".join(block.anchor for block in pack.evidence)
    assert len(pack.evidence) == 2
    assert "task_family=tensile_test" in anchors
    assert "task_family=rdf_analysis" in anchors


def test_existing_collection_unaffected(tmp_path):
    plain = tmp_path / "plain_python_collection"
    plain.mkdir()
    for idx in range(3):
        (plain / f"module_{idx}.py").write_text(f"print({idx})\n", encoding="utf-8")

    info = inspect_file(plain)

    assert info.type == "collection"
