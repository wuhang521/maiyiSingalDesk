"""Actual local tar/source checks; never execute host systemd or network."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile

import pytest

SPEC=importlib.util.spec_from_file_location('health_increment_verify',Path(__file__).with_name('verify-health-server-package.py'))
verify=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(verify)


def fixture(tmp_path, alteration=None):
    entries=[]
    for name in sorted(verify.EXPECTED):
        data=b'VALUE = True\n' if name.endswith('.py') else b'[Unit]\nDescription=Synthetic fixture\n'
        if alteration=='syntax' and name=='app/health_delivery.py':data=b'! invalid python\n'
        source=tmp_path/'server'/name;source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(data)
        entries.append((name,data))
    if alteration=='missing':entries.pop()
    if alteration=='duplicate':entries.append(entries[0])
    if alteration=='extra':entries.append(('app/unknown.py',b'VALUE=True\n'))
    if alteration=='link':entries[0]=(entries[0][0],None)
    archive=tmp_path/'synthetic-increment.tar.gz'
    with tarfile.open(archive,'w:gz',format=tarfile.USTAR_FORMAT) as output:
        for name,data in entries:
            member=tarfile.TarInfo(name)
            if data is None:
                member.type=tarfile.SYMTYPE;member.linkname='/synthetic-only';output.addfile(member)
            else:
                member.size=len(data);output.addfile(member,io.BytesIO(data))
    return archive,hashlib.sha256(archive.read_bytes()).hexdigest()


def test_exact_local_eleven_source_members_match_without_subprocess(tmp_path,monkeypatch):
    archive,checksum=fixture(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('Host subprocess is forbidden in local mode')
    monkeypatch.setattr(verify.subprocess,'run',forbidden)
    result=verify.verify(archive,checksum,tmp_path)
    assert result['passed'] and result['members']==result['local_source_members_matched']==11
    assert result['python_modules_compiled']==7 and not result['actual_systemd_unit_verification']
    assert result['messages_sent']==result['network_operations']==0


@pytest.mark.parametrize('alteration',['missing','duplicate','extra','link'])
def test_invalid_archive_members_are_rejected(tmp_path,alteration):
    archive,checksum=fixture(tmp_path,alteration)
    with pytest.raises(ValueError):verify.verify(archive,checksum,tmp_path)


def test_mutated_source_and_wrong_archive_sha_are_rejected(tmp_path):
    archive,checksum=fixture(tmp_path)
    with pytest.raises(ValueError,match='Archive SHA mismatch'):verify.verify(archive,'0'*64,tmp_path)
    (tmp_path/'server/app/health_delivery.py').write_bytes(b'CHANGED=True\n')
    with pytest.raises(ValueError,match='Archive/source bytes differ'):verify.verify(archive,checksum,tmp_path)


def test_invalid_python_is_rejected_before_any_host_action(tmp_path):
    archive,checksum=fixture(tmp_path,'syntax')
    with pytest.raises(SyntaxError):verify.verify(archive,checksum,tmp_path)


def test_host_mode_does_not_accept_arbitrary_local_path(tmp_path):
    archive,checksum=fixture(tmp_path)
    with pytest.raises(ValueError,match='Managed host archive path required'):verify.verify(archive,checksum)
