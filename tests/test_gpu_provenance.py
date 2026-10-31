"""Provenance must report physical process devices despite CUDA remapping."""
import os
from types import SimpleNamespace

import pytest
from manipulation_agent.omnigibson_backend import OmniGibsonBackend


@pytest.mark.parametrize('devices,expected', [
    ('GPU-physical-two', 'GPU-physical-two'),
    ('', None),
    ('GPU-physical-two,GPU-physical-three', None),
])
def test_provenance_uses_process_gpu_membership(monkeypatch, tmp_path, devices, expected):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '2')
    monkeypatch.delenv('OMNIGIBSON_GPU_ID', raising=False)
    monkeypatch.setenv('OMNIGIBSON_DATA_PATH', str(tmp_path))
    monkeypatch.setattr('manipulation_agent.omnigibson_backend.metadata.version', lambda p: 'fixture')
    def run(command, **kwargs):
        if '--query-compute-apps=pid,gpu_uuid' in command:
            rows=[f'{os.getpid()}, {uuid}' for uuid in devices.split(',') if uuid]
            rows.append('999999999, GPU-unrelated')
            return SimpleNamespace(returncode=0, stdout='\n'.join(rows))
        if '--query-gpu=uuid' in command:
            return SimpleNamespace(returncode=0, stdout='GPU-wrong-zero')
        return SimpleNamespace(returncode=0, stdout='source-fixture')
    monkeypatch.setattr('manipulation_agent.omnigibson_backend.subprocess.run', run)
    backend=SimpleNamespace(og=SimpleNamespace(__file__=str(tmp_path/'og.py')),
        mode='rgb',inside_placement='fixture',input_hashes={},task_metadata={})
    result=OmniGibsonBackend.provenance(backend)
    assert result['gpu_uuid']==expected
    assert result['gpu_uuids']==sorted(filter(None,devices.split(',')))
    assert result['gpu_identity_source']=='nvidia-smi process membership'
