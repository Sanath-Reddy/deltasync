import random

import pytest

from deltasync.client.cli import main


@pytest.mark.parametrize("name", ["plain.bin", "with space.bin", "ünïcödé файл.bin"])
def test_sig_delta_patch(tmp_path, name):
    rng = random.Random(3)
    old = rng.randbytes(300_000)
    new = old[:1000] + rng.randbytes(500) + old[1000:250_000]
    (tmp_path / f"old {name}").write_bytes(old)
    (tmp_path / f"new {name}").write_bytes(new)

    def p(s):
        return str(tmp_path / s)

    assert main(["sig", p(f"old {name}"), p("old.sig")]) == 0
    assert main(["delta", p("old.sig"), p(f"new {name}"), p("patch.dlt")]) == 0
    assert main(["patch", p(f"old {name}"), p("patch.dlt"), p(f"out {name}")]) == 0
    assert (tmp_path / f"out {name}").read_bytes() == new
    assert (tmp_path / "patch.dlt").stat().st_size < 20_000


def test_patch_against_wrong_base_fails(tmp_path):
    (tmp_path / "old").write_bytes(b"a" * 10_000)
    (tmp_path / "other").write_bytes(b"z" * 10_000)
    (tmp_path / "new").write_bytes(b"a" * 9_000)
    main(["sig", str(tmp_path / "old"), str(tmp_path / "old.sig")])
    main(["delta", str(tmp_path / "old.sig"), str(tmp_path / "new"), str(tmp_path / "d")])
    code = main(["patch", str(tmp_path / "other"), str(tmp_path / "d"), str(tmp_path / "out")])
    assert code != 0
    assert not (tmp_path / "out").exists()
