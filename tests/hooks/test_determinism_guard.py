"""The determinism guard flags every non-reproducible read in the core, and nothing else."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from determinism_guard import check_source, in_scope

GUARD = Path(__file__).parents[2] / ".claude" / "hooks" / "determinism_guard.py"


def _findings(source: str) -> list[str]:
    return check_source(source, "src/docgap/stage.py")


# One case per rule, each with the part of the finding that names it.
FLAGGED = [
    # clock
    ("import datetime\ndatetime.datetime.now()", "datetime.datetime.now()"),
    ("from datetime import datetime as dt\ndt.utcnow()", "datetime.datetime.utcnow()"),
    ("from datetime import datetime\ndatetime.today()", "datetime.datetime.today()"),
    ("from datetime import date\ndate.today()", "datetime.date.today()"),
    ("import time\ntime.time()", "time.time()"),
    ("import time\ntime.time_ns()", "time.time_ns()"),
    # ids and hashing
    ("import uuid\nuuid.uuid1()", "uuid.uuid1()"),
    ("import uuid\nuuid.uuid4()", "uuid.uuid4()"),
    ("hash('column')", "hash()"),
    # environment and working directory
    ("import os\nos.getenv('X')", "os.getenv()"),
    ("import os\nos.environ['X']", "environment read"),
    ("from os import environ\nenviron.get('X')", "environment read"),
    ("import os\nos.getcwd()", "os.getcwd()"),
    ("from os import getcwd\ngetcwd()", "os.getcwd()"),
    ("from pathlib import Path\nPath.cwd()", "pathlib.Path.cwd()"),
    ("import pathlib\npathlib.Path.cwd()", "pathlib.Path.cwd()"),
    # directory listings, on a module, a name, or any expression
    ("import os\nos.listdir(d)", "os.listdir()"),
    ("import os\nos.scandir(d)", "os.scandir()"),
    ("import glob\nglob.glob('*.sql')", "glob.glob()"),
    ("import glob\nglob.iglob('*.sql')", "glob.iglob()"),
    ("root.iterdir()", ".iterdir()"),
    ("root.rglob('*.sql')", ".rglob()"),
    ("from pathlib import Path\nPath(d).iterdir()", ".iterdir()"),
    ("(root / 'x').glob('*.parquet')", ".glob()"),
    ("[p for p in Path(d).iterdir()]", ".iterdir()"),
    # randomness
    ("import random\nrandom.random()", "unseeded randomness"),
    ("import random\nrandom.Random()", "unseeded randomness"),
    ("import numpy as np\nnp.random.rand(3)", "unseeded randomness"),
    ("import numpy as np\nnp.random.default_rng()", "unseeded randomness"),
    ("frame.sample(n=5)", ".sample() without seed="),
    ("import polars as pl\npl.read_parquet(p).sample(n=5)", ".sample() without seed="),
    ("frame.shuffle()", ".shuffle() without seed="),
    # A seeded RNG stored under a subscript must not make every expression receiver look seeded.
    (
        "import random\nrngs[0] = random.Random(7)\nload().sample(n=3)",
        ".sample() without seed=",
    ),
    # model clients
    ("import anthropic", "model client import (anthropic)"),
    ("from openai import OpenAI", "model client import (openai)"),
]

CLEAN = [
    "import os\nsorted(os.listdir(d))",
    "import glob\nsorted(glob.iglob('*.sql'))",
    "from pathlib import Path\nsorted(Path(d).iterdir())",
    "sorted(p.name for p in (root / 'x').glob('*.parquet'))",
    "sorted([p for p in root.rglob('*.sql')])",
    "import polars as pl\npl.read_parquet(p).sample(n=5, seed=config.seed)",
    "import random\nrng = random.Random(seed)\nrng.sample(xs, 3)",
    "import random\nrandom.Random(seed).sample(xs, 3)",
    "import numpy as np\nrng = np.random.default_rng(seed)\nrng.shuffle(xs)",
    "import hashlib\nhashlib.sha256(b'x').hexdigest()",
    '"""Never call os.getcwd() or Path.cwd() here."""\n# Path(d).iterdir() is unsorted',
]


@pytest.mark.parametrize(("source", "expected"), FLAGGED)
def test_each_rule_flags_its_call(source: str, expected: str) -> None:
    findings = _findings(source)
    assert any(expected in f for f in findings), findings


@pytest.mark.parametrize("source", CLEAN)
def test_deterministic_code_passes(source: str) -> None:
    assert _findings(source) == []


def test_findings_are_ordered_by_line() -> None:
    findings = _findings("import os\nos.getcwd()\nos.getenv('X')\nroot.iterdir()")
    assert [f.split(":")[0] for f in findings] == ["line 2", "line 3", "line 4"]


@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        ("src/docgap/rank.py", True),
        ("src/docgap/io/export.py", True),
        ("src/docgap/llm/client.py", False),
        ("src/docgap/cli.py", False),
        ("src/docgap/test_rank.py", False),
        ("src/docgap/notes.md", False),
        ("tests/test_rank.py", False),
        ("eval/grade.py", False),
    ],
)
def test_scope_is_the_core_only(rel_path: str, expected: bool) -> None:
    assert in_scope(rel_path) is expected


def _run_guard(root: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (fixed argv: this interpreter and the guard script)
        [sys.executable, str(GUARD), *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=root,
        env={"CLAUDE_PROJECT_DIR": str(root)},
        check=False,
    )


@pytest.fixture
def core_file(tmp_path: Path) -> Path:
    path = tmp_path / "src" / "docgap" / "stage.py"
    path.parent.mkdir(parents=True)
    path.write_text("from pathlib import Path\nfiles = list(Path('.').iterdir())\n")
    return path


def test_hook_mode_exits_2_with_the_finding(tmp_path: Path, core_file: Path) -> None:
    payload = json.dumps({"tool_input": {"file_path": str(core_file)}})
    result = _run_guard(tmp_path, stdin=payload)
    assert result.returncode == 2
    assert "src/docgap/stage.py line 2: .iterdir()" in result.stderr


def test_file_mode_exits_1_for_pre_commit(tmp_path: Path, core_file: Path) -> None:
    result = _run_guard(tmp_path, str(core_file))
    assert result.returncode == 1


def test_files_outside_the_core_are_not_checked(tmp_path: Path) -> None:
    script = tmp_path / "tests" / "helper.py"
    script.parent.mkdir()
    script.write_text("import os\nos.getcwd()\n")
    assert _run_guard(tmp_path, str(script)).returncode == 0
