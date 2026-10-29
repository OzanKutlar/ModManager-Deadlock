from pathlib import Path
from dlmm import units
from dlmm.archives import ExtractedVpk


def test_group_units_pairs_chunks_with_main(tmp_path):
    p_dir = tmp_path / 'pak01_dir.vpk'
    p_000 = tmp_path / 'pak01_000.vpk'
    p_001 = tmp_path / 'pak01_001.vpk'
    p_dir.write_bytes(b'\x34\x12\xaa\x55\x02\x00\x00\x00\x00\x00\x00\x00')  # VPK signature
    p_000.write_bytes(b'chunk0')
    p_001.write_bytes(b'chunk1')

    files = [
        ExtractedVpk('pak01_dir.vpk', p_dir, 12),
        ExtractedVpk('pak01_000.vpk', p_000, 6),
        ExtractedVpk('pak01_001.vpk', p_001, 6),
    ]
    grouped, orphans = units.group_units(files)
    assert len(grouped) == 1
    assert orphans == []
    assert grouped[0].main.relative == 'pak01_dir.vpk'
    assert [c[0] for c in grouped[0].chunks] == ['000', '001']


def test_chunk_slot():
    assert units.chunk_slot('pak03_dir.vpk', '000') == 'pak03_000.vpk'
    assert units.chunk_slot('pak99_dir.vpk', '012') == 'pak99_012.vpk'
